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
from mcp_pystub.semantics import (
    Event, Program, Reachability, Ref, Unit, certain_units, compute_reachability, entry_paths, under,
)


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
    graph: ImportGraph | None = None
    hooks: dict[str, str] = field(default_factory=dict)  # フックのあるモジュール名 → フックのファイル
    certain: set = field(default_factory=set)  # 確実に実行されるコード単位
    precise_parent: dict = field(default_factory=dict)  # 確実なユニット → 確実な辺での親
    entries: dict = field(default_factory=dict)  # 確実でないユニット → 過大近似の入口


def find_pyinstaller_hooks(resolver: ModuleResolver) -> dict[str, str]:
    """解析対象の site-packages にある PyInstaller のフック: モジュール名 → フックのパス（仕様 5 節）。"""
    hooks: dict[str, str] = {}
    for sp in resolver.site_packages:
        dirs = [
            sp / "PyInstaller" / "hooks",
            sp / "_pyinstaller_hooks_contrib" / "stdhooks",
            sp / "_pyinstaller_hooks_contrib" / "hooks" / "stdhooks",
        ]
        try:
            dirs.extend(d for d in sp.glob("*/__pyinstaller") if d.is_dir())
            dirs.extend(d for d in sp.glob("*/_pyinstaller") if d.is_dir())
        except OSError:
            pass
        for d in dirs:
            if not d.is_dir():
                continue
            try:
                for f in d.glob("hook-*.py"):
                    mod = f.name[len("hook-"):-3]
                    hooks.setdefault(mod, str(f))
            except OSError:
                continue
    return hooks


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
    graph_modules: list[str] = []
    for name, node in graph.nodes.items():
        if node.file_path and node.classification in ("local", "third_party"):
            program.register(name, node.file_path)
            graph_modules.append(name)
            if node.classification == "local":
                program.local_modules.add(name)
            if node.depth == 0 and not entry_module:
                entry_module = name
    # ビルド時に PyInstaller のフックが import するモジュール（グラフに入っているもの）も起点
    hooks = find_pyinstaller_hooks(resolver)
    build_roots = sorted(
        m for m in hooks
        if m in graph.nodes and graph.nodes[m].classification == "third_party" and graph.nodes[m].file_path
    )
    reach = (
        compute_reachability(program, entry_module, build_roots=build_roots, graph_modules=graph_modules)
        if entry_module else Reachability()
    )

    events_by_top: dict[str, list[tuple[Unit, Event]]] = defaultdict(list)
    loaded_tops = {m.split(".")[0] for m in reach.loaded_modules}
    for key in reach.reached:
        unit = program.units[key]
        for ev in unit.events:
            if ev.guard and ev.guard not in loaded_tops:
                continue  # `if 'X' not in sys.modules: return` の後: X が他で読み込まれない限り実行されない
            tops: set[str] = set()
            if ev.ref is not None:
                tops = ev.ref.touched_tops()
            elif ev.kind == "import":
                tops = {m.split(".")[0] for m in ev.modules}
                if ev.from_base:
                    tops.add(ev.from_base.split(".")[0])
            for top in tops:
                events_by_top[top].append((unit, ev))

    unparsed_local = []
    for name in reach.loaded_modules:
        info = program.modules.get(name)
        if info is not None and info.parse_error:
            node = graph.nodes.get(name)
            if node is not None and node.classification == "local":
                unparsed_local.append(info.path or name)
    sem = Semantics(program, reach, entry_module, dict(events_by_top), unparsed_local, graph,
                    {m: hooks[m] for m in build_roots})
    sem.certain = certain_units(reach)
    sem.entries = entry_paths(reach, sem.certain)
    for src, dst, t, _ev in reach.edges:
        if t == "precise" and dst in sem.certain and dst not in sem.precise_parent and src != dst:
            sem.precise_parent[dst] = src
    graph._semantics = sem  # type: ignore[attr-defined]
    return sem


# ---------------------------------------------------------------------------
# 使用の判定（許可リスト方式: 仕様 2・3 節）
# ---------------------------------------------------------------------------

# どの文脈でも元と同じ値・同じ同一性をスタブが提供する種類
_ALWAYS_SAFE_KINDS = frozenset({
    "const", "constattr", "classconst", "external", "external_module",
})


