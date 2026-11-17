"""スタブコード生成

解析結果を元に、パッケージ（またはサブモジュール）の最小スタブコードを生成する。
ファイル書き出しは行わない — {パス: コード} の辞書を返す。

スタブの中身は元ファイルの AST から決める:
- クラス → isinstance / 型注釈 / 属性参照が通るクラス（呼ぶと RuntimeError）
- 関数 → 呼ぶと RuntimeError を出す関数
- 定数リテラル → 同じ値
- サブモジュール → 同じ構造のスタブファイル
- 不明 → 属性参照・isinstance が通る代替クラス
各スタブモジュールは PEP 562 の __getattr__ で、取りこぼした名前にも代替クラスを返す。
"""

from __future__ import annotations

import ast
import importlib.metadata
import keyword
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.models import ImportGraph
from mcp_pystub.module_resolver import EXTENSION_SUFFIXES, ModuleResolver, is_extension_file, _is_within
from mcp_pystub.semantics import Program, Ref, literal_source, under
from mcp_pystub.usage_analyzer import Semantics, get_semantics, has_c_extensions, import_closure, prefix_usage

_HEADER = """# mcp-pystub stub: {module}
class _M(type):
    __getattr__ = lambda c, n: _g(n)
    def __call__(c, *a, **k): raise RuntimeError(c.__name__ + " (stub)")
    __getitem__ = lambda c, i: c
    __iter__ = lambda c: iter(())
def _g(n):
    if n[:2] == "__": raise AttributeError(n)
    return _M(n, (), {{}})
__getattr__ = _g
"""


def _valid_dotted(name: str) -> bool:
    if not name:
        return False
    return all(p.isidentifier() and not keyword.iskeyword(p) for p in name.split("."))


def _q(path: Path | str) -> str:
    """シェル用に引用（パスは / 区切りに統一。Git Bash / POSIX シェル向け）。"""
    return shlex.quote(str(path).replace("\\", "/"))


# ---------------------------------------------------------------------------
# アクセス収集
# ---------------------------------------------------------------------------


@dataclass
class ModuleAccess:
    names: dict[str, set[tuple[str, ...]]] = field(default_factory=lambda: defaultdict(set))
    star: bool = False


def collect_accesses(sem: Semantics, prefix: str) -> dict[str, ModuleAccess]:
    """prefix の外の（読み込まれる）コードが prefix 配下のどのモジュールの何を参照するか。"""
    acc: dict[str, ModuleAccess] = defaultdict(ModuleAccess)
    loaded = sem.reach.loaded_modules
    for (module, _q_), unit in sem.program.units.items():
        if module not in loaded or under(module, prefix):
            continue
        for ev in unit.events:
            if ev.kind == "import":
                for m in ev.modules:
                    if under(m, prefix):
                        acc[m]  # noqa: B018 — 存在だけ登録
                if ev.from_base:
                    for n in ev.names:
                        if under(ev.from_base, prefix):
                            if n == "*":
                                acc[ev.from_base].star = True
                            else:
                                acc[ev.from_base].names[n].add(())
                        elif n != "*" and under(f"{ev.from_base}.{n}", prefix):
                            acc[f"{ev.from_base}.{n}"]  # noqa: B018
                continue
            ref = ev.ref
            if ref is None:
                continue
            entry = ref.entry_for(prefix)
            if entry is None:
                continue
            mod, chain = entry
            for i, c in enumerate(chain):
                if c in ("()", "[]"):
                    chain = chain[:i]  # 呼び出し結果・添字の先はスタブの対象外
                    break
            a = acc[mod]
            if chain:
                a.names[chain[0]].add(chain[1:])
    return dict(acc)


# ---------------------------------------------------------------------------
# スタブ本体の生成
# ---------------------------------------------------------------------------


