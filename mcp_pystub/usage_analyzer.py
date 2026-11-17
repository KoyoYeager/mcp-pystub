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
from collections import deque
from dataclasses import replace
from mcp_pystub.semantics import (
    Event, Program, Reachability, Ref, Unit, all_edges, certain_units, compute_reachability, entry_paths, under,
    is_external_module,
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
            from mcp_pystub.import_graph import entry_module_name
            resolver.add_local_root(entry_module_name(Path(graph.entry_point).resolve())[1])
    program = Program(resolver)
    entry_module = getattr(graph, "entry_module", "") or ""
    graph_modules: list[str] = []
    # 先に: プロジェクトのモジュールの一覧と、属性への書き込みの検出（モジュール本体のユニットを読み込み直後に
    # 作るので、順序に依らない情報を先にそろえる）
    for name, node in graph.nodes.items():
        if node.file_path and node.classification in ("local", "third_party"):
            if node.classification == "local":
                program.local_modules.add(name)
            program.prescan(name, node.file_path)
    program.eager_units = True
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
    # python -m pkg 形式: エントリーの親パッケージ（pkg/__init__.py）がエントリーより先に実行される
    for parent in getattr(graph, "entry_packages", []) or []:
        if parent not in build_roots:
            build_roots.insert(0, parent)
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
                    {m: hooks[m] for m in build_roots if m in hooks})
    sem.certain = certain_units(reach)
    sem.entries = entry_paths(reach, sem.certain)
    sem.__dict__["_parent_ev"] = pev = {}
    for src, dst, t, _ev in all_edges(reach):
        if t == "precise" and dst in sem.certain and dst not in sem.precise_parent and src != dst \
                and (src is None or src in sem.certain):
            sem.precise_parent[dst] = src
            pev[dst] = _ev
    # 解析中の注意（定数でない動的 import・pkgutil の列挙の対象不明など: 仕様 5 節の前提）を結果の warnings へ
    seen_w = set(graph.warnings)
    for w in program.warnings:
        if w not in seen_w:
            seen_w.add(w)
            graph.warnings.append(w)
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
    intro = sem.program.introspecting_tops
    for unit, ev in sem.events_by_top.get(top, []):
        if any(under(unit.module, x) for x in skip):
            continue
        if ev.kind == "import":
            if any(under(m, prefix) for m in ev.modules) or (
                ev.from_base and any(under(f"{ev.from_base}.{n}", prefix) for n in ev.names if n != "*")
            ) or (ev.from_base and under(ev.from_base, prefix)):
                out.imports.append((unit, ev))
                # `try: from X import Y / except ImportError` は Y の有無の判定（方針 2）: Y が X で無条件に
                # 束縛される（またはサブモジュール）と示せなければ、スタブでは結果が変わりうる
                if ev.protected and ev.from_base and under(ev.from_base, prefix):
                    finfo = sem.program.get(ev.from_base)
                    for n in ev.names:
                        if n == "*" or (finfo is not None and sem.program.submodule(finfo, n)):
                            continue
                        if not sem.program.name_always_bound(finfo, n):
                            out.uses.append((unit, replace(ev, ctx="presence_import"), f"{ev.from_base}.{n}<有無>"))
            continue
        if ev.kind == "ref" and ev.ctx == "annot" and intro:
            # 注釈を実行時に取り出すコード（get_type_hints / dataclasses.fields 等）が、注釈のあるモジュールと
            # 同じパッケージかプロジェクトにあれば、注釈は実行時に使われうる（方針 4）
            utop = "<project>" if unit.module in sem.program.local_modules else unit.module.split(".")[0]
            if utop in intro or "<project>" in intro or utop == "<project>":
                ev = replace(ev, ctx="annot_introspected")
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

    side = _import_side_effect(sem, package_name)
    if side:
        return PackageAnalysis(
            verdict="required",
            reason=f"{package_name} の import 時に {side} が読み込まれ、外のコードがその有無を調べています"
            "（import の副作用で別パッケージの属性が変わるため安全側判定）。",
            gateway_functions=gateways,
            **common,
        )

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
            conditions_complete=pending.get("complete", True),
            confidence_reason=pending.get("confidence_reason", ""),
            conditional_because=pending["because"],
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