def _leaf_is_use(rr: Ref, ctx: str) -> bool:
    kind = rr.kind
    names = [c for _, ch in rr.trace for c in ch]
    if kind in _ALWAYS_SAFE_KINDS or "__version__" in names:
        return False
    if ctx in ("bind", "annot", "read", "hasattr", "presence"):
        return False  # A. 束縛（スタブが同じ名前・同じ種類で提供する）
    if ctx in ("isinst", "except"):
        return kind != "class"  # B. 判別: P の実体が生成されないので結果は元と同じ
    if ctx == "is":
        return kind not in ("class", "function", "method", "module")
    if ctx == "truth":
        # 真偽の判定: クラス・関数・モジュールは元でもスタブでも常に真
        return kind not in ("class", "function", "method", "module", "external_module")
    return True  # C. 実行（未知の文脈も含めて既定は使用）


def classify_event(ev: Event, prefix: str) -> str | None:
    """prefix 配下への参照イベントを分類する。

    Returns:
        "use"（スタブでは動かない = C）/ "ref"（スタブで提供すれば同じ = A・B）/ None（無関係）
    """
    ref = ev.ref
    if ref is None or not ref.touches(prefix):
        return None
    for rr in ref.all_refs():
        if rr.alts or not rr.touches(prefix):
            continue
        if rr.fallback and ev.ctx == "truth":
            continue  # except ImportError の代替: 元の環境（パッケージがある）では実行されない
        if _leaf_is_use(rr, ev.ctx):
            return "use"
    return "ref"


def _symbol_name(ref: Ref, prefix: str) -> str:
    entry = ref.entry_for(prefix)
    if entry is None:
        return ref.module
    mod, chain = entry
    return ".".join((mod,) + tuple(chain)).replace(".()", "()").replace(".[]", "[]")


@dataclass
class PrefixUsage:
    uses: list[tuple[Unit, Event, str]] = field(default_factory=list)
    refs: list[tuple[Unit, Event, str]] = field(default_factory=list)
    imports: list[tuple[Unit, Event]] = field(default_factory=list)


def prefix_usage(sem: Semantics, prefix: str, exclude: tuple[str, ...] = ()) -> PrefixUsage:
    """prefix（パッケージ / サブモジュール）の外の到達可能コードからの使用を集める。

    exclude: 同時にスタブ化する他の範囲（そこからの使用は数えない）
    """
    top = prefix.split(".")[0]
    out = PrefixUsage()
    skip = (prefix,) + tuple(exclude)
    for unit, ev in sem.events_by_top.get(top, []):
        if any(under(unit.module, x) for x in skip):
            continue
        if ev.kind == "import":
            if any(under(m, prefix) for m in ev.modules) or (
                ev.from_base and any(under(f"{ev.from_base}.{n}", prefix) for n in ev.names if n != "*")
            ) or (ev.from_base and under(ev.from_base, prefix)):
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


def import_closure(sem: Semantics, exclude_prefix: str = "", excludes: tuple[str, ...] = ()) -> set[str]:
    """エントリー（とビルド時の起点）から読み込まれるモジュール（exclude 配下を除いた場合）。"""
    reach = sem.reach
    ex = tuple(x for x in (exclude_prefix,) + tuple(excludes) if x)
    seen: set[str] = set()
    q: deque[str] = deque([sem.entry_module] + list(reach.build_roots))
    while q:
        m = q.popleft()
        if m in seen:
            continue
        if any(under(m, x) for x in ex):
            continue
        seen.add(m)
        if "." in m:
            q.append(m.rsplit(".", 1)[0])
        for t in reach.module_imports.get(m, ()):
            if t not in seen:
                q.append(t)
    return {m for m in seen if m in reach.loaded_modules}