class _StubBuilder:
    def __init__(self, sem: Semantics, prefix: str, version: str | None) -> None:
        self.sem = sem
        self.p: Program = sem.program
        self.prefix = prefix
        self.version = version
        self.modules: dict[str, ModuleAccess] = {}
        self.done: set[str] = set()

    def need(self, module: str, name: str | None = None, sub: tuple[str, ...] = ()) -> None:
        a = self.modules.setdefault(module, ModuleAccess())
        if name is not None:
            a.names[name].add(sub)

    def build(self, accesses: dict[str, ModuleAccess], extra_modules: list[str]) -> dict[str, str]:
        for m, a in accesses.items():
            ma = self.modules.setdefault(m, ModuleAccess())
            ma.star = ma.star or a.star
            for n, subs in a.names.items():
                ma.names[n].update(subs)
        for m in extra_modules:
            self.modules.setdefault(m, ModuleAccess())
        # 親パッケージ（prefix 以下）
        for m in list(self.modules):
            parts = m.split(".")
            for i in range(1, len(parts)):
                parent = ".".join(parts[:i])
                if under(parent, self.prefix):
                    self.modules.setdefault(parent, ModuleAccess())
        self.modules.setdefault(self.prefix, ModuleAccess())

        def signature() -> tuple:
            return tuple(sorted(
                (m, tuple(sorted((n, tuple(sorted(v))) for n, v in a.names.items())), a.star)
                for m, a in self.modules.items()
            ))

        code: dict[str, str] = {}
        for _ in range(8):
            before = signature()
            for m in list(self.modules):
                code[m] = self._module_code(m, self.modules[m])
            if signature() == before:
                break
        return code

    def _module_code(self, module: str, access: ModuleAccess) -> str:
        info = self.p.get(module)
        lines: list[str] = [_HEADER.format(module=module)]
        names: dict[str, set[tuple[str, ...]]] = {k: set(v) for k, v in access.names.items()}
        all_list: list[str] | None = None
        if access.star and info is not None and not info.opaque:
            all_list = info.all_names
            public = all_list if all_list is not None else self._public_names(module)
            for n in public:
                names.setdefault(n, set())
        if module == self.prefix.split(".")[0] and self.version:
            names.pop("__version__", None)
        emitted: list[str] = []
        for name in sorted(names):
            if not name.isidentifier() or keyword.iskeyword(name):
                continue
            if name.startswith("__") and name.endswith("__") and name not in ("__version__", "__all__"):
                continue
            emitted.append(self._emit_name(module, name, names[name]))
        if all_list is not None:
            emitted.append(f"__all__ = {all_list!r}\n")
        if module == self.prefix.split(".")[0] and self.version:
            emitted.append(f"__version__ = {self.version!r}\n")
        return "\n".join(lines + emitted)

    def _public_names(self, module: str) -> list[str]:
        info = self.p.get(module)
        if info is None or info.opaque:
            return []
        names = [n for n in info.bindings if not n.startswith("_")]
        for star in info.star_sources:
            si = self.p.get(star)
            if si is not None and not si.opaque:
                names.extend(si.all_names if si.all_names is not None else [
                    n for n in si.bindings if not n.startswith("_")
                ])
        return sorted(set(names))

    def _emit_name(self, module: str, name: str, subs: set[tuple[str, ...]]) -> str:
        r = self.p.resolve(module, (name,))
        if name == "__version__":
            lit = self._const_source(r)
            if lit is not None:
                return f"__version__ = {lit}\n"
            return f"__version__ = {(self.version or '0')!r}\n"
        if r.kind == "module":
            target = r.module
            if under(target, self.prefix):
                # サブモジュール（またはパッケージ内モジュールの別名）
                for s in subs:
                    if s:
                        self.need(target, s[0], s[1:])
                    else:
                        self.need(target)
                if target == f"{module}.{name}":
                    # `from . import x` はモジュールの __getattr__（代替クラスを返す）を先に
                    # 見てしまうため、サブモジュールは __import__ で明示的に読み込む
                    return (
                        f"try:\n    {name} = __import__({target!r}, None, None, ['_'])\n"
                        f"except ImportError:\n    {name} = _g({name!r})\n"
                    )
                return (
                    f"try:\n    import importlib as _il\n    {name} = _il.import_module({target!r})\n"
                    f"except ImportError:\n    {name} = _g({name!r})\n"
                )
            return f"{name} = _g({name!r})\n"
        if r.kind == "class":
            return self._class_code(name, r, subs)
        if r.kind in ("function", "method"):
            return f"def {name}(*a, **k): raise RuntimeError({(module + '.' + name)!r})\n"
        if r.kind == "const":
            lit = self._const_source(r)
            if lit is not None:
                return f"{name} = {lit}\n"
        return f"{name} = _g({name!r})\n"

    def _class_code(self, name: str, r: Ref, subs: set[tuple[str, ...]]) -> str:
        body: list[str] = []
        for s in sorted(subs):
            if not s:
                continue
            attr = s[0]
            if not attr.isidentifier() or keyword.iskeyword(attr) or (
                attr.startswith("__") and attr.endswith("__")
            ):
                continue
            ar = self.p.extend(r, (attr,))
            if ar.kind == "const":
                lit = self._const_source(ar)
                if lit is not None:
                    body.append(f"    {attr} = {lit}")
                    continue
            if ar.kind == "method":
                body.append(f"    @staticmethod\n    def {attr}(*a, **k): raise RuntimeError({(name + '.' + attr)!r})")
        uniq = list(dict.fromkeys(body))
        inner = "\n".join(uniq) if uniq else "    pass"
        return f"class {name}(metaclass=_M):\n{inner}\n"

    def _const_source(self, r: Ref) -> str | None:
        info = self.p.get(r.module)
        if info is None or info.opaque or not r.chain:
            return None
        b = info.bindings.get(r.chain[0])
        if len(r.chain) == 1:
            if b is not None and b.kind == "const":
                return literal_source(b.node)
            return None
        if b is not None and b.kind == "class":
            ci = info.classes.get(b.qualname)
            if ci is not None and len(r.chain) == 2:
                ab = ci.attrs.get(r.chain[1])
                if ab is not None and ab.kind == "const":
                    return literal_source(ab.node)
        return None


