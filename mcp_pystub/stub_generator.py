"""スタブコード生成

解析結果を元に、パッケージ（またはサブモジュール）の最小スタブコードを生成する。
ファイル書き出しは行わない — {パス: コード} の辞書を返す。

スタブの中身は元ファイルの AST から決める:
- クラス → isinstance / 型注釈 / 属性参照が通るクラス（呼ぶと RuntimeError）
- 関数 → 呼ぶと RuntimeError を出す関数
- 定数リテラル → 同じ値
- サブモジュール → 同じ構造のスタブファイル
- 不明 → 属性参照・isinstance が通る代替クラス
各スタブモジュールの PEP 562 __getattr__ は、元のモジュールにある名前だけ代替クラスを返し、
元に無い名前は AttributeError にする（hasattr / getattr の既定値を元と同じにする: 仕様 3 節）。
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
import sys as _sys
_STDLIB_NAMES = frozenset(getattr(_sys, 'stdlib_module_names', ()))
from mcp_pystub.semantics import ClassInfo, ModuleInfo, Program, Ref, _unwrap_base, literal_source, stdlib_object, under
from mcp_pystub.usage_analyzer import Semantics, get_semantics, graph_closure, has_c_extensions, import_closure, prefix_usage

_HEADER = """# mcp-pystub stub: {module}
class _M(type):
    def __getattr__(c, n):
        k = c.__dict__.get("_pystub_known")
        if n[:2] != "__" and (k is None or n in k):
            return _M(n, (), {{"_pystub_known": ()}})
        raise AttributeError(n)
    def __call__(c, *a, **k): raise RuntimeError(c.__name__ + " (stub)")
    __getitem__ = lambda c, i: c
def _g(n):
    return _M(n, (), {{"_pystub_known": ()}})
"""

# 条件付き候補のスタブ: 呼ばれたら「mcp-pystub のスタブ」と分かる例外で落ちる
_LOUD_HEADER = """# mcp-pystub stub (conditional): {module}
class PystubConditionalStubError(RuntimeError):
    pass
_PYSTUB_NOTE = {note!r}
def _raise(n):
    raise PystubConditionalStubError("mcp-pystub のスタブです。" + _PYSTUB_NOTE + "（呼ばれたもの: " + n + "）")
class _M(type):
    def __getattr__(c, n):
        k = c.__dict__.get("_pystub_known")
        if n[:2] != "__" and (k is None or n in k):
            return _M(n, (), {{"_pystub_known": ()}})
        raise AttributeError(n)
    def __call__(c, *a, **k): _raise(c.__name__)
    __getitem__ = lambda c, i: c
def _g(n):
    return _M(n, (), {{"_pystub_known": ()}})
"""

_NET_KNOWN = """_PYSTUB_KNOWN = {known}
def __getattr__(n):
    if n in _PYSTUB_KNOWN and n[:2] != "__":
        return _g(n)
    raise AttributeError(n)
"""

_NET_ALL = """def __getattr__(n):
    if n[:2] == "__":
        raise AttributeError(n)
    return _g(n)
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


def _cut_chain(chain: tuple[str, ...]) -> tuple[str, ...]:
    for i, c in enumerate(chain):
        if c in ("()", "[]"):
            return chain[:i]  # 呼び出し結果・添字の先はスタブの対象外
    return chain