def graph_closure(graph: ImportGraph, entry: str, excludes: tuple[str, ...] = ()) -> set[str]:
    """静的な import グラフ（関数内・保護された import も含む = PyInstaller が同梱する範囲）での閉包。"""
    adj: dict[str, list[str]] = {}
    for e in graph.edges:
        adj.setdefault(e.from_module, []).append(e.to_module)
    seen: set[str] = set()
    q: deque[str] = deque([entry])
    while q:
        m = q.popleft()
        if m in seen or any(under(m, x) for x in excludes):
            continue
        seen.add(m)
        if "." in m:
            q.append(m.rsplit(".", 1)[0])
        q.extend(adj.get(m, ()))
    return seen


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
        chints: list[SubmoduleStubHint] = []
        reason = "C 拡張（.pyd/.so）を含むためスタブ化不可。"
        if hints:
            submod_names = ", ".join(h.submodule for h in hints)
            reason += f" ただし {submod_names} をスタブ化することで間接排除が可能です。"
        else:
            chints = find_submodule_hints(package_name, graph, sem, allow_conditional=True)
            if chints:
                reason += (" 条件付きの候補: " + ", ".join(h.submodule for h in chints)
                           + "（conditional_submodule_hints を参照）")
        return PackageAnalysis(verdict="required", reason=reason, submodule_stubs=hints,
                               conditional_submodule_stubs=chints, **common)

    usage = prefix_usage(sem, package_name)
    gateways = _gateways(usage.uses, sem.reach.reached)

    pending: dict | None = None
    certain_uses = [(u, ev, sym) for u, ev, sym in usage.uses if is_certain_use(sem, u, ev)]
    if usage.uses and not certain_uses:
        pending = build_conditions(sem, usage.uses, package_name)
        if pending is None:
            certain_uses = usage.uses  # 入口を特定できない: 区別できないので required
    if certain_uses:
        first_unit, first_ev, first_sym = certain_uses[0]
        where = first_unit.module + (
            "" if first_unit.qualname == "<module>" else f":{first_unit.qualname}"
        )
        kind = "import 時に" if first_unit.qualname == "<module>" else "確実に実行される関数で"
        return PackageAnalysis(
            verdict="required",
            reason=(
                f"{kind}実際に使用されています（{where} 行 {first_ev.lineno}: {first_sym}、"
                f"文脈 {first_ev.ctx}、確実な使用 {len(certain_uses)} 件 / 使用 {len(usage.uses)} 件）。"
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

    # エントリーポイント・プラグイン登録（仕様 4 節: 解析不能）: entry_points() で読み込まれて
    # 未知のコードから呼ばれる
    eps = sorted({m for caller, m in sem.reach.entry_point_calls
                  if under(m, package_name) and not under(caller, package_name)})
    if eps:
        return PackageAnalysis(
            verdict="required",
            reason=f"到達可能なコードが entry_points() でプラグインを読み込み、{eps[0]} が対象です"
            "（呼ばれ方を静的に追えないため安全側判定）。",
            gateway_functions=gateways,
            **common,
        )

    # 有無の検出（仕様 3 節）: 'X' in sys.modules / find_spec("X") の X が、P をスタブにすると
    # 読み込まれなくなる（または P の一部）なら、検出結果が変わる
    if sem.reach.presence_checked:
        full = import_closure(sem)
        without = None
        for x in sorted(sem.reach.presence_checked):
            if under(x, package_name):
                changed = False  # スタブ化しても P 自体は（スタブとして）読み込まれる
            else:
                if without is None:
                    without = import_closure(sem, exclude_prefix=package_name)
                changed = x in full and x not in without
            if changed:
                return PackageAnalysis(
                    verdict="required",
                    reason=f"到達可能なコードがモジュール '{x}' の有無を調べており、{package_name} を"
                    "スタブにすると結果が変わります（安全側判定）。",
                    gateway_functions=gateways,
                    **common,
                )

    # 解析不能（仕様 4 節）: プロジェクトのコードにある非定数の動的 import / exec / sys.modules[...]
    local_unanalyzable = [u for u in sem.reach.unanalyzable if u[0] in sem.program.local_modules]
    if local_unanalyzable:
        m, what, line = local_unanalyzable[0]
        return PackageAnalysis(
            verdict="required",
            reason=f"プロジェクトのコードに静的に解析できない処理があります（{m} 行 {line}: {what}、安全側判定）。",
            gateway_functions=gateways,
            **common,
        )

    if pending is not None:
        return PackageAnalysis(
            verdict="conditional",
            reason=(
                "安全は示せません（到達の過大近似・入力次第の経路でだけ使われます）。"
                f"次の機能を使わない前提で外せます: {pending['summary']}（verify(..., run_entry=True) で確認を推奨）"
            ),
            gateway_functions=gateways,
            conditions=pending["conditions"],
            conditions_total=pending["total"],
            unproven_because=pending["because"],
            confidence=pending["confidence"],
            **common,
        )

    # nofollow（仕様 3 節）: 実行される import が全て「単純な保護」（try 本体は import だけ、
    # except は pass か None/False 代入だけ）で、到達可能なコードからの参照が一切無い
    executed_imports = usage.imports
    if (
        executed_imports
        and all(ev.protected and ev.protect_ok for _, ev in executed_imports)
        and not usage.refs
    ):
        return PackageAnalysis(
            verdict="nofollow",
            reason="全ての import が try/except ImportError で単純に保護され、到達可能なコードからの"
            "参照がありません。--nofollow-import-to で除外可能。",
            gateway_functions=gateways,
            **common,
        )

    if is_directly_imported:
        reason = "プロジェクトコードが import していますが、実際のコードパスでは使用されていません。"
    else:
        reason = "依存ライブラリ経由でのみ import され、プロジェクトは該当機能を使用していません。"
    if usage.refs:
        reason += f"（参照 {len(usage.refs)} 件は isinstance・型注釈・定数などでスタブが提供します）"
    reason += "（静的判定。verify ツールで動作確認を推奨）"
    return PackageAnalysis(verdict="stubbable", reason=reason, gateway_functions=gateways, **common)


# ---------------------------------------------------------------------------
# サブモジュールによる間接排除（仕様 6 節）
# ---------------------------------------------------------------------------


def find_submodule_hints(
    c_ext_package: str, graph: ImportGraph, sem: Semantics, allow_conditional: bool = False
) -> list[SubmoduleStubHint]:
    """C 拡張パッケージ T を import 閉包から外す、最小で互いに重ならないサブモジュールの組。

    1. 静的な import グラフ（PyInstaller が同梱する範囲）で T を import しているモジュールを集める
    2. 各モジュールについて、祖先のサブモジュール（短い順）から「外の到達可能なコードが C の
       操作をしない」最初の候補を選ぶ（ライブラリの中核は選ばれない）。選べないモジュールが
       1 つでもあればヒントは出さない
    3. 選んだ組を全部同時に適用して、(a) 組の外からの使用が無い (b) T がグラフの閉包から消える、
       を確かめる。満たさなければ出さない
    """
    T = c_ext_package
    entry = sem.entry_module
    full = graph_closure(graph, entry)
    if not any(under(m, T) for m in full):
        return []
    importers: set[str] = set()
    for e in graph.edges:
        if under(e.to_module, T) and not under(e.from_module, T) and e.from_module in full:
            importers.add(e.from_module)
    if not importers:
        return []
    chosen: list[str] = []
    for imp in sorted(importers, key=lambda m: (m.count("."), m)):
        if any(under(imp, c) for c in chosen):
            continue
        node = graph.nodes.get(imp)
        if "." not in imp or node is None or node.classification != "third_party":
            return []  # トップレベルのパッケージ自体やプロジェクトのコードは切れない
        parts = imp.split(".")
        picked = ""
        for i in range(2, len(parts) + 1):
            cand = ".".join(parts[:i])
            if under(cand, T):
                break
            uses = prefix_usage(sem, cand).uses
            if not uses or (allow_conditional and _conditional_ok(sem, uses, cand)):
                picked = cand
                break
        if not picked:
            return []
        chosen = [c for c in chosen if not under(c, picked)] + [picked]
    chosen = sorted(set(chosen))
    # 全部同時に適用して確かめる
    cond_info: dict[str, dict] = {}
    for c in chosen:
        others = tuple(x for x in chosen if x != c)
        uses = prefix_usage(sem, c, exclude=others).uses
        if uses:
            if not allow_conditional or not _conditional_ok(sem, uses, c):
                return []
            cond_info[c] = build_conditions(sem, uses, c)  # type: ignore[assignment]
    if allow_conditional and not cond_info:
        return []  # 証明済みのヒントとして出せる（こちらには出さない）
    # 静的な import グラフ（PyInstaller が同梱する範囲 ⊇ 実行時に読み込まれる範囲）から T が消えること。
    # T のフック（ビルド時の起点）は、T がグラフから消えれば実行されない
    after = graph_closure(graph, entry, excludes=tuple(chosen))
    if any(under(m, T) for m in after):
        return []

    hints: list[SubmoduleStubHint] = []
    for cand in chosen:
        others = tuple(x for x in chosen if x != cand)
        usage = prefix_usage(sem, cand, exclude=others)
        names: set[str] = set()
        for unit, ev in usage.imports:
            if ev.from_base and under(ev.from_base, cand):
                names.update(n for n in ev.names if n != "*")
        for _, ev, sym in usage.refs:
            entry_ = ev.ref.entry_for(cand) if ev.ref else None
            if entry_ and entry_[1]:
                names.add(entry_[1][0])
        info = sem.program.modules.get(cand)
        node = graph.nodes.get(cand)
        group = ", ".join(chosen)
        ci = cond_info.get(cand)
        hints.append(SubmoduleStubHint(
            confidence=(("high" if all(x.get("confidence") == "high" for x in cond_info.values()) else "low")
                        if cond_info else ""),
            conditional=bool(cond_info),
            conditions=(ci or {}).get("conditions", []),
            conditions_total=(ci or {}).get("total", 0),
            target_package=T,
            parent_package=cand.split(".")[0],
            submodule=cand,
            submodule_path=(info.path if info and info.path else (node.file_path if node else "")) or "",
            imported_symbols=sorted(names),
            reason=(
                f"{cand} が {T} への import 経路にあり、到達可能なコードは {cand} の機能を実行しません。"
                + (f"この組（{group}）を全て同時にスタブ化すると" if len(chosen) > 1 else "これをスタブ化すると")
                + f" {T} が import 閉包から外れます（静的判定。verify ツールで動作確認を推奨）。"
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


# ---------------------------------------------------------------------------
# 条件付き候補（仕様 9 節）
# ---------------------------------------------------------------------------

_TAG_TEXT = {
    "name": "名前だけの振り分け（受け手の型が分からない呼び出しを、同名のメソッド全てに届くと仮定）",
    "argclass": "引数に渡したクラス（渡した先で生成されると仮定）",
    "decorator": "登録デコレータ（飾られた関数が後で呼ばれると仮定）",
    "branch": "入力・呼び出し次第の分岐（関数の中の if / except / 条件式の枝）",
}

MAX_CONDITIONS = 10


def _conditional_ok(sem: Semantics, uses: list, prefix: str) -> bool:
    """使用が全て「確実ではない」もので、入口を特定できるか。"""
    if any(is_certain_use(sem, u, ev) for u, ev, _ in uses):
        return False
    return build_conditions(sem, uses, prefix) is not None


def is_certain_use(sem: Semantics, unit: Unit, ev: Event) -> bool:
    """確実に実行される使用か（import 時・ビルド時・エントリーが必ず通る経路で、分岐の中ではない）。"""
    return unit.key in sem.certain and not ev.cond


def _api_name(sem: Semantics, key: tuple[str, str]) -> str:
    """ユニットを、利用者が照らせる名前に（パッケージの最上位から再エクスポートされていればその名前）。"""
    module, qual = key
    q = qual.split("#")[0].replace(".<locals>.", ".")
    if q == "<module>":
        return f"import {module}"
    top = module.split(".")[0]
    head = q.split(".")[0]
    info = sem.program.modules.get(top)
    if info is not None and not info.opaque and head in info.bindings and module != top:
        try:
            r = sem.program.resolve(top, (head,))
            if r.module == module:
                return f"{top}.{q}()"
        except Exception:  # noqa: BLE001
            pass
    return f"{module}.{q}()"


def _path_to(sem: Semantics, key: tuple[str, str], stop: tuple[str, str], limit: int = 6) -> list[str]:
    out = []
    seen = set()
    k = key
    while k is not None and k not in seen and len(out) < limit:
        seen.add(k)
        out.append(_api_name(sem, k))
        if k == stop:
            break
        w = sem.reach.why.get(k)
        k = w[0] if w else None
    return list(reversed(out))


def _public_route(sem: Semantics, caller: tuple[str, str] | None, limit: int = 8) -> tuple[str, list[str]]:
    """確実な呼び出し元から起点まで遡り、プロジェクトのコードが直接呼ぶ依存ライブラリの関数（公開 API）と経路。"""
    chain: list[tuple[str, str]] = []
    k = caller
    seen = set()
    while k is not None and k not in seen and len(chain) < 60:
        seen.add(k)
        chain.append(k)
        k = sem.precise_parent.get(k)
    chain.reverse()  # 起点 → 呼び出し元
    api = ""
    for i, u in enumerate(chain):
        if u[0] in sem.program.local_modules:
            continue
        if i > 0 and chain[i - 1][0] in sem.program.local_modules and u[1] != "<module>":
            api = _api_name(sem, u)
            break
    route = [_api_name(sem, u) for u in chain if u[0] not in sem.program.local_modules][-limit:]
    return api, route


def build_conditions(sem: Semantics, uses: list, prefix: str) -> dict | None:
    """使用（全て確実ではない）から「これを使うと壊れる」入口を組み立てる。入口が分からなければ None。"""
    groups: dict = {}
    because: set[str] = set()
    for unit, ev, sym in uses:
        if unit.key in sem.certain and ev.cond:
            ekey, tag, eev, caller = unit.key, "branch", ev, unit.key
        else:
            info = sem.entries.get(unit.key)
            if info is None:
                return None
            ekey, tag, eev, caller = info
            if tag == "precise":
                # 確実な辺の先が確実でない（起点から外れた）: 区別できない
                return None
        because.add(tag)
        g = groups.setdefault((ekey, tag), {
            "entry": _api_name(sem, ekey),
            "entry_location": f"{ekey[0]}:{ekey[1]}" + (f" 行 {eev.lineno}" if tag == "branch" and getattr(eev, "lineno", 0) else ""),
            "why_unproven": _TAG_TEXT.get(tag, tag),
            "dispatch_name": (getattr(eev, "name", "") if tag == "name" else ""),
            "called_from": _api_name(sem, caller) if caller is not None else "",
            "uses": [],
            "path": _path_to(sem, unit.key, ekey),
            "may_be_swallowed": False,
        })
        if "public_api" not in g:
            api, route = _public_route(sem, caller)
            g["public_api"] = api  # プロジェクトのコードが呼ぶ、条件の入口に至る依存ライブラリの関数
            g["route_from_project"] = route
        if len(g["uses"]) < 5 and sym not in g["uses"]:
            g["uses"].append(sym)
        if ev.broad or getattr(eev, "broad", False):
            g["may_be_swallowed"] = True
    conds = sorted(groups.values(), key=lambda g: (-len(g["uses"]), g["entry"]))
    tags_of: dict[int, str] = {}
    for (ekey, tag), g in groups.items():
        tags_of[id(g)] = tag
    all_project_zero = True
    for g in conds:
        name = g["entry"].rstrip("()").split(".")[-1] if not g["entry"].startswith("import ") else ""
        hits = project_name_uses(sem, name) if name else []
        g["project_uses_count"] = len(hits)
        g["project_uses"] = hits[:10]
        if hits:
            all_project_zero = False
            g["project_note"] = f"プロジェクトのコードに `{name}` の呼び出し・参照が {len(hits)} 件あります（照らしてください）"
        else:
            g["project_note"] = (f"プロジェクトのコードは `{name}` を直接使っていません"
                                 + ("（到達は依存ライブラリ内の名前の一致による推定だけ）" if tags_of[id(g)] == "name" else ""))
    confidence = "high" if (all_project_zero and "branch" not in because) else "low"
    total = len(conds)
    shown = conds[:MAX_CONDITIONS]
    for g in shown:
        if g["may_be_swallowed"]:
            g["note"] = "呼び出し元が except Exception（または except:）で囲まれており、スタブの例外が握りつぶされることがあります"
    summary = ", ".join(g["entry"] for g in shown[:3]) + (f" ほか {total - 3} 件" if total > 3 else "")
    return {"conditions": shown, "total": total, "because": sorted(_TAG_TEXT.get(t, t) for t in because),
            "summary": summary, "confidence": confidence}


def project_name_uses(sem: Semantics, name: str) -> list[str]:
    """プロジェクト自身のコード（local 分類のファイル）での、その名前の呼び出し・属性参照（ファイル:行）。"""
    cache = sem.__dict__.setdefault("_name_hits", {})
    if name in cache:
        return cache[name]
    import ast as _ast
    hits: list[str] = []
    for m in sorted(sem.program.local_modules):
        info = sem.program.modules.get(m)
        if info is None or info.tree is None:
            continue
        for n in _ast.walk(info.tree):
            if (isinstance(n, _ast.Attribute) and n.attr == name) or (
                isinstance(n, _ast.Name) and n.id == name and isinstance(n.ctx, _ast.Load)
            ):
                hits.append(f"{info.path}:{getattr(n, 'lineno', 0)}")
    cache[name] = hits
    return hits