# ---------------------------------------------------------------------------
# パス
# ---------------------------------------------------------------------------


def _module_rel_path(module: str, program: Program, site_root: Path) -> str:
    parts = module.split(".")
    info = program.get(module)
    is_pkg = bool(info and info.is_package)
    if info is None:
        d = site_root.joinpath(*parts)
        is_pkg = d.is_dir()
    if is_pkg:
        return "/".join(parts) + "/__init__.py"
    return "/".join(parts) + ".py"


def _site_root_of(path: Path, resolver: ModuleResolver) -> Path | None:
    matches = [sp for sp in resolver.site_packages if _is_within(path, sp)]
    return max(matches, key=lambda p: len(str(p))) if matches else None


def _locate(name: str, resolver: ModuleResolver) -> tuple[Path | None, list[Path], Path | None]:
    """(パッケージの主パス, 移動すべき元ファイル群, site-packages ルート)。"""
    parts = name.split(".")
    for sp in resolver.site_packages:
        d = sp.joinpath(*parts)
        moves: list[Path] = []
        main: Path | None = None
        if d.is_dir():
            main = d
            moves.append(d)
        py = d.parent / (parts[-1] + ".py")
        if py.is_file():
            if main is None:
                main = py
            moves.append(py)
        if d.parent.is_dir():
            for suf in EXTENSION_SUFFIXES:
                ext = d.parent / (parts[-1] + suf)
                if ext.is_file():
                    if main is None:
                        main = ext
                    moves.append(ext)
        if main is not None:
            return main, list(dict.fromkeys(moves)), sp
    return None, [], None