def _import_side_effect(sem: Semantics, package_name: str) -> str:
    """package_name の到達済みモジュール本体が別パッケージ Q のサブモジュール Q.y を import し、
    package_name の外の到達可能なコードが Q の属性の有無を動的に調べる（hasattr 等）なら Q.y。"""
    subs: set[str] = set()
    for (m, q) in sem.reach.reached:
        if q != "<module>" or not under(m, package_name):
            continue
        for x in sem.reach.module_imports.get(m, ()):
            top = x.split(".")[0]
            if "." in x and top != package_name.split(".")[0] and not is_external_module(x):
                subs.add(x)
    if not subs:
        return ""
    for x in sorted(subs):
        parent, last = x.rsplit(".", 1)
        ptop = parent.split(".")[0]
        for unit, ev in sem.events_by_top.get(ptop, []):
            if under(unit.module, package_name) or under(unit.module, ptop) or ev.ref is None:
                continue
            on_parent = any(rr.kind == "module" and rr.module == parent for rr in ev.ref.all_refs())
            if not on_parent:
                continue
            if ev.ctx in ("hasattr", "hasattr_opaque") and ev.name == last:
                return x  # hasattr(Q, 'y'): y はこの import の副作用でだけ付く
            local = unit.module in sem.program.local_modules
            if local and (ev.kind == "dynamic" or ev.ctx == "unknown"):
                return x  # プロジェクトのコードが Q の属性を名前の分からない形で調べる
    return ""


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
            if not allow_conditional or any(is_certain_use(sem, u, ev) for u, ev, _ in uses):
                return []
            info_c = build_conditions(sem, uses, c)
            if info_c is None:
                return []
            cond_info[c] = info_c  # type: ignore[assignment]
    if allow_conditional and not cond_info:
        return []  # 使用が見つからなかったヒントとして出せる（こちらには出さない）
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
            conditions_complete=(ci or {}).get("complete", True),
            confidence_reason=((ci or {}).get("confidence_reason", "") or next(
                (f"同時にスタブにする {c2} の条件: {x.get('confidence_reason')}" for c2, x in cond_info.items()
                 if x.get("confidence") == "low" and c2 != cand), "")),
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
    "decorator": "解析できないデコレータ（飾られた関数が後で呼ばれると仮定）",
    "registry": "登録の仕組み（登録辞書・コールバック一覧・singledispatch 等に登録された関数が呼ばれると仮定）",
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


_UNRESOLVED_EV = frozenset({"dynamic", "dynamic_param", "dynamic_import", "unanalyzable"})


def _rev_index(sem: Semantics):
    """逆向きの辺と、名前での振り分けの呼び出し元を（確実 / 確実でない）に分けた索引（1 回だけ作る）。"""
    idx = sem.__dict__.get("_rev_index")
    if idx is not None:
        return idx
    rev: dict = {}
    for src, dst, t, ev in all_edges(sem.reach):
        if src is not None:
            rev.setdefault(dst, []).append((src, t, ev))
    by_name_certain: dict = {}  # 名前 → {印: (呼び出し元, イベント)}（確実な呼び出し元の代表）
    by_name_other: dict = {}  # 名前 → [確実でない呼び出し元]
    for name, callers in sem.reach.name_callers.items():
        for (src, t), ev in callers.items():
            if src in sem.certain:
                by_name_certain.setdefault(name, {}).setdefault(t, (src, ev))
            else:
                by_name_other.setdefault(name, []).append((src, t, ev))
    idx = (rev, by_name_certain, by_name_other)
    sem.__dict__["_rev_index"] = idx
    return idx