def collect_accesses(sem: Semantics, prefix: str, exclude: tuple[str, ...] = ()) -> dict[str, ModuleAccess]:
    """prefix の外のコードが prefix 配下のどのモジュールの何を参照するか。

    - 到達可能なコード単位のイベント（参照・import）
    - 静的な import グラフ上の、外から prefix 配下への import（到達しない関数内も含む）
    """
    acc: dict[str, ModuleAccess] = defaultdict(ModuleAccess)
    skip = (prefix,) + tuple(exclude)
    for key in sem.reach.reached:
        unit = sem.program.units.get(key)
        if unit is None or any(under(unit.module, x) for x in skip):
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
            for rr in ref.all_refs():
                entry = rr.entry_for(prefix)
                if entry is None:
                    continue
                mod, chain = entry
                chain = _cut_chain(chain)
                a = acc[mod]
                if chain:
                    a.names[chain[0]].add(chain[1:])
    graph = sem.graph
    if graph is not None:
        for e in graph.edges:
            if any(under(e.from_module, x) for x in skip) or not under(e.to_module, prefix):
                continue
            acc[e.to_module]  # noqa: B018
            info = e.import_info
            if info.import_type in ("from_import", "star_import"):
                base = e.to_module
                for n in info.names_imported:
                    if n == "*":
                        acc[base].star = True
                    elif n:
                        acc[base].names[n].add(())
    return dict(acc)


# ---------------------------------------------------------------------------
# スタブ本体の生成（元の AST から忠実に）
# ---------------------------------------------------------------------------