def _find_package_root(package_name: str, resolver: ModuleResolver) -> Path | None:
    """パッケージの site-packages 内のルート（互換 API）。"""
    if not _valid_dotted(package_name):
        return None
    main, _, _ = _locate(package_name, resolver)
    return main


# ---------------------------------------------------------------------------
# 版数・配布物
# ---------------------------------------------------------------------------


def _distribution_for(package_name: str, site_root: Path | None) -> tuple[str | None, str | None]:
    """(pip 名, 版数) を site_root の dist-info から求める。"""
    if site_root is None:
        return None, None
    try:
        dists = list(importlib.metadata.distributions(path=[str(site_root)]))
    except Exception:  # noqa: BLE001
        return None, None
    top = package_name.split(".")[0]
    for dist in dists:
        try:
            tl = dist.read_text("top_level.txt") or ""
            tops = {line.strip() for line in tl.splitlines() if line.strip()}
            if not tops and dist.files:
                tops = {
                    str(f).replace("\\", "/").split("/")[0].split(".")[0]
                    for f in dist.files
                    if not str(f).startswith(("..", "/"))
                }
            if top in tops:
                return dist.metadata["Name"], dist.version
        except Exception:  # noqa: BLE001
            continue
    return None, None


def _ast_version(program: Program, package_name: str) -> str | None:
    r = program.resolve(package_name, ("__version__",))
    info = program.get(r.module)
    if r.kind == "const" and info is not None and r.chain:
        b = info.bindings.get(r.chain[0])
        if b is not None and b.node is not None:
            try:
                v = ast.literal_eval(b.node)
                if isinstance(v, str):
                    return v
            except Exception:  # noqa: BLE001
                pass
    return None


def _get_package_version(package_name: str, pkg_root: Path) -> str | None:  # 互換
    site_root = pkg_root.parent
    _, v = _distribution_for(package_name, site_root)
    return v


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------


def _prepare(entry_point: str, python_path: str, max_depth: int):
    entry = Path(entry_point).resolve()
    if not entry_point or not entry.is_file():
        return None, {"error": f"エントリーポイントが見つかりません: {entry_point}"}
    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(project_root=str(project_root), python_path=python_path)
    graph = build_import_graph(
        entry_point=str(entry), project_root=str(project_root), resolver=resolver, max_depth=max_depth,
    )
    return (entry, resolver, graph), None