def _region_entries_multi(sem: Semantics, keys: list) -> tuple[dict, bool]:
    """確実でないユニット群 keys のどれかに届く全ての入口（多始点の逆向き探索）。

    戻り値: {(入口のユニット, 辺の印): (辺のイベント, 確実側の呼び出し元, 使用までの距離, 由来の使用ユニット)}, 列挙しきれたか
    """
    rev, by_name_certain, by_name_other = _rev_index(sem)
    out: dict = {}
    origin = {k: k for k in keys}
    dist = {k: 0 for k in keys}
    q = deque(keys)
    names_done: set[str] = set()
    complete = True
    while q:
        k = q.popleft()
        preds = rev.get(k, ())
        nm = k[1].split(".")[-1].split("#")[0] if k[1] != "<module>" else ""
        if nm:
            for t, (src, ev) in by_name_certain.get(nm, {}).items():
                if src != k and _instance_from_certain(sem, k):
                    ek = (k, t)
                    if ek not in out:
                        out[ek] = (ev, src, dist[k], origin[k], _indirect_edge(t, ev))
            if nm not in names_done:
                names_done.add(nm)
                preds = list(preds) + by_name_other.get(nm, [])
        for src, t, ev in preds:
            if src in sem.certain:
                if t == "name" and not _instance_from_certain(sem, k):
                    continue  # 名前の振り分け先のクラスのインスタンスは、この範囲に入らないと作られない
                ek = (k, t)
                if ek not in out:
                    out[ek] = (ev, src, dist[k], origin[k], _indirect_edge(t, ev))
            elif src not in dist:
                dist[src] = dist[k] + 1
                origin[src] = origin[k]
                q.append(src)
    return out, complete


_INDIRECT_TAGS = frozenset({"registry"})


def _indirect_edge(tag: str, ev) -> bool:
    """登録・間接呼び出しの仕組みの辺か（デコレータでの登録・singledispatch・登録辞書・コールバック一覧・
    引数に渡したクラス・operator.methodcaller・__init_subclass__ の登録）。"""
    if tag in _INDIRECT_TAGS:
        return True
    if getattr(ev, "ctx", "") == "methodcaller":
        return True
    return getattr(ev, "kind", "") in ("classdef", "class_escape")  # 定義時のフック・クラスの登録から


def _exact_region(sem: Semantics, keys: list) -> set:
    """使用のユニットへ、確実な辺（precise / 関数の中の分岐）だけで届くユニット（名前の振り分け等の推定を挟まない）。"""
    rev, _c, _o = _rev_index(sem)
    seen = set(keys)
    q = deque(keys)
    while q:
        k = q.popleft()
        for src, t, _ev in rev.get(k, ()):
            if src is not None and src not in seen and t in ("precise", "branch"):
                seen.add(src)
                q.append(src)
    return seen


def _region_entries(sem: Semantics, key: tuple[str, str]) -> tuple[list, bool]:
    """互換: 1 つのユニットに届く入口の一覧。"""
    out, complete = _region_entries_multi(sem, [key])
    return [(k, t, ev, src, d) for (k, t), (ev, src, d, _o, _i) in out.items()], complete


def _instance_from_certain(sem: Semantics, key: tuple[str, str]) -> bool:
    """メソッド key のクラス（またはその派生）が、確実なコードからインスタンス化されうるか。"""
    unit = sem.program.units.get(key)
    if unit is None or not unit.cls:
        return True  # 関数（モジュールの属性）: 名前で呼ばれうる
    ckey = (unit.module, unit.cls)
    cache = sem.__dict__.setdefault("_inst_certain", {})
    if ckey in cache:
        return cache[ckey]
    ok = False
    for inst, srcs in sem.reach.inst_srcs.items():
        if ckey == inst or ckey in sem.reach.mro.get(inst, ()):
            if any(s is None or s in sem.certain for s in srcs):
                ok = True
                break
    cache[ckey] = ok
    return ok


def _swallowed_on_path(sem: Semantics, src: tuple[str, str] | None, ev) -> bool:
    """入口の辺のイベントか、確実側の呼び出し元から起点までの呼び出しのどれかが、例外を握りつぶしうる枠の中か。"""
    if getattr(ev, "broad", False):
        return True
    k = src
    seen = set()
    while k is not None and k not in seen:
        seen.add(k)
        parent = sem.precise_parent.get(k)
        if parent is None:
            break
        if getattr(sem.__dict__.get("_parent_ev", {}).get(k), "broad", False):
            return True
        k = parent
    return False


def _edges_between(sem: Semantics, a, b) -> list:
    idx = sem.__dict__.get("_edges_ab")
    if idx is None:
        idx = {}
        for src, dst, t, ev in all_edges(sem.reach):
            idx.setdefault((src, dst), []).append((src, dst, t, ev))
        sem.__dict__["_edges_ab"] = idx
    return idx.get((a, b), [])