class _StubBuilder:
    def __init__(self, sem: Semantics, prefix: str, version: str | None, loud_note: str = "") -> None:
        self.sem = sem
        self.p: Program = sem.program
        self.prefix = prefix
        self.version = version
        self.loud_note = loud_note  # 条件付き候補: 呼ばれたら出す説明
        self.modules: dict[str, ModuleAccess] = {}

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

        def add_parents() -> None:
            for m in list(self.modules):
                parts = m.split(".")
                for i in range(1, len(parts)):
                    parent = ".".join(parts[:i])
                    if under(parent, self.prefix):
                        self.modules.setdefault(parent, ModuleAccess())

        add_parents()
        self.modules.setdefault(self.prefix, ModuleAccess())

        def signature() -> tuple:
            return tuple(sorted(
                (m, tuple(sorted((n, tuple(sorted(v))) for n, v in a.names.items())), a.star)
                for m, a in self.modules.items()
            ))

        code: dict[str, str] = {}
        for _ in range(10):
            before = signature()
            for m in list(self.modules):
                code[m] = self._module_code(m, self.modules[m])
            add_parents()
            if signature() == before:
                break
        return code

    # --- モジュール ---

    def _known_names(self, info: ModuleInfo | None) -> set[str] | None:
        if info is None or info.opaque:
            return None
        if info.has_getattr:
            return None  # 元が __getattr__ を持つ: どの名前があるか静的に分からない
        names = set(info.bindings)
        for star in info.star_sources:
            si = self.p.get(star)
            if si is None or si.opaque or si.has_getattr:
                return None
            names |= set(si.bindings)
        names.add("__version__") if "__version__" in info.bindings else None
        return names

    def _module_code(self, module: str, access: ModuleAccess) -> str:
        info = self.p.get(module)
        names: dict[str, set[tuple[str, ...]]] = {k: set(v) for k, v in access.names.items()}
        all_list: list[str] | None = None
        if info is not None and not info.opaque:
            if access.star:
                all_list = info.all_names
                public = all_list if all_list is not None else self._public_names(module)
                for n in public:
                    names.setdefault(n, set())
            elif info.all_names is not None:
                all_list = info.all_names
        is_top = module == self.prefix.split(".")[0]
        if is_top and self.version:
            names.pop("__version__", None)
        defs: list[str] = []
        tail: list[str] = []
        self._emitted: set[str] = set()
        self._defs = defs
        self._module = module
        for name in sorted(names):
            if not name.isidentifier() or keyword.iskeyword(name):
                continue
            if name.startswith("__") and name.endswith("__") and name not in ("__version__", "__all__"):
                continue
            self._emit_name(module, info, name, names[name], defs, tail)
        if all_list is not None:
            for n in all_list:
                if n not in self._emitted and n.isidentifier() and not keyword.iskeyword(n) \
                        and not (n.startswith("__") and n.endswith("__")):
                    self._emit_name(module, info, n, set(), defs, tail)
            defs.append(f"__all__ = {list(all_list)!r}\n")
        if is_top and self.version:
            defs.append(f"__version__ = {self.version!r}\n")
        known = self._known_names(info)
        net = _NET_ALL if known is None else _NET_KNOWN.format(known=repr(frozenset(sorted(known))))
        header = _LOUD_HEADER.format(module=module, note=self.loud_note) if self.loud_note else \
            _HEADER.format(module=module)
        return "\n".join([header, net] + defs + tail)

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

    def _emit_name(self, module: str, info: ModuleInfo | None, name: str, subs: set[tuple[str, ...]],
                   defs: list[str], tail: list[str]) -> None:
        if name in self._emitted:
            return
        self._emitted.add(name)
        r = self.p.resolve(module, (name,))
        if r.kind == "missing" and not r.alts:
            return  # 元のモジュールに無い名前は出さない（hasattr / getattr の既定値を元と同じに: S13）
        if name == "__all__" and (info is None or info.all_names is None):
            return  # 定数でない __all__ は出さない（スターは出した公開名だけになる）
        if name == "__version__":
            lit = self._const_source(r)
            defs.append(f"__version__ = {lit if lit is not None else repr(self.version or '0')}\n")
            return
        leaves = [x for x in r.all_refs() if not x.alts]
        if r.alts:
            kinds = {x.kind for x in leaves}
            if kinds == {"class"} and len({(x.module, x.target) for x in leaves}) == 1:
                r = leaves[0]
            elif kinds == {"function"}:
                defs.append(self._func_stub(name, module + "." + name))
                return
            else:
                defs.append(f"{name} = _g({name!r})\n")
                return
        kind = r.kind
        if kind in ("module", "external_module"):
            target = r.module
            if kind == "module" and under(target, self.prefix):
                for s in subs:
                    if s:
                        self.need(target, s[0], s[1:])
                    else:
                        self.need(target)
                tail.append(
                    f"try:\n    {name} = __import__({target!r}, None, None, ['_'])\n"
                    f"except ImportError:\n    {name} = _g({name!r})\n"
                )
            else:
                tail.append(
                    f"try:\n    {name} = __import__({target!r}, None, None, ['_'])\n"
                    f"except ImportError:\n    {name} = _g({name!r})\n"
                )
            return
        if kind == "external":
            # stdlib / 組み込みの再エクスポートは同じものを import する（同一性を保つ: S14）
            mod, chain = r.module, r.chain
            if mod == "builtins" and len(chain) == 1:
                defs.append(f"{name} = {chain[0]}\n")
            else:
                expr = ".".join(chain)
                defs.append(
                    f"try:\n    import importlib as _il\n    {name} = _il.import_module({mod!r})"
                    + "".join(f".{c}" for c in chain) + "\n"
                    f"except (ImportError, AttributeError):\n    {name} = _g({name!r})  # {mod}.{expr}\n"
                )
            return
        if not under(r.module, self.prefix) and kind in ("class", "function", "const", "opaque", "classconst"):
            # 別パッケージのオブジェクトの再エクスポート: 元と同じものを import する
            if r.chain:
                path = r.module
                tail.append(
                    f"try:\n    import importlib as _il\n    {name} = _il.import_module({path!r})"
                    + "".join(f".{c}" for c in r.chain) + "\n"
                    f"except (ImportError, AttributeError):\n    {name} = _g({name!r})\n"
                )
                return
        if kind in ("class", "function", "const") and r.chain and len(r.chain) == 1 and r.module != module \
                and under(r.module, self.prefix):
            # 定義元のモジュールで定義し、ここではそれを import する（同一性を保つ: F04）
            for s in subs or {()}:
                self.need(r.module, r.chain[0], s)
            tail.append(
                f"try:\n    from {r.module} import {r.chain[0]} as {name}\n"
                f"except ImportError:\n    {name} = _g({name!r})\n"
            )
            return
        if kind == "class":
            defs.append(self._class_code(name, r, subs))
            return
        if kind in ("function", "method"):
            defs.append(self._func_stub(name, module + "." + name))
            return
        if kind in ("const", "classconst"):
            lit = self._const_source(r)
            if lit is not None:
                defs.append(f"{name} = {lit}\n")
                return
        defs.append(f"{name} = _g({name!r})\n")

    def _func_stub(self, name: str, qual: str) -> str:
        if self.loud_note:
            return f"def {name}(*a, **k): _raise({qual!r})\n"
        return f"def {name}(*a, **k): raise RuntimeError({qual!r})\n"

    # --- クラス ---

    def _exc_bases(self, info: ModuleInfo, ci: ClassInfo, depth: int = 0) -> list[str]:
        """元のクラスの基底を辿って、組み込み・stdlib の例外の祖先を式として返す（S1）。"""
        out: list[str] = []
        if depth > 30:
            return out
        for b in ci.bases:
            rb = self.p.ref_of_expr(info, _unwrap_base(b), None)
            if rb is None or rb.alts:
                continue
            if rb.kind == "external" and rb.chain:
                try:
                    obj = stdlib_object(rb.module, rb.chain)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(obj, type) and issubclass(obj, BaseException):
                    expr = rb.chain[0] if rb.module == "builtins" and len(rb.chain) == 1 else (
                        f"__import__({rb.module!r}, None, None, ['_'])" + "".join(f".{c}" for c in rb.chain)
                    )
                    if expr not in out:
                        out.append(expr)
            elif rb.kind == "class":
                binfo = self.p.get(rb.module)
                bci = binfo.classes.get(rb.target) if binfo is not None else None
                if binfo is not None and bci is not None:
                    for x in self._exc_bases(binfo, bci, depth + 1):
                        if x not in out:
                            out.append(x)
        return out

    def _class_known(self, info: ModuleInfo, ci: ClassInfo, depth: int = 0) -> set[str]:
        names = set(ci.attrs) | set(ci.methods)
        if depth > 30:
            return names
        for b in ci.bases:
            rb = self.p.ref_of_expr(info, _unwrap_base(b), None)
            if rb is not None and rb.kind == "class" and not rb.alts:
                binfo = self.p.get(rb.module)
                bci = binfo.classes.get(rb.target) if binfo is not None else None
                if binfo is not None and bci is not None:
                    names |= self._class_known(binfo, bci, depth + 1)
        return names

    def _class_code(self, name: str, r: Ref, subs: set[tuple[str, ...]]) -> str:
        info = self.p.get(r.module)
        ci = info.classes.get(r.target) if info is not None else None
        bases: list[str] = []
        known: set[str] | None = set()
        if info is not None and ci is not None:
            bases = self._exc_bases(info, ci)
            known = self._class_known(info, ci)
            if not self.p.class_is_plain(r.module, r.target):
                known = None  # Enum 等: 属性の有無を静的に決めない
        body: list[str] = [f"    _pystub_known = {repr(frozenset(sorted(known))) if known is not None else 'None'}"]
        for s in sorted(subs):
            if not s:
                continue
            attr = s[0]
            if not attr.isidentifier() or keyword.iskeyword(attr) or (attr.startswith("__") and attr.endswith("__")):
                continue
            ar = self.p.extend(r, (attr,))
            if ar.kind == "classconst":
                lit = self._const_source(ar)
                if lit is not None:
                    body.append(f"    {attr} = {lit}")
                    continue
            if ar.kind in ("method", "function"):
                if self.loud_note:
                    body.append(f"    @staticmethod\n    def {attr}(*a, **k): _raise({(name + '.' + attr)!r})")
                else:
                    body.append(f"    @staticmethod\n    def {attr}(*a, **k): raise RuntimeError({(name + '.' + attr)!r})")
        inner = "\n".join(dict.fromkeys(body))
        base_s = ", ".join(bases + ["metaclass=_M"])
        return f"class {name}({base_s}):\n{inner}\n"

    def _const_source(self, r: Ref) -> str | None:
        info = self.p.get(r.module)
        if info is None or info.opaque or not r.chain:
            return None
        blist = info.bindings.get(r.chain[0], [])
        if len(r.chain) == 1:
            if len(blist) == 1 and blist[0].kind == "literal" and self.p.binding_is_const(info, r.chain[0], blist):
                return literal_source(blist[0].node)
            return None
        if r.kind == "classconst" and r.target:
            ci = info.classes.get(r.target)
            if ci is not None:
                found = self.p._class_attr_lookup(info, ci, r.chain[-1])
                if found is not None and found[2] == "attr" and len(found[3]) == 1:
                    return literal_source(found[3][0].node)
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