def generate_stubs(
    entry_point: str,
    package_name: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """パッケージのスタブコードを生成する。"""
    if not _valid_dotted(package_name) or "." in package_name:
        return {"error": f"パッケージ名が不正です: {package_name!r}（トップレベルのパッケージ名を指定）"}
    prepared, err = _prepare(entry_point, python_path, max_depth)
    if err:
        return err
    entry, resolver, graph = prepared

    main, moves, site_root = _locate(package_name, resolver)
    if main is None or site_root is None:
        return {"error": f"パッケージ '{package_name}' が見つかりません"}
    if not _is_within(main, site_root) or main.resolve() == site_root.resolve():
        return {"error": f"パッケージ '{package_name}' の位置が site-packages の外です: {main}"}

    sem = get_semantics(graph)
    pip_name, version = _distribution_for(package_name, site_root)
    if version is None:
        version = _ast_version(sem.program, package_name)

    accesses = collect_accesses(sem, package_name)
    # 外から参照されないモジュール（P 内部からだけ import されるもの）は、
    # P 自体がスタブになれば読み込まれないので生成しない
    extra: list[str] = []
    builder = _StubBuilder(sem, package_name, version)
    if main.is_file() and not is_extension_file(main) and "." not in package_name:
        # 単一ファイルモジュール
        code_by_mod = builder.build(accesses, [])
        files = {f"{package_name}.py": code_by_mod.get(package_name, _HEADER.format(module=package_name))}
        for m, c in code_by_mod.items():
            if m != package_name:
                files[_module_rel_path(m, sem.program, site_root)] = c
    else:
        code_by_mod = builder.build(accesses, extra)
        files = {}
        for m, c in code_by_mod.items():
            rel = _module_rel_path(m, sem.program, site_root)
            if main.is_file() and m == package_name:
                rel = f"{package_name}.py"
            files[rel] = c
        # 親ディレクトリの __init__.py
        for rel in list(files):
            parts = rel.split("/")
            for i in range(1, len(parts) - 1):
                init = "/".join(parts[:i]) + "/__init__.py"
                if init not in files:
                    files[init] = _HEADER.format(module=".".join(parts[:i]))

    for rel, code in files.items():
        compile(code, rel, "exec")  # 生成物は必ず構文として正しい

    referenced = {
        m: sorted(a.names) + (["*"] if a.star else []) for m, a in sorted(accesses.items())
    }
    warnings = _usage_warnings(sem, package_name)
    if has_c_extensions(package_name, resolver):
        warnings.append(f"警告: {package_name} は C 拡張を含みます（スタブ化不可の判定）。")
    warnings.extend(graph.warnings[:20])
    stub_size = sum(len(code.encode("utf-8")) for code in files.values())
    original_files = _count_original_files_many(moves)
    build_instructions = _generate_build_instructions(
        package_name, moves, site_root, pip_name or package_name, version, graph,
    )
    return {
        "package": package_name,
        "files": files,
        "referenced_symbols": referenced,
        "original_file_count": original_files,
        "stub_file_count": len(files),
        "stub_total_bytes": stub_size,
        "build_instructions": build_instructions,
        "warnings": warnings,
    }


def _usage_warnings(sem: Semantics, prefix: str) -> list[str]:
    usage = prefix_usage(sem, prefix)
    if not usage.uses:
        return []
    unit, ev, sym = usage.uses[0]
    where = unit.module + ("" if unit.qualname == "<module>" else f":{unit.qualname}")
    return [
        f"警告: {prefix} は到達可能なコードで実際に使用されています（{where} 行 {ev.lineno}: {sym}、"
        f"使用箇所 {len(usage.uses)} 件）。このスタブに差し替えると実行時に失敗します。"
    ]


def _detect_project_root(entry: Path) -> Path:
    """プロジェクトルートを推定する。"""
    current = entry.parent
    for _ in range(20):
        if (current / "pyproject.toml").exists():
            return current
        if (current / "setup.py").exists():
            return current
        if (current / "setup.cfg").exists():
            return current
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return entry.parent


def _count_original_files_many(paths: list[Path]) -> int:
    count = 0
    for p in paths:
        count += _count_original_files(p)
    return count


def _count_original_files(pkg_root: Path) -> int:
    """元パッケージのファイル数を数える。"""
    if pkg_root.is_file():
        return 1
    count = 0
    try:
        for f in pkg_root.rglob("*"):
            if f.is_file():
                count += 1
    except OSError:
        pass
    return count


def _get_pip_name(package_name: str) -> str:  # 互換
    return package_name


def _generate_build_instructions(
    name: str,
    moves: list[Path],
    site_root: Path,
    pip_name: str,
    version: str | None,
    graph: ImportGraph | None = None,
    stub_root_hint: str = "_stubs",
    verify_imports: list[str] | None = None,
) -> dict:
    """スタブ適用・復元のためのビルド手順を生成する。

    - 解析で実際に見つけた絶対パスを引用符つきで埋め込む（site.getsitepackages() は使わない）
    - pip uninstall はしない（dist-info を残す → 復元後もメタデータが元のまま）
    - 元ファイルは site-packages の外のバックアップ領域へ移動（ビルドに混入しない）
    """
    site_root = site_root.resolve()
    backup_root = (site_root.parent / "_pystub_bak").resolve()
    rel_moves = []
    for m in moves:
        mp = m.resolve()
        if not _is_within(mp, site_root) or mp == site_root:
            raise ValueError(f"site-packages の外は扱いません: {mp}")
        rel_moves.append(mp.relative_to(site_root))

    backup_cmds = ["# === 元ファイルをバックアップ領域へ移動（必ず最初に実行） ==="]
    backup_cmds.append(f"mkdir -p {_q(backup_root)}")
    for rel in rel_moves:
        src = site_root / rel
        dst = backup_root / rel
        backup_cmds.append(f"mkdir -p {_q(dst.parent)}")
        backup_cmds.append(f"mv {_q(src)} {_q(dst)}  # .bak 相当")

    stub_top = name.replace(".", "/")
    main_rel = rel_moves[0] if rel_moves else Path(stub_top)
    install_cmds = ["# スタブを site-packages にコピー（生成された files を _stubs/ に書き出してから）:"]
    if (site_root / main_rel).suffix == ".py" or str(main_rel).endswith(".py"):
        install_cmds.append(f"cp {_q(Path(stub_root_hint) / main_rel)} {_q(site_root / main_rel)}")
    else:
        install_cmds.append(f"cp -r {_q(Path(stub_root_hint) / stub_top)} {_q(site_root / Path(stub_top))}")

    uninstall_cmds = ["# === 方法1: バックアップから復元（推奨・高速・確実） ==="]
    stub_target = site_root / (main_rel if str(main_rel).endswith(".py") else Path(stub_top))
    uninstall_cmds.append(f"rm -rf {_q(stub_target)}")
    for rel in rel_moves:
        uninstall_cmds.append(f"mv {_q(backup_root / rel)} {_q(site_root / rel)}")
    uninstall_cmds.append("")
    uninstall_cmds.append("# === 方法2: pip で再インストール（バックアップ消失時のフォールバック。対象環境の python で実行） ===")
    spec = f"{pip_name}=={version}" if version else pip_name
    uninstall_cmds.append(f"python -m pip install --force-reinstall --no-deps {_q(spec)}")

    imports = verify_imports or [name]
    verify_cmds = [f'python -c "import {m}; print(\'{m} OK\')"' for m in imports]
    if version and "." not in name:
        verify_cmds.append(
            f'python -c "import importlib.metadata as m; v = m.version(\'{pip_name}\'); '
            f'assert v == \'{version}\', v; print(\'version OK\', v)"'
        )

    hook_patterns: list[str] = []
    top = name.split(".")[0]
    pyinstaller_hooks = site_root / "PyInstaller" / "hooks"
    if pyinstaller_hooks.is_dir():
        hook_patterns.extend(h.name for h in pyinstaller_hooks.glob(f"hook-{top}*.py"))
    contrib = site_root / "_pyinstaller_hooks_contrib"
    if contrib.is_dir():
        hook_patterns.extend(str(h.relative_to(site_root)) for h in contrib.rglob(f"hook-{top}*.py"))
    hook_cmds = (
        [f"# リネームして無効化: {h} → {h}.disabled" for h in hook_patterns]
        if hook_patterns else [f"# hook-{top}*.py があれば .disabled にリネーム"]
    )

    notes = [
        "必ず backup_commands を最初に実行すること（元ファイルを site-packages の外へ移動）",
        "pip uninstall は不要（dist-info を残すので復元後もメタデータは元のまま）",
        "ビルド後は uninstall_commands → verify_commands の順で復元・検証",
        "コマンドは POSIX シェル（Git Bash 等）向け。パスは解析で見つけた実際の場所",
        "PyInstaller フックがスタブと衝突する場合は hook_disable でフック無効化",
    ]
    return {
        "site_packages": str(site_root),
        "backup_dir": str(backup_root),
        "backup_commands": backup_cmds,
        "install_commands": install_cmds,
        "uninstall_commands": uninstall_cmds,
        "verify_commands": verify_cmds,
        "hook_disable": hook_cmds,
        "notes": notes,
    }


def generate_submodule_stubs(
    entry_point: str,
    parent_package: str,
    submodule: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """サブモジュール単位のスタブを生成する。

    C拡張パッケージ（PySide6等）を直接スタブ化できない場合に、
    そのパッケージを import しているサブモジュール（asammdf.gui等）を
    スタブ化することで間接的に排除する。
    """
    if not _valid_dotted(parent_package) or "." in parent_package:
        return {"error": f"親パッケージ名が不正です: {parent_package!r}"}
    if not _valid_dotted(submodule):
        return {"error": f"サブモジュール名が不正です: {submodule!r}"}
    submod_parts = submodule.split(".")
    if len(submod_parts) < 2 or submod_parts[0] != parent_package:
        return {"error": f"サブモジュール '{submodule}' は '{parent_package}' に属していません"}

    prepared, err = _prepare(entry_point, python_path, max_depth)
    if err:
        return err
    entry, resolver, graph = prepared

    pkg_main, _, site_root = _locate(parent_package, resolver)
    if pkg_main is None or site_root is None:
        return {"error": f"パッケージ '{parent_package}' が見つかりません"}
    if pkg_main.is_file():
        return {"error": f"'{parent_package}' は単一ファイルのモジュールでサブモジュールを持ちません: {pkg_main}"}

    sub_main, moves, _ = _locate(submodule, resolver)
    if sub_main is None:
        return {"error": f"サブモジュール '{submodule}' のファイルが見つかりません"}

    sem = get_semantics(graph)
    accesses = collect_accesses(sem, submodule)
    extra: list[str] = []
    builder = _StubBuilder(sem, submodule, None)
    code_by_mod = builder.build(accesses, extra)
    files: dict[str, str] = {}
    for m, c in code_by_mod.items():
        if not under(m, submodule):
            continue
        rel = _module_rel_path(m, sem.program, site_root)
        if sub_main.is_file() and m == submodule:
            rel = "/".join(submod_parts) + ".py"
        files[rel] = c
    for rel, code in files.items():
        compile(code, rel, "exec")

    # 排除されるパッケージ: 全体の import 閉包にあり、サブモジュールを除くと消えるもの
    full = import_closure(sem)
    without = import_closure(sem, exclude_prefix=submodule)
    full_tops = {m.split(".")[0] for m in full}
    without_tops = {m.split(".")[0] for m in without}
    eliminated = sorted(
        t for t in full_tops - without_tops
        if t != parent_package and (graph.nodes.get(t) is None or graph.nodes[t].classification != "local")
    )

    symbols_to_export: list[str] = []
    a = accesses.get(submodule)
    if a is not None:
        symbols_to_export = sorted(a.names)

    original_size = 0
    for mv in moves:
        if mv.is_dir():
            for f in mv.rglob("*"):
                if f.is_file():
                    try:
                        original_size += f.stat().st_size
                    except OSError:
                        pass
        elif mv.is_file():
            try:
                original_size += mv.stat().st_size
            except OSError:
                pass
    stub_size = sum(len(code.encode("utf-8")) for code in files.values())

    pip_name, version = _distribution_for(parent_package, site_root)
    build_instructions = _generate_build_instructions(
        submodule, moves, site_root, pip_name or parent_package, version, graph,
        verify_imports=[parent_package, submodule],
    )
    build_instructions["notes"].append(f"親パッケージ ({parent_package}) の他のファイルは変更しない")

    return {
        "parent_package": parent_package,
        "submodule": submodule,
        "files": files,
        "eliminated_packages": eliminated,
        "original_size_bytes": original_size,
        "stub_size_bytes": stub_size,
        "symbols_exported": symbols_to_export,
        "build_instructions": build_instructions,
        "warnings": _usage_warnings(sem, submodule),
    }
