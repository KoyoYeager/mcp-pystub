"""使用分析 + 判定

各 third_party パッケージ P について、エントリーポイントから到達可能なコード
（P の外のコード）が P の機能を実際に使うかを、関数単位の到達解析で判定する
（解析エンジンは semantics.py）。

使用（= スタブでは動かない）とみなすもの:
- P のシンボルの呼び出し・インスタンス化・デコレータとしての適用・基底クラス化
- P の関数・クラス・不明な値を外へ渡す（引数・格納・return）— 後で呼ばれうる
- P の不明な値（定数リテラル以外）を演算・反復・with に使う
- C 拡張から P を import している可能性（バイナリ中の文字列）

使用とみなさないもの（スタブが同じ形で提供する）:
- isinstance / 型注釈 / except 節 / 比較での参照
- 定数リテラル・サブモジュール・クラスの属性参照、別名への代入

最終判定: stubbable / nofollow / required
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from mcp_pystub.models import (
    GatewayFunction,
    ImportGraph,
    ImportGraphEdge,
    PackageAnalysis,
    SubmoduleStubHint,
)
from mcp_pystub.module_resolver import EXTENSION_SUFFIXES, ModuleResolver, is_extension_file
from mcp_pystub.semantics import Event, Program, Reachability, Ref, Unit, compute_reachability, under


# ビルドツールが内部で使用するパッケージ。スタブ化すると
# PyInstaller/Nuitka 自体がクラッシュするため常に required にする。
_BUILD_TOOL_PACKAGES: frozenset[str] = frozenset({
    "packaging", "setuptools", "pip", "wheel", "hatchling",
    "pyinstaller", "pyinstaller_hooks_contrib", "PyInstaller",
    "nuitka", "cx_freeze", "cx_Freeze", "pkg_resources", "_distutils_hack",
})


# ---------------------------------------------------------------------------
# 意味解析の構築（グラフごとにキャッシュ）
# ---------------------------------------------------------------------------


@dataclass
class Semantics:
    program: Program
    reach: Reachability
    entry_module: str
    # top-level パッケージ → そのパッケージに触れる（到達済みユニットの）イベント
    events_by_top: dict[str, list[tuple[Unit, Event]]] = field(default_factory=dict)
    unparsed_local: list[str] = field(default_factory=list)


def get_semantics(graph: ImportGraph) -> Semantics:
    cached = getattr(graph, "_semantics", None)
    if cached is not None:
        return cached
    resolver: ModuleResolver | None = getattr(graph, "resolver", None)
    if resolver is None:
        resolver = ModuleResolver(project_root=graph.project_root or ".")
        if graph.entry_point:
            resolver.add_local_root(Path(graph.entry_point).resolve().parent)
    program = Program(resolver)
    entry_module = getattr(graph, "entry_module", "") or ""
    for name, node in graph.nodes.items():
        if node.file_path and node.classification in ("local", "third_party"):
            program.register(name, node.file_path)
            if node.depth == 0 and not entry_module:
                entry_module = name
    reach = compute_reachability(program, entry_module) if entry_module else Reachability()

    events_by_top: dict[str, list[tuple[Unit, Event]]] = defaultdict(list)
    for key in reach.reached:
        unit = program.units[key]
        for ev in unit.events:
            tops: set[str] = set()
            if ev.ref is not None:
                tops = {m.split(".")[0] for m, _ in ev.ref.trace}
            elif ev.kind == "import":
                tops = {m.split(".")[0] for m in ev.modules}
            for top in tops:
                events_by_top[top].append((unit, ev))

    unparsed_local = []
    for name in reach.loaded_modules:
        info = program.modules.get(name)
        if info is not None and info.parse_error:
            node = graph.nodes.get(name)
            if node is not None and node.classification == "local":
                unparsed_local.append(info.path or name)
    sem = Semantics(program, reach, entry_module, dict(events_by_top), unparsed_local)
    graph._semantics = sem  # type: ignore[attr-defined]
    return sem


# ---------------------------------------------------------------------------
# 使用の判定
# ---------------------------------------------------------------------------

_SAFE_CTX = frozenset({"safe", "alias", "compare"})
_CALL_CTX = frozenset({"call", "decorator", "base", "raise"})
_ESCAPE_CTX = frozenset({"arg", "store", "return"})


def classify_event(ev: Event, prefix: str) -> str | None:
    """prefix 配下への参照イベントを分類する。

    Returns:
        "use"（スタブでは動かない）/ "ref"（スタブで提供すれば動く）/ None（無関係）
    """
    ref = ev.ref
    if ref is None or not ref.touches(prefix):
        return None
    kind = ref.kind
    chain_names = [c for _, ch in ref.trace for c in ch]
    is_version = "__version__" in chain_names
    ctx = ev.ctx
    if kind == "constattr":
        return "ref"  # 定数リテラルのメソッド呼び出し等（スタブも同じ値）
    if ctx in _CALL_CTX:
        return "use"
    if ctx in _SAFE_CTX:
        return "ref"
    if kind == "const" or is_version:
        return "ref"
    if ctx == "operand":
        if kind in ("module", "class"):
            return "ref"
        return "use"
    if ctx in _ESCAPE_CTX:
        return "use"
    return "use"


def _symbol_name(ref: Ref, prefix: str) -> str:
    entry = ref.entry_for(prefix)
    if entry is None:
        return ref.module
    mod, chain = entry
    return ".".join((mod,) + tuple(c if c not in ("()", "[]") else c for c in chain)).replace(".()", "()").replace(".[]", "[]")


@dataclass
class PrefixUsage:
    uses: list[tuple[Unit, Event, str]] = field(default_factory=list)
    refs: list[tuple[Unit, Event, str]] = field(default_factory=list)
    imports: list[tuple[Unit, Event]] = field(default_factory=list)


def prefix_usage(sem: Semantics, prefix: str) -> PrefixUsage:
    """prefix（パッケージ / サブモジュール）の外の到達可能コードからの使用を集める。"""
    top = prefix.split(".")[0]
    out = PrefixUsage()
    for unit, ev in sem.events_by_top.get(top, []):
        if under(unit.module, prefix):
            continue
        if ev.kind == "import":
            if any(under(m, prefix) for m in ev.modules) or (
                ev.from_base and any(under(f"{ev.from_base}.{n}", prefix) for n in ev.names if n != "*")
            ):
                out.imports.append((unit, ev))
            continue
        if ev.kind == "dynamic":
            if ev.ref is not None and ev.ref.touches(prefix):
                out.uses.append((unit, ev, _symbol_name(ev.ref, prefix) + ".<getattr>"))
            continue
        cls = classify_event(ev, prefix)
        if cls is None or ev.ref is None:
            continue
        sym = _symbol_name(ev.ref, prefix)
        if cls == "use":
            out.uses.append((unit, ev, sym))
        else:
            out.refs.append((unit, ev, sym))
    return out


def import_closure(sem: Semantics, exclude_prefix: str = "") -> set[str]:
    """エントリーから読み込まれるモジュール（exclude_prefix 配下を除いた場合）。"""
    reach = sem.reach
    seen: set[str] = set()
    q: deque[str] = deque([sem.entry_module])
    while q:
        m = q.popleft()
        if m in seen:
            continue
        if exclude_prefix and under(m, exclude_prefix):
            continue
        seen.add(m)
        if "." in m:
            q.append(m.rsplit(".", 1)[0])
        for t in reach.module_imports.get(m, ()):
            if t not in seen:
                q.append(t)
    return {m for m in seen if m in reach.loaded_modules}


# ---------------------------------------------------------------------------
# C 拡張
# ---------------------------------------------------------------------------


def _site_root_for(resolver: ModuleResolver, package_name: str) -> tuple[Path | None, bool]:
    """(パッケージのルート, 単体の拡張モジュールか)。"""
    for sp in resolver.site_packages:
        d = sp / package_name
        if (d / "__init__.py").is_file() or d.is_dir():
            return d, False
        if (sp / f"{package_name}.py").is_file():
            return sp / f"{package_name}.py", False
        for suf in EXTENSION_SUFFIXES:
            f = sp / f"{package_name}{suf}"
            if f.is_file():
                return f, True
    return None, False


def has_c_extensions(package_name: str, resolver: ModuleResolver) -> bool:
    """パッケージの ROOT に C 拡張（.pyd / .so）があるか、パッケージ自体が拡張モジュールか。

    ROOT に .pyd がある = import 時に C 拡張が読み込まれる → スタブ化不可。
    サブディレクトリのみに .pyd がある場合（pandas/_libs/ 等）はスタブ化可能。
    スタブは参照されるモジュールだけを提供し、_libs/ の中身は含まないため。
    """
    root, single_ext = _site_root_for(resolver, package_name)
    if root is None:
        return False
    if single_ext:
        return True
    if root.is_dir():
        try:
            for f in root.iterdir():
                if f.is_file() and is_extension_file(f.name):
                    return True
        except OSError:
            return False
    return False


def _has_c_extensions(package_name: str, graph: ImportGraph) -> bool:  # 互換
    resolver = getattr(graph, "resolver", None)
    if resolver is None:
        return False
    return has_c_extensions(package_name, resolver)


def _binary_mentions(sem: Semantics, names: set[str]) -> dict[str, str]:
    """到達する拡張モジュール（バイナリ）が import しうるパッケージ名 → 拡張モジュールのパス。"""
    if not names:
        return {}
    pattern = re.compile(
        rb"\x00(" + b"|".join(re.escape(n.encode()) for n in sorted(names, key=len, reverse=True))
        + rb")(?=\x00|\.)"
    )
    found: dict[str, str] = {}
    for mod in sem.reach.loaded_modules:
        info = sem.program.modules.get(mod)
        if info is None or not info.is_extension or not info.path:
            continue
        own_top = mod.split(".")[0]
        try:
            data = Path(info.path).read_bytes()
        except OSError:
            continue
        for m in pattern.finditer(data):
            name = m.group(1).decode()
            if name != own_top and name not in found:
                found[name] = info.path
    return found


# ---------------------------------------------------------------------------
# パッケージ判定
# ---------------------------------------------------------------------------


def analyze_packages(
    graph: ImportGraph,
    target_packages: set[str] | None = None,
) -> list[PackageAnalysis]:
    """グラフ内の全 third_party パッケージを分析する。

    Args:
        graph: 構築済みの import グラフ
        target_packages: 分析対象のパッケージ名セット（None で全 third_party）

    Returns:
        各パッケージの PackageAnalysis リスト
    """
    if target_packages is None:
        target_packages = {
            node.top_level_package
            for node in graph.nodes.values()
            if node.classification == "third_party" and node.top_level_package
        }

    sem = get_semantics(graph)
    resolver: ModuleResolver | None = getattr(graph, "resolver", None)

    edges_by_target_pkg: dict[str, list[ImportGraphEdge]] = defaultdict(list)
    for edge in graph.edges:
        target_node = graph.nodes.get(edge.to_module)
        if target_node and target_node.top_level_package in target_packages:
            edges_by_target_pkg[target_node.top_level_package].append(edge)

    results = [
        _analyze_single_package(pkg, edges_by_target_pkg.get(pkg, []), graph, sem, resolver)
        for pkg in sorted(target_packages)
    ]

    # C 拡張から import されている可能性（AST では見えない）
    candidates = {r.package_name for r in results if r.verdict in ("stubbable", "nofollow")}
    mentions = _binary_mentions(sem, candidates)
    for i, r in enumerate(results):
        if r.package_name in mentions:
            results[i] = _to_required(
                r,
                f"C 拡張 {Path(mentions[r.package_name]).name} が '{r.package_name}' を "
                "import している可能性があります（バイナリ中にモジュール名あり、安全側判定）。",
            )
    return results


def _to_required(pkg: PackageAnalysis, reason: str) -> PackageAnalysis:
    return PackageAnalysis(
        package_name=pkg.package_name,
        verdict="required",
        reason=reason,
        is_directly_imported=pkg.is_directly_imported,
        is_transitively_imported=pkg.is_transitively_imported,
        imported_by=pkg.imported_by,
        import_depth=pkg.import_depth,
        gateway_functions=pkg.gateway_functions,
        estimated_size_mb=pkg.estimated_size_mb,
        submodule_stubs=pkg.submodule_stubs,
        warnings=pkg.warnings + [reason],
    )


def _gateways(uses: list[tuple[Unit, Event, str]], reached: set) -> list[GatewayFunction]:
    by_unit: dict[tuple[str, str], GatewayFunction] = {}
    for unit, ev, sym in uses:
        gf = by_unit.get(unit.key)
        if gf is None:
            gf = GatewayFunction(
                module=unit.module,
                function_name="__module_level__" if unit.qualname == "<module>" else unit.qualname,
                symbols_from_package=[],
                called_by_project=unit.key in reached,
            )
            by_unit[unit.key] = gf
        if sym not in gf.symbols_from_package:
            gf.symbols_from_package.append(sym)
    return list(by_unit.values())


def _analyze_single_package(
    package_name: str,
    edges: list[ImportGraphEdge],
    graph: ImportGraph,
    sem: Semantics,
    resolver: ModuleResolver | None,
) -> PackageAnalysis:
    """単一パッケージの分析と判定を行う。"""
    if package_name in _BUILD_TOOL_PACKAGES:
        return PackageAnalysis(
            package_name=package_name,
            verdict="required",
            reason="ビルドツール（PyInstaller/Nuitka 等）が使用するためスタブ化不可。",
        )

    imported_by: list[str] = []
    is_directly_imported = False
    is_transitively_imported = False
    min_depth = 999

    for edge in edges:
        from_node = graph.nodes.get(edge.from_module)
        if from_node is None:
            continue
        if from_node.top_level_package == package_name and from_node.classification == "third_party":
            continue  # パッケージ内部の自己 import
        imported_by.append(edge.from_module)
        if from_node.classification == "local":
            is_directly_imported = True
        else:
            is_transitively_imported = True
        if from_node.depth < min_depth:
            min_depth = from_node.depth

    if not edges:
        return PackageAnalysis(
            package_name=package_name,
            verdict="required",
            reason="import エッジが見つかりません",
        )

    common = dict(
        package_name=package_name,
        is_directly_imported=is_directly_imported,
        is_transitively_imported=is_transitively_imported,
        imported_by=_unique(imported_by),
        import_depth=(min_depth + 1) if min_depth != 999 else 0,
    )

    # C 拡張を含むパッケージはスタブ化不可（間接排除のヒントを探す）
    if resolver is not None and has_c_extensions(package_name, resolver):
        hints = find_submodule_hints(package_name, graph, sem)
        reason = "C 拡張（.pyd/.so）を含むためスタブ化不可。"
        if hints:
            submod_names = ", ".join(h.submodule for h in hints)
            reason += f" ただし {submod_names} をスタブ化することで間接排除が可能です。"
        return PackageAnalysis(verdict="required", reason=reason, submodule_stubs=hints, **common)

    usage = prefix_usage(sem, package_name)
    gateways = _gateways(usage.uses, sem.reach.reached)

    if usage.uses:
        first_unit, first_ev, first_sym = usage.uses[0]
        where = first_unit.module + (
            "" if first_unit.qualname == "<module>" else f":{first_unit.qualname}"
        )
        kind = "import 時に" if first_unit.qualname == "<module>" else "到達可能な関数で"
        return PackageAnalysis(
            verdict="required",
            reason=(
                f"{kind}実際に使用されています（{where} 行 {first_ev.lineno}: {first_sym}、"
                f"文脈 {first_ev.ctx}、使用箇所 {len(usage.uses)} 件）。"
            ),
            gateway_functions=gateways,
            **common,
        )

    if sem.unparsed_local:
        return PackageAnalysis(
            verdict="required",
            reason=(
                "構文解析できないローカルファイルがあり使用状況を確定できません（安全側判定）: "
                + ", ".join(sem.unparsed_local[:3])
            ),
            gateway_functions=gateways,
            **common,
        )

    # nofollow: 外からの実行される import が全て try/except ImportError で保護され、
    # 属性参照も無い（`P is None` 程度）
    executed_imports = usage.imports
    attr_refs = [r for r in usage.refs if r[1].ref is not None and _has_attr_access(r[1].ref, package_name)]
    if executed_imports and all(ev.protected for _, ev in executed_imports) and not attr_refs:
        return PackageAnalysis(
            verdict="nofollow",
            reason="全ての import が try/except ImportError で保護され、到達可能なコードで"
            "使用されていません。--nofollow-import-to で除外可能。",
            gateway_functions=gateways,
            **common,
        )

    if is_directly_imported:
        reason = "プロジェクトコードが import していますが、実際のコードパスでは使用されていません。"
    else:
        reason = "依存ライブラリ経由でのみ import され、プロジェクトは該当機能を使用していません。"
    if usage.refs:
        reason += f"（参照 {len(usage.refs)} 件は isinstance・型注釈・定数などでスタブが提供します）"
    return PackageAnalysis(verdict="stubbable", reason=reason, gateway_functions=gateways, **common)


def _has_attr_access(ref: Ref, prefix: str) -> bool:
    entry = ref.entry_for(prefix)
    if entry is None:
        return False
    _, chain = entry
    return bool(chain)


# ---------------------------------------------------------------------------
# サブモジュールによる間接排除
# ---------------------------------------------------------------------------


def find_submodule_hints(
    c_ext_package: str, graph: ImportGraph, sem: Semantics
) -> list[SubmoduleStubHint]:
    """C拡張パッケージを間接排除できるサブモジュールを検出する。

    判定ロジック:
    1. 到達コードで c_ext_package を import しているモジュール（別パッケージ所属）を特定
    2. その祖先サブモジュール（短い順）を候補とし、
       a. 候補の外の到達可能コードが候補の機能を使わない（呼び出し・値渡し等なし）
       b. 候補を除いた import 閉包に c_ext_package が残らない
       を満たす最短の候補をヒントとする
    """
    reach = sem.reach
    importers: set[str] = set()
    for mod, targets in reach.module_imports.items():
        if mod.split(".")[0] == c_ext_package:
            continue
        if any(t.split(".")[0] == c_ext_package for t in targets):
            node = graph.nodes.get(mod)
            info = sem.program.modules.get(mod)
            if "." in mod and (node is None or node.classification == "third_party" or (
                info is not None and node is None
            )):
                importers.add(mod)
    if not importers:
        return []

    candidates: set[str] = set()
    for imp in importers:
        parts = imp.split(".")
        for i in range(2, len(parts) + 1):
            candidates.add(".".join(parts[:i]))

    hints: list[SubmoduleStubHint] = []
    chosen: list[str] = []
    for cand in sorted(candidates, key=lambda c: (c.count("."), c)):
        if any(under(cand, c) for c in chosen):
            continue
        usage = prefix_usage(sem, cand)
        if usage.uses:
            continue
        closure = import_closure(sem, exclude_prefix=cand)
        if any(m.split(".")[0] == c_ext_package for m in closure):
            continue
        chosen.append(cand)
        names: set[str] = set()
        for unit, ev in usage.imports:
            if ev.from_base and under(ev.from_base, cand):
                names.update(n for n in ev.names if n != "*")
        for _, ev, sym in usage.refs:
            entry = ev.ref.entry_for(cand) if ev.ref else None
            if entry and entry[1]:
                names.add(entry[1][0])
        info = sem.program.modules.get(cand)
        node = graph.nodes.get(cand)
        hints.append(SubmoduleStubHint(
            target_package=c_ext_package,
            parent_package=cand.split(".")[0],
            submodule=cand,
            submodule_path=(info.path if info and info.path else (node.file_path if node else "")) or "",
            imported_symbols=sorted(names),
            reason=(
                f"{cand} が {c_ext_package} を import していますが、"
                f"到達可能なコードは {cand} の機能を使用していません。"
                f"このサブモジュールをスタブ化することで "
                f"{c_ext_package} を間接排除できます。"
            ),
        ))
    return hints


def _unique(items: list[str]) -> list[str]:
    """重複を除去しつつ順序を保持する。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