def _distributions_for(name: str, site_root: Path | None) -> list[tuple[str, str]]:
    """name（トップレベル名、またはサブモジュール名）のファイルを持つ全配布物の (pip 名, 版数)。

    名前空間を共有するパッケージ（google, backports 等）は複数の配布物が同じトップレベルを持つ。
    RECORD（dist.files）でファイルの持ち主を確かめる。
    """
    if site_root is None:
        return []
    try:
        dists = list(importlib.metadata.distributions(path=[str(site_root)]))
    except Exception:  # noqa: BLE001
        return []
    parts = name.split(".")
    prefix_dir = "/".join(parts) + "/"
    prefix_file = "/".join(parts) + "."
    out: list[tuple[str, str]] = []
    for dist in dists:
        try:
            owned = False
            if dist.files:
                for f in dist.files:
                    s = str(f).replace("\\", "/")
                    if s.startswith(prefix_dir) or s.startswith(prefix_file):
                        owned = True
                        break
            else:
                tl = dist.read_text("top_level.txt") or ""
                owned = parts[0] in {line.strip() for line in tl.splitlines() if line.strip()}
            if owned:
                pair = (dist.metadata["Name"], dist.version)
                if pair not in out:
                    out.append(pair)
        except Exception:  # noqa: BLE001
            continue
    return out