def build_conditions(sem: Semantics, uses: list, prefix: str) -> dict | None:
    """使用（全て確実ではない）から「これを使うと壊れる」入口を組み立てる。入口が分からなければ None。"""
    groups: dict = {}
    because: set[str] = set()
    complete_all = True
    sym_of: dict = {}
    region_keys: list = []
    flat: list = []
    for unit, ev, sym in uses:
        if unit.key in sem.certain and ev.cond:
            flat.append((unit.key, "branch", ev, unit.key, 0, [(ev, sym)], unit.key, False))
        else:
            if unit.key not in sym_of:
                region_keys.append(unit.key)
            sym_of.setdefault(unit.key, []).append((ev, sym))
    exact: set = set()
    if region_keys:
        exact = _exact_region(sem, region_keys)
        found, complete_all = _region_entries_multi(sem, region_keys)
        if not found:
            return None  # 入口を特定できない: 区別できない
        for (k, t), (eev, src, d, orig, indi) in found.items():
            flat.append((k, t, eev, src, d, sym_of.get(orig, []), orig, indi))
            # 依存ライブラリの中の動的な getattr などから入る辺も、入口としては列挙できている（過大近似の一種）。
            # 列挙が完了しないのは、プロジェクトのコードに解決できない同名の呼び出しがある場合（下）
    for ekey, tag, eev, caller, dist, evsyms, orig, indi in flat:
        if tag == "precise":
            return None  # 確実な辺の先が確実でない（起点から外れた）: 区別できない
        because.add(tag)
        g = groups.setdefault((ekey, tag), {
            "entry": _api_name(sem, ekey),
            "entry_location": f"{ekey[0]}:{ekey[1]}" + (f" 行 {eev.lineno}" if tag == "branch" and getattr(eev, "lineno", 0) else ""),
            "why_unproven": _TAG_TEXT.get(tag, tag),
            "dispatch_name": (getattr(eev, "name", "") if tag == "name" else ""),
            "called_from": _api_name(sem, caller) if caller is not None else "",
            "uses": [],
            "path": _path_to(sem, orig, ekey),
            "may_be_swallowed": False,
            "_key": ekey,
            "_tag": tag,
            "_dist": dist,
            "_caller": caller,
            "_exact": False,
            "indirect": False,
        })
        if tag == "branch" or ekey in exact:
            g["_exact"] = True
        if indi:
            g["indirect"] = True
        g["_dist"] = min(g["_dist"], dist)
        if "public_api" not in g:
            api, route = _public_route(sem, caller)
            g["public_api"] = api  # プロジェクトのコードが呼ぶ、条件の入口に至る依存ライブラリの関数
            g["route_from_project"] = route
        for uev, sym in evsyms[:5]:
            if len(g["uses"]) < 5 and sym not in g["uses"]:
                g["uses"].append(sym)
            if uev.broad:
                g["may_be_swallowed"] = True
        if not g["may_be_swallowed"] and _swallowed_on_path(sem, caller, eev):
            g["may_be_swallowed"] = True
    # サブモジュールのヒント: スタブにするサブモジュール自身の中の入口は 1 つにまとめる
    inner = [g for g in groups.values() if "." in prefix and under(g["_key"][0], prefix)]
    if inner:
        merged = dict(inner[0])
        merged.update({
            "entry": f"{prefix} 以下を import・使用しない",
            "entry_location": f"{prefix}（中の入口 {len(inner)} 件をまとめたもの）",
            "why_unproven": "スタブにするサブモジュール自身の中の入口",
            "dispatch_name": "", "inner_entries": len(inner),
            "uses": sorted({u for g in inner for u in g["uses"]})[:5],
            "may_be_swallowed": any(g["may_be_swallowed"] for g in inner),
            "_dist": min(g["_dist"] for g in inner),
        })
        groups = {k: g for k, g in groups.items() if g not in inner}
        groups[("#inner", prefix)] = merged
    all_project_zero = True
    unresolved_project = False
    low_reasons: list[str] = []
    for g in groups.values():
        ekey, tag = g.pop("_key"), g.pop("_tag")
        g["distance_to_use"] = g.pop("_dist")
        g["_tag2"] = tag
        if tag == "branch" and ekey in sem.certain:
            # 入口の関数は確実に実行される（プロジェクトがそこへ至る関数を呼んでいる）: 入力次第で届く
            all_project_zero = False
            api = g.get("public_api") or g["entry"]
            g["project_calls_entry"] = True
            low_reasons.append(f"プロジェクトが `{api}` を呼んでいます。入力によってはここから {prefix} に届きます"
                               f"（{g['entry']} の中の分岐）")
        caller = g.pop("_caller", None)
        exact_entry = g.pop("_exact", False)
        loc = sem.program.local_modules
        # 経路を分ける（第 7 回）: 確定した経路 = プロジェクト →（確実な辺）→ 入口へ入る呼び出し →（推定を挟まない辺）→ 使用。
        # それ以外（入口から使用までに名前だけの結び付け・暗黙の呼び出し・動的な振り分けを挟む）は推定の経路
        route = g.get("route_from_project") or []
        confirmed = bool(route) and (tag == "branch" or (exact_entry and bool(g.get("public_api"))))
        g["route_from_project"] = route if confirmed else []
        g["estimated_routes"] = [] if confirmed else ([route] if route else [])
        if tag != "branch" and exact_entry and g.get("public_api") and not g.get("project_calls_entry"):
            # 推移的（第 6 回）: 入口へ入る呼び出しのある関数に、プロジェクトが呼ぶ公開関数から確実に届く
            # （lib3.top() → lib2.run() → _dispatch() の o.plot() 等。直接の呼び出しに限らない）
            all_project_zero = False
            g["project_calls_entry"] = True
            low_reasons.append(f"プロジェクトが `{g['public_api']}` を呼んでいます。入力によってはそこから "
                               f"{_api_name(sem, caller) if caller else '?'} → {g['entry']} を経て {prefix} に届きます")
        if g.get("indirect"):
            low_reasons.append(f"{g['entry']} から {prefix} への経路に、登録・間接呼び出しの仕組み（デコレータでの登録 / "
                               "singledispatch / 登録辞書・コールバック一覧 / methodcaller / __init_subclass__ 等）があります")
        if g.get("inner_entries"):
            hits, loose = _inner_project_uses(sem, prefix)
        else:
            name = g["entry"].rstrip("()").split(".")[-1] if not g["entry"].startswith("import ") else ""
            hits, loose = project_entry_uses(sem, ekey, name)
        g["project_uses_count"] = len(hits)  # 解決した参照だけ
        g["project_uses"] = hits[:10]
        g["project_unresolved_calls"] = loose[:10]  # 受け手の分からない同名の呼び出し（解決できない呼び出し）
        if hits:
            all_project_zero = False
            g["project_note"] = f"プロジェクトのコードがこの入口を {len(hits)} か所で使っています（解決した参照）"
            low_reasons.append(f"プロジェクトが `{g['entry']}` を呼んでいます（{hits[0]}）")
        elif loose:
            unresolved_project = True
            low_reasons.append(f"プロジェクトに受け手の分からない `{name if not g.get('inner_entries') else ''}` の呼び出しがあります"
                               f"（{loose[0]}）。名前の一致で {g['entry']} に届きえます")
            g["project_note"] = (f"プロジェクトのコードに、受け手の分からない同名の呼び出しが {len(loose)} か所あります"
                                 "（解決できない呼び出し: 列挙は完了していません）")
        else:
            g["project_note"] = ("プロジェクトのコードはこの入口を直接使っていません"
                                 + ("（到達は依存ライブラリ内の名前の一致による推定だけ）" if tag == "name" else ""))
    # 表示の順: 公開 API に近い（プロジェクトから呼びやすい）順。総数は conditions_total に
    if "." in prefix:
        # サブモジュールのヒント: まとめた中の入口、次に使用に近い外の入口（Signal.plot() 等）から
        conds = sorted(groups.values(), key=lambda g: (
            0 if g.get("inner_entries") else 1, g["distance_to_use"], -len(g["uses"]), g["entry"]))
    else:
        conds = sorted(groups.values(), key=lambda g: (
            0 if g.get("public_api") else 1,
            len(g.get("route_from_project") or []) or 99,
            g["distance_to_use"], -len(g["uses"]), g["entry"]))
    total = len(conds)
    complete = complete_all and not unresolved_project
    indirect_any = any(g.get("indirect") for g in conds)
    for g in conds:
        g.pop("_tag2", None)
    confidence = "high" if (complete and all_project_zero and not indirect_any and total > 0) else "low"
    reason = ""
    if confidence == "low":
        reason = low_reasons[0] if low_reasons else "入口の列挙が完了していません"
        if len(low_reasons) > 1:
            reason += f"（ほか {len(low_reasons) - 1} 件）"
    elif any(g.get("estimated_routes") for g in conds):
        reason = "推定の経路があります。verify(run_entry=True) で確かめてください"
    if confidence == "high" and any(g.get("route_from_project") for g in conds):
        # 規則は 8c0a487 で凍結（第 7 回の判断）。high でも確定した経路が出ることがある（表示だけ。判定は変えない）
        reason = "プロジェクトから入口へ入る経路があります（high は規則による参考の表示です）。verify で確かめてください"
    shown = conds[:MAX_CONDITIONS]
    for g in shown:
        if g["may_be_swallowed"]:
            g["note"] = ("呼び出し元が except Exception / RuntimeError（または except: / contextlib.suppress）で囲まれており、"
                         "MCP_PYSTUB_STRICT=0 のときはスタブの例外が握りつぶされることがあります（既定では終了コード 70 で止まります）")
    summary = ", ".join(g["entry"] for g in shown[:3]) + (f" ほか {total - 3} 件" if total > 3 else "")
    return {"conditions": shown, "total": total, "because": sorted(_TAG_TEXT.get(t, t) for t in because),
            "summary": summary, "confidence": confidence, "complete": complete, "confidence_reason": reason}