def _distribution_for(package_name: str, site_root: Path | None) -> tuple[str | None, str | None]:
    """(pip 名, 版数)。配布物が 1 つに決まらなければ (None, None)。"""
    d = _distributions_for(package_name, site_root)
    if len(d) == 1:
        return d[0]
    return None, None


def _ast_version(program: Program, package_name: str) -> str | None:
    r = program.resolve(package_name, ("__version__",))
    info = program.get(r.module)
    if r.kind == "const" and info is not None and r.chain:
        blist = info.bindings.get(r.chain[0]) or []
        if len(blist) == 1 and blist[0].node is not None:
            try:
                v = ast.literal_eval(blist[0].node)
                if isinstance(v, str):
                    return v
            except Exception:  # noqa: BLE001
                pass
    return None


def _true_site_root(path: Path, given: Path) -> Path | None:
    """path を含む本当の site-packages（.pth で sys.path に足されたサブディレクトリではない）。"""
    cur = path.resolve()
    for _ in range(40):
        if cur.name.lower() in ("site-packages", "dist-packages"):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return given.resolve()


def _validate_target(name: str, main: Path, site_root: Path, entry: Path, resolver: ModuleResolver) -> str | None:
    """手順を出してよい対象か（仕様 7 節: site-packages の外・site-packages 自体は扱わない）。"""
    top = name.split(".")[0]
    if top in _STDLIB_NAMES:
        return f"'{top}' は標準ライブラリです"
    if not _is_within(main, site_root) or main.resolve() == site_root.resolve():
        return f"'{name}' の位置が site-packages の外です: {main}"
    if _is_within(entry, site_root) or _is_within(site_root, entry.parent):
        return f"python_path（{site_root}）がプロジェクトと重なっています"
    for d in resolver._stdlib_dirs:
        if (_is_within(d, site_root) or _is_within(site_root, d)) and site_root.name.lower() not in (
            "site-packages", "dist-packages"
        ):
            return f"python_path（{site_root}）が標準ライブラリの場所と重なっています"
    for root in resolver._local_roots:
        if _is_within(main, root) and not _is_within(root, site_root):
            return f"'{name}' はプロジェクト内のモジュールです: {main}"
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

    main, moves, given_root = _locate(package_name, resolver)
    if main is None or given_root is None:
        return {"error": f"パッケージ '{package_name}' が見つかりません"}
    site_root = _true_site_root(main, given_root)
    if site_root is None:
        return {"error": f"'{package_name}' の場所 {main} は site-packages の中ではありません"}
    bad = _validate_target(package_name, main, site_root, entry, resolver)
    if bad:
        return {"error": bad}

    sem = get_semantics(graph)
    dists = _distributions_for(package_name, site_root)
    version = dists[0][1] if len(dists) == 1 else None
    if version is None:
        version = _ast_version(sem.program, package_name)

    accesses = collect_accesses(sem, package_name)
    # 外から参照されないモジュール（P 内部からだけ import されるもの）は、
    # P 自体がスタブになれば読み込まれないので生成しない
    extra: list[str] = []
    note, conditional_info = _conditional_note(graph, sem, package_name, is_package=True)
    builder = _StubBuilder(sem, package_name, version, loud_note=note)
    if main.is_file() and not is_extension_file(main) and "." not in package_name:
        # 単一ファイルモジュール
        code_by_mod = builder.build(accesses, [])
        files = {f"{package_name}.py": code_by_mod.get(package_name, _HEADER.format(module=package_name))}
        for m, c in code_by_mod.items():
            if m != package_name:
                files[_module_rel_path(m, sem.program, given_root)] = c
    else:
        code_by_mod = builder.build(accesses, extra)
        files = {}
        for m, c in code_by_mod.items():
            rel = _module_rel_path(m, sem.program, given_root)
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
        package_name, moves, site_root, dists if dists else package_name, version, graph,
        import_root=given_root,
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
        "conditional": bool(conditional_info),
        "conditions": (conditional_info or {}).get("conditions", []),
    }


def _conditional_note(graph, sem: Semantics, prefix: str, is_package: bool) -> tuple[str, dict | None]:
    """条件付き候補なら、スタブが落ちる時に出す説明（「<prefix> は <条件> を使わない前提」）。"""
    from mcp_pystub.usage_analyzer import analyze_packages, build_conditions, is_certain_use

    info = None
    if is_package:
        a = analyze_packages(graph, {prefix})
        if a and a[0].verdict == "conditional":
            info = {"conditions": a[0].conditions, "total": a[0].conditions_total}
    else:
        uses = prefix_usage(sem, prefix).uses
        if uses and not any(is_certain_use(sem, u, ev) for u, ev, _ in uses):
            info = build_conditions(sem, uses, prefix)
    if not info:
        return "", None
    ents = []
    for g in info["conditions"][:3]:
        api = g.get("public_api") or ""
        ents.append((api + " → " if api and api != g["entry"] else "") + g["entry"])
    more = f" ほか {info['total'] - 3} 件" if info["total"] > 3 else ""
    note = f"{prefix} は、次の機能を使わない前提で外されています: " + "、".join(ents) + more
    return note, info


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
    pip_name: str | list[tuple[str, str]],
    version: str | None,
    graph: ImportGraph | None = None,
    stub_root_hint: str = "_stubs",
    verify_imports: list[str] | None = None,
    import_root: Path | None = None,
) -> dict:
    """スタブ適用・復元のためのビルド手順を生成する（仕様 7 節）。

    - 解析で実際に見つけた絶対パスを引用符つきで埋め込む（site.getsitepackages() は使わない）
    - pip uninstall はしない（dist-info を残す → 復元後もメタデータが元のまま）
    - 元ファイルは site-packages の外のバックアップ領域へ移動（ビルドに混入しない）
    - 繰り返し適用しても同じ結果（バックアップが既にあれば移動しない、スタブは置き直す）
    """
    site_root = site_root.resolve()
    import_root = (import_root or site_root).resolve()
    backup_root = (site_root.parent / "_pystub_bak").resolve()
    rel_moves: list[Path] = []
    for m in moves:
        mp = m.resolve()
        if not _is_within(mp, import_root) or mp == import_root or mp == site_root:
            raise ValueError(f"site-packages の外は扱いません: {mp}")
        rel_moves.append(mp.relative_to(import_root))
    bak_of = {rel: backup_root / (import_root / rel).relative_to(site_root) for rel in rel_moves}

    backup_cmds = ["# === 元ファイルをバックアップ領域へ移動（必ず最初に実行。既にあれば何もしない） ==="]
    for rel in rel_moves:
        src = import_root / rel
        dst = bak_of[rel]
        backup_cmds.append(
            f"if [ ! -e {_q(dst)} ]; then mkdir -p {_q(dst.parent)} && mv {_q(src)} {_q(dst)}; fi  # .bak 相当"
        )

    stub_top = name.replace(".", "/")
    main_rel = rel_moves[0] if rel_moves else Path(stub_top)
    is_file = str(main_rel).endswith(".py") or (import_root / main_rel).suffix == ".py"
    target = import_root / (main_rel if is_file else Path(stub_top))
    src_stub = Path(stub_root_hint) / (main_rel if is_file else Path(stub_top))
    guard = " && ".join(f"[ -e {_q(bak_of[r])} ]" for r in rel_moves) or "true"
    install_cmds = [
        "# スタブを配置（生成された files を _stubs/ に書き出してから。バックアップが無ければ中止）:",
        f"if {guard}; then rm -rf {_q(target)} && cp -r {_q(src_stub)} {_q(target)}; "
        f"else echo 'backup missing: run backup_commands first' >&2; exit 1; fi",
    ]

    uninstall_cmds = ["# === 方法1: バックアップから復元（推奨・高速・確実） ==="]
    uninstall_cmds.append(f"if {guard}; then rm -rf {_q(target)}; fi")
    for rel in rel_moves:
        uninstall_cmds.append(
            f"if [ -e {_q(bak_of[rel])} ]; then rm -rf {_q(import_root / rel)} && mv {_q(bak_of[rel])} {_q(import_root / rel)}; fi"
        )
    # 空になったバックアップ領域を消す（復元後の環境を元と同じにする: 仕様 7 節）
    uninstall_cmds.append(
        f"if [ -d {_q(backup_root)} ]; then find {_q(backup_root)} -depth -type d -empty -delete 2>/dev/null || true; fi"
    )
    uninstall_cmds.append("")
    uninstall_cmds.append("# === 方法2: pip で再インストール（バックアップ消失時のフォールバック。対象環境の python で実行） ===")
    if isinstance(pip_name, list):
        specs = [f"{n}=={v}" for n, v in pip_name] or [name.split(".")[0]]
    else:
        specs = [f"{pip_name}=={version}" if version else pip_name]
    uninstall_cmds.append("python -m pip install --force-reinstall --no-deps " + " ".join(_q(s) for s in specs))

    imports = verify_imports or [name]
    verify_cmds = [f'python -c "import {m}; print(\'{m} OK\')"' for m in imports]
    if isinstance(pip_name, list):
        for n, v in pip_name:
            verify_cmds.append(
                f'python -c "import importlib.metadata as m; v = m.version(\'{n}\'); '
                f'assert v == \'{v}\', v; print(\'version OK\', v)"'
            )
    elif version and "." not in name:
        verify_cmds.append(
            f'python -c "import importlib.metadata as m; v = m.version(\'{pip_name}\'); '
            f'assert v == \'{version}\', v; print(\'version OK\', v)"'
        )

    # PyInstaller のフック（仕様 5 節）: スタブにした範囲のフックは無効化する（名前が完全に一致するものだけ）
    hook_patterns: list[str] = []
    for d in (site_root / "PyInstaller" / "hooks", site_root / "_pyinstaller_hooks_contrib"):
        if not d.is_dir():
            continue
        for h in d.rglob("hook-*.py"):
            mod = h.name[len("hook-"):-3]
            if under(mod, name):
                hook_patterns.append(h.name if d.name == "hooks" and d.parent.name == "PyInstaller"
                                     else str(h.relative_to(site_root)).replace("\\", "/"))
    hook_cmds = (
        [f"# リネームして無効化: {h} → {h}.disabled" for h in sorted(set(hook_patterns))]
        if hook_patterns else [f"# {name} 用の PyInstaller フックは見つかりませんでした"]
    )

    notes = [
        "必ず backup_commands を最初に実行すること（元ファイルを site-packages の外へ移動）",
        "pip uninstall は不要（dist-info を残すので復元後もメタデータは元のまま）",
        "ビルド後は uninstall_commands → verify_commands の順で復元・検証",
        "コマンドは POSIX シェル（Git Bash 等）向け。パスは解析で見つけた実際の場所",
        "PyInstaller フックがスタブと衝突する場合は hook_disable でフック無効化",
        "適用前に verify ツールで、スタブに差し替えた状態の import を確かめることを推奨",
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

    pkg_main, _, given_root = _locate(parent_package, resolver)
    if pkg_main is None or given_root is None:
        return {"error": f"パッケージ '{parent_package}' が見つかりません"}
    site_root = _true_site_root(pkg_main, given_root)
    if site_root is None:
        return {"error": f"'{parent_package}' の場所 {pkg_main} は site-packages の中ではありません"}
    bad = _validate_target(parent_package, pkg_main, site_root, entry, resolver)
    if bad:
        return {"error": bad}
    if pkg_main.is_file():
        return {"error": f"'{parent_package}' は単一ファイルのモジュールでサブモジュールを持ちません: {pkg_main}"}

    sub_main, moves, _ = _locate(submodule, resolver)
    if sub_main is None:
        return {"error": f"サブモジュール '{submodule}' のファイルが見つかりません"}

    sem = get_semantics(graph)
    accesses = collect_accesses(sem, submodule)
    extra: list[str] = []
    note, conditional_info = _conditional_note(graph, sem, submodule, is_package=False)
    builder = _StubBuilder(sem, submodule, None, loud_note=note)
    code_by_mod = builder.build(accesses, extra)
    files: dict[str, str] = {}
    for m, c in code_by_mod.items():
        if not under(m, submodule):
            continue
        rel = _module_rel_path(m, sem.program, given_root)
        if sub_main.is_file() and m == submodule:
            rel = "/".join(submod_parts) + ".py"
        files[rel] = c
    for rel, code in files.items():
        compile(code, rel, "exec")

    # 排除されるパッケージ: 静的な import グラフ（同梱される範囲）の閉包にあり、サブモジュールを除くと消えるもの
    full = graph_closure(graph, sem.entry_module)
    without = graph_closure(graph, sem.entry_module, excludes=(submodule,))
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

    dists = _distributions_for(submodule, site_root)
    build_instructions = _generate_build_instructions(
        submodule, moves, site_root, dists if dists else parent_package, None, graph,
        verify_imports=[parent_package, submodule], import_root=given_root,
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
        "conditional": bool(conditional_info),
        "conditions": (conditional_info or {}).get("conditions", []),
    }