def _inner_project_uses(sem: Semantics, prefix: str) -> tuple[list[str], list[str]]:
    """プロジェクトのコードが、サブモジュール prefix の中を解決した参照で使う場所。"""
    by_target, _by_name = _local_index(sem)
    hits: list[str] = []
    for (m, _q), where in by_target.items():
        if under(m, prefix):
            hits.extend(where)
    return sorted(set(hits)), []


def _local_index(sem: Semantics):
    idx = sem.__dict__.get("_local_ref_index")
    if idx is None:
        by_target: dict = {}
        by_name: dict = {}
        prog = sem.program
        for key in sorted(k for k in sem.reach.reached if k[0] in prog.local_modules):
            unit = prog.units.get(key)
            info = prog.modules.get(key[0])
            if unit is None or info is None:
                continue
            for ev in unit.events:
                where = f"{info.path}:{ev.lineno}"
                if ev.ref is not None:
                    for rr in ev.ref.all_refs():
                        if rr.kind in ("function", "method", "class"):
                            by_target.setdefault((rr.module, rr.target), []).append(where)
                        elif rr.kind in ("derived", "unknown") and rr.chain and ev.ctx in ("call", "decorator"):
                            # plug.make().run のような、受け手の分からない属性の呼び出し
                            last = rr.chain[-1]
                            if last not in ("()", "[]"):
                                by_name.setdefault(last, []).append(where)
                elif ev.kind == "method" and ev.ctx != "self":
                    by_name.setdefault(ev.name, []).append(where)  # 受け手が分からない呼び出し（属性の読み出しは数えない）
        idx = (by_target, by_name)
        sem.__dict__["_local_ref_index"] = idx
    return idx


def project_entry_uses(sem: Semantics, ekey: tuple[str, str], name: str) -> tuple[list[str], list[str]]:
    """プロジェクト自身のコード（到達済みの local ユニット）で、入口 ekey を指す解決した参照の場所と、
    受け手の分からない同名のメソッド呼び出し（解決できない呼び出し）の場所（ファイル:行）。"""
    by_target, by_name = _local_index(sem)
    hits = list(dict.fromkeys(by_target.get(ekey, [])))
    loose = list(dict.fromkeys(by_name.get(name, []))) if name else []
    return hits, loose


def project_name_uses(sem: Semantics, name: str) -> list[str]:
    """互換（未使用）: プロジェクトのコードでの、その名前の呼び出し・属性参照（ファイル:行）。"""
    cache = sem.__dict__.setdefault("_name_hits", {})
    if name in cache:
        return cache[name]
    import ast as _ast
    hits: list[str] = []
    for m in sorted(sem.program.local_modules):
        info = sem.program.modules.get(m)
        tree = sem.program.tree_of(info) if info is not None else None
        if tree is None:
            continue
        for n in _ast.walk(tree):
            if (isinstance(n, _ast.Attribute) and n.attr == name) or (
                isinstance(n, _ast.Name) and n.id == name and isinstance(n.ctx, _ast.Load)
            ):
                hits.append(f"{info.path}:{getattr(n, 'lineno', 0)}")
    cache[name] = hits
    return hits
