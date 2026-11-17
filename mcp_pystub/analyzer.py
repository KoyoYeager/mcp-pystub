"""解析オーケストレーター

全モジュールを統合し、エントリーポイントから分析結果を生成する。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict
from pathlib import Path

from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.models import AnalysisResult, ImportGraph, PackageAnalysis
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.size_estimator import estimate_package_size
from mcp_pystub.usage_analyzer import analyze_packages


def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
    include_small_conditional: bool = False,
    trace_files: list[str] | None = None,
) -> dict:
    """プロジェクトを解析し、スタブ可能性を判定する。

    trace_files: 実行の記録（python -m mcp_pystub.trace で作ったファイル）。渡すと各候補に観測の区分を付ける。
    analyze はプログラムを実行しない（記録ファイルを読むだけ）。

    Args:
        entry_point: エントリーポイントファイルパス
        python_path: site-packages パス（空で自動検出）
        max_depth: import グラフの最大探索深度

    Returns:
        AnalysisResult を辞書化したもの
    """
    start = time.monotonic()
    import gc
    gc_was = gc.isenabled()
    gc.disable()  # 解析中は循環参照の回収を止める（数百万の構文木ノードを何度も走査して遅くなる）
    try:
        return _analyze(entry_point, python_path, max_depth, include_small_conditional, start, trace_files)
    finally:
        if gc_was:
            gc.enable()
        gc.collect()


def _analyze(entry_point: str, python_path: str, max_depth: int, include_small_conditional: bool,
             start: float, trace_files: list[str] | None = None) -> dict:
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return asdict(AnalysisResult(
            entry_point=entry_point,
            warnings=[f"エントリーポイントが見つかりません: {entry_point}"],
        ))

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    # Phase A+B: import グラフ構築
    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    # Phase C+D: 使用分析 + 判定
    try:
        analyses = analyze_packages(graph)
    except RecursionError:
        # 解析が深すぎて終わらない: 全て required（安全側）
        tops = sorted({n.top_level_package for n in graph.nodes.values()
                       if n.classification == "third_party" and n.top_level_package})
        analyses = [PackageAnalysis(package_name=t, verdict="required",
                                    reason="解析が深すぎて完了しませんでした（安全側判定）。") for t in tops]
        graph.warnings.append("解析が深すぎて完了しなかったため、全パッケージを required にしました")

    # サイズ推定を付与
    sp_dirs = [str(sp) for sp in resolver._site_packages]
    for pkg in analyses:
        pkg.estimated_size_mb = estimate_package_size(pkg.package_name, sp_dirs)

    # 判定ごとに分類
    stubbable = [a for a in analyses if a.verdict == "stubbable"]
    nofollow = [a for a in analyses if a.verdict == "nofollow"]
    required = [a for a in analyses if a.verdict == "required"]

    elapsed_ms = int((time.monotonic() - start) * 1000)

    result = AnalysisResult(
        entry_point=str(entry),
        project_root=str(project_root),
        total_packages_analyzed=len(analyses),
        stubbable=stubbable,
        nofollow=nofollow,
        required=required,
        warnings=graph.warnings,
        analysis_time_ms=elapsed_ms,
    )
    result_dict = asdict(result)

    # サブモジュールスタブのヒントを集約して表示しやすくする
    all_hints = []
    for pkg in required:
        for hint in pkg.submodule_stubs:
            all_hints = all_hints + [asdict(hint)]
    result_dict["submodule_stub_hints"] = all_hints

    # 条件付き候補（仕様 9 節）: stubbable には混ぜない。削減量の大きい順、1 MB 未満は既定で省く
    min_mb = 0.0 if include_small_conditional else 1.0
    py_arg = f", python_path={python_path!r}" if python_path else ""
    conditional = []
    omitted = 0
    for a in sorted((a for a in analyses if a.verdict == "conditional"), key=lambda a: -a.estimated_size_mb):
        if a.estimated_size_mb < min_mb:
            omitted += 1
            continue
        d = asdict(a)
        d["estimated_savings_mb"] = round(a.estimated_size_mb, 1)
        d["how_to_check"] = (f"verify({str(entry)!r}{py_arg}, packages=[{a.package_name!r}], submodules=[], "
                             f"nofollow=[], run_entry=True)")
        conditional.append(d)
    chints = []
    for a in required:
        for h in a.conditional_submodule_stubs:
            h.estimated_savings_mb = round(a.estimated_size_mb, 1)
            if h.estimated_savings_mb < min_mb:
                omitted += 1
                continue
            d = asdict(h)
            d["how_to_check"] = (f"verify({str(entry)!r}{py_arg}, packages=[], "
                                 f"submodules=[{(h.submodule + '=>' + h.target_package)!r}], nofollow=[], run_entry=True)")
            chints.append(d)
    # 同じパッケージに全体とサブモジュールの両方を提案しない（全体が出るならサブは出さない: 仕様 7 節）
    whole = {a["package_name"] for a in result_dict["stubbable"]} | {d["package_name"] for d in conditional}
    dropped = []
    for lst in (result_dict["submodule_stub_hints"], chints):
        keep = [h for h in lst if not any(h["submodule"] == w or h["submodule"].startswith(w + ".") for w in whole)]
        dropped += [h["submodule"] for h in lst if h not in keep]
        lst[:] = keep
    # 条件が 1 件も無い条件付きヒントは出さない（どの機能を使わない前提なのか示せない）
    chints[:] = [h for h in chints if h.get("conditions")]
    if dropped:
        result_dict["warnings"] = list(result_dict.get("warnings", [])) + [
            "パッケージ全体のスタブと重なるため出さなかったサブモジュールのヒント: " + ", ".join(sorted(set(dropped)))]
    rank = {"high": 0, "low": 1, "": 2}
    conditional.sort(key=lambda d: (rank.get(d.get("confidence", ""), 2), -d["estimated_savings_mb"]))
    chints.sort(key=lambda d: (rank.get(d.get("confidence", ""), 2), -d["estimated_savings_mb"]))
    result_dict["conditional"] = conditional
    result_dict["conditional_submodule_hints"] = chints
    result_dict["conditional_omitted_small"] = omitted
    result_dict["verify_status"] = "未実施"
    if trace_files:
        _apply_trace(result_dict, trace_files, sp_dirs, required, str(entry), py_arg)
    result_dict = {"summary": _summary(result_dict, str(entry), python_path, required), **result_dict}
    result_dict["next_steps"] = [
        "手順 1: verify(entry_point, python_path, packages=[...], submodules=[...], nofollow=[...]) で、スタブに差し替えて "
        "import できることを確かめてください（既定は import だけ: グラフ上のモジュールの直下のコード（import 時に実行されるコード）は動きますが、エントリーのスクリプトはどの名前でも実行しません）。",
        "手順 2: スタブを適用してビルドし、普段のやり方で exe を試してください（スタブに触れたら原因を出して終了コード 70 で止まります）。",
        "任意: エントリーを動かしても安全な時（副作用が無い・試験用の引数や入力がある）だけ、run_entry=True や実行の記録"
        "（python -m mcp_pystub.trace）でより強く確かめられます。",
        "この結果は静的解析による候補です（実行の保証ではありません）。verify は site-packages を書き換えません。",
    ]

    return result_dict


def inspect_graph(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 5,
) -> dict:
    """import グラフを構築して可視化用データを返す。"""
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return {"error": f"エントリーポイントが見つかりません: {entry_point}"}

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    nodes = [
        {
            "module": name,
            "file_path": node.file_path,
            "classification": node.classification,
            "depth": node.depth,
            "top_level_package": node.top_level_package,
        }
        for name, node in graph.nodes.items()
    ]

    edges = [
        {
            "from": edge.from_module,
            "to": edge.to_module,
            "is_module_level": edge.import_info.is_module_level,
            "is_protected": edge.import_info.is_protected,
            "line_number": edge.import_info.line_number,
            "import_type": edge.import_info.import_type,
        }
        for edge in graph.edges
    ]

    return {
        "entry_point": str(entry),
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "total_modules": len(graph.nodes),
            "stdlib": sum(1 for n in graph.nodes.values() if n.classification == "stdlib"),
            "third_party": sum(1 for n in graph.nodes.values() if n.classification == "third_party"),
            "local": sum(1 for n in graph.nodes.values() if n.classification == "local"),
            "unresolvable": sum(1 for n in graph.nodes.values() if n.classification == "unresolvable"),
        },
        "warnings": graph.warnings,
    }


def check_usage(
    entry_point: str,
    package_name: str,
    python_path: str = "",
    max_depth: int = 5,
) -> dict:
    """特定パッケージの詳細な使用分析を返す。"""
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return {"error": f"エントリーポイントが見つかりません: {entry_point}"}

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    # パッケージがグラフ内に存在するか確認
    pkg_in_graph = any(
        node.top_level_package == package_name
        for node in graph.nodes.values()
        if node.classification == "third_party"
    )
    if not pkg_in_graph:
        return {
            "package": package_name,
            "found": False,
            "message": f"パッケージ '{package_name}' は import グラフ内に見つかりません。",
        }

    analyses = analyze_packages(graph, target_packages={package_name})

    if not analyses:
        return {
            "package": package_name,
            "found": False,
            "message": f"パッケージ '{package_name}' の分析結果がありません。",
        }

    pkg = analyses[0]
    sp_dirs = [str(sp) for sp in resolver._site_packages]
    pkg.estimated_size_mb = estimate_package_size(package_name, sp_dirs)

    # import チェーンを構築
    chains = _build_import_chains(graph, package_name)

    return {
        "package": package_name,
        "found": True,
        "verdict": pkg.verdict,
        "reason": pkg.reason,
        "is_directly_imported": pkg.is_directly_imported,
        "is_transitively_imported": pkg.is_transitively_imported,
        "imported_by": pkg.imported_by,
        "import_chains": chains,
        "gateway_functions": [asdict(gf) for gf in pkg.gateway_functions],
        "estimated_size_mb": round(pkg.estimated_size_mb, 2),
        "warnings": pkg.warnings,
    }


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


def _build_import_chains(
    graph: ImportGraph, target_package: str, max_paths: int = 5
) -> list[list[str]]:
    """エントリーポイントからターゲットパッケージまでの import チェーン（最短経路）を返す。

    訪問済み集合つきの BFS（O(V+E)）。循環の多いグラフでも指数時間にならない。
    """
    target_nodes = {
        name
        for name, node in graph.nodes.items()
        if node.top_level_package == target_package
    }
    if not target_nodes:
        return []

    adj: dict[str, list[str]] = {}
    for edge in graph.edges:
        adj.setdefault(edge.from_module, []).append(edge.to_module)

    entry_modules = [name for name, node in graph.nodes.items() if node.depth == 0]
    parent: dict[str, str | None] = {}
    queue: deque[str] = deque()
    for e in entry_modules:
        parent[e] = None
        queue.append(e)
    found: list[str] = []
    while queue and len(found) < max_paths:
        cur = queue.popleft()
        if cur in target_nodes:
            found.append(cur)
            continue  # ターゲットの内部は辿らない
        for nxt in adj.get(cur, []):
            if nxt not in parent:
                parent[nxt] = cur
                queue.append(nxt)

    chains: list[list[str]] = []
    for t in found:
        path = [t]
        while parent.get(path[-1]) is not None:
            path.append(parent[path[-1]])  # type: ignore[arg-type]
        chains.append(list(reversed(path)))
    return chains


def _apply_trace(rd: dict, trace_files: list[str], sp_dirs: list[str], required: list, entry: str, py_arg: str) -> None:
    """実行の記録を候補に付ける（第 7 回 第 2 段）。

    - 静的解析の候補が記録で executed → 「記録と矛盾」として推奨から外す（キーの一覧には残し、trace_conflict を付ける）
    - 静的解析で required だが、記録で not_imported / import_only → 「記録からの候補」（verify で確かめる）
    """
    from mcp_pystub import trace as _trace
    t = _trace.load(list(trace_files))
    conflicts: list[dict] = []

    def mark(d: dict, name: str, kind: str) -> None:
        obs = _trace.observe(t, name, sp_dirs)
        d["observed"] = obs
        if obs["state"] == "executed":
            d["trace_conflict"] = True
            conflicts.append({"item": name, "kind": kind, "functions_executed": obs["functions_executed"],
                              "top_functions": obs["top_functions"],
                              "reason": f"記録と矛盾: 実行の記録で {obs['functions_executed']} 個の関数が実行されました"
                                        f"（例: {', '.join(obs['top_functions'][:3])}）。推奨から外しました"})

    for p in rd["stubbable"]:
        mark(p, p["package_name"], "stubbable")
    for p in rd["nofollow"]:
        mark(p, p["package_name"], "nofollow")
    for p in rd["conditional"]:
        mark(p, p["package_name"], "conditional")
    for h in rd["submodule_stub_hints"]:
        mark(h, h["submodule"], "submodule_stub_hints")
    for h in rd["conditional_submodule_hints"]:
        mark(h, h["submodule"], "conditional_submodule_hints")
    cands = []
    for a in required:
        obs = _trace.observe(t, a.package_name, sp_dirs)
        if obs["state"] in ("not_imported", "import_only"):
            cands.append({"package_name": a.package_name, "estimated_size_mb": round(a.estimated_size_mb, 1),
                          "observed": obs, "static_reason": a.reason,
                          "how_to_check": f"verify({entry!r}{py_arg}, packages=[{a.package_name!r}], submodules=[], "
                                          "nofollow=[]) の後、エントリーを動かして安全なら run_entry=True で"})
    cands.sort(key=lambda d: -d["estimated_size_mb"])
    rd["trace"] = {"runs": t.get("runs", 1), "files": list(trace_files),
                   "note": "記録は動かした入力の範囲だけです。子プロセスの中と拡張モジュール（.pyd / .so）の中は記録できません"
                           "（拡張モジュールを含み関数の実行が記録に無いものは unknown_native で、未実行とは扱いません）。"}
    rd["trace_conflicts"] = conflicts
    rd["trace_candidates"] = cands


def _cond_text(conds: list[dict], total: int, complete: bool = True) -> tuple[str, int]:
    ents = [c.get("entry", "") for c in conds[:3]]
    uses = sum(int(c.get("project_uses_count", 0)) for c in conds)
    more = (f" ほか {total - 3} 件" + ("" if complete else "以上")) if total > 3 else ""
    return "、".join(ents) + more, uses


def _summary(rd: dict, entry: str, python_path: str, required: list) -> dict:
    """結果の先頭に置く要約: 使用が見つからなかった候補・条件付き（high）の削減見込みと、次の手順。"""
    proven_mb = sum(p.get("estimated_size_mb", 0.0) for p in rd["stubbable"]) + \
        sum(p.get("estimated_size_mb", 0.0) for p in rd["nofollow"])
    size_of = {a.package_name: a.estimated_size_mb for a in required}
    proven_targets = {h["target_package"] for h in rd.get("submodule_stub_hints", [])}
    proven_mb += sum(size_of.get(t, 0.0) for t in proven_targets)
    recs: list[str] = []
    for p in rd["stubbable"]:
        if p.get("trace_conflict"):
            continue
        recs.append(f"{p['package_name']} をスタブ化できます（静的解析で使用が見つからなかった候補、約 {p['estimated_size_mb']:.1f} MB）")
    for h in rd.get("submodule_stub_hints", []):
        if h.get("trace_conflict"):
            continue
        recs.append(f"{h['submodule']} をスタブ化すると {h['target_package']} を排除できます（静的解析で使用が見つからなかった候補）")
    high_mb = 0.0
    high_n = 0
    seen_targets: set[str] = set()
    cond_recs: list[str] = []
    for h in rd.get("conditional_submodule_hints", []):
        if h.get("trace_conflict"):
            continue
        text, uses = _cond_text(h.get("conditions", []), h.get("conditions_total", 0), h.get("conditions_complete", True))
        if h.get("confidence") == "high":
            if h["target_package"] not in seen_targets:
                high_mb += h.get("estimated_savings_mb", 0.0)
                seen_targets.add(h["target_package"])
            high_n += 1
        cond_recs.append((h.get("confidence"), -h.get("estimated_savings_mb", 0.0),
                          f"[{h.get('confidence')}] {h['submodule']} をスタブ化すると {h['target_package']} を排除できます"
                          f"（約 {h.get('estimated_savings_mb', 0.0):.1f} MB。条件: {text or '—'} を使わない。プロジェクト内の使用 {uses} 件）"
                          + (f" — low の理由: {h.get('confidence_reason')}" if h.get("confidence") == "low" and h.get("confidence_reason") else "")))
    for p in rd.get("conditional", []):
        if p.get("trace_conflict"):
            continue
        text, uses = _cond_text(p.get("conditions", []), p.get("conditions_total", 0), p.get("conditions_complete", True))
        if p.get("confidence") == "high":
            high_mb += p.get("estimated_savings_mb", 0.0)
            high_n += 1
        cond_recs.append((p.get("confidence"), -p.get("estimated_savings_mb", 0.0),
                          f"[{p.get('confidence')}] {p['package_name']} をスタブ化できます"
                          f"（約 {p.get('estimated_savings_mb', 0.0):.1f} MB。条件: {text or '—'} を使わない。プロジェクト内の使用 {uses} 件）"
                          + (f" — low の理由: {p.get('confidence_reason')}" if p.get("confidence") == "low" and p.get("confidence_reason") else "")))
    cond_recs.sort(key=lambda x: (0 if x[0] == "high" else 1, x[1]))
    py = f", python_path={python_path!r}" if python_path else ""
    steps = [
        f"1. verify({entry!r}{py}, packages=[...], submodules=['<サブモジュール>=><排除する C 拡張>']) で、スタブに差し替えて"
        "import できることを確かめる（既定は import だけ: グラフ上のモジュールの直下のコード（import 時のコード）は動くが、エントリーのスクリプトはどの名前でも実行しない（エントリーを間接に import するローカルのモジュールは import の対象から外し、結果の note に出す））",
        "2. スタブを適用してビルドし、普段のやり方で exe を試す（スタブに触れる経路を通ったら原因を出して終了コード 70 で止まる）",
        "（任意）エントリーを動かしても安全な時（副作用が無い・試験用の引数や入力がある）だけ、verify(..., run_entry=True) や"
        "実行の記録（python -m mcp_pystub.trace）でより強く確かめられる",
        "推奨の読み方: 上から選ぶ。[high] = プロジェクトが、入口に届く公開関数を 1 つも呼んでいない（入口の列挙が完了し、入口へ"
        "入る辺が登録・間接呼び出しの仕組みでない）。[low] = プロジェクトが入口に届く公開関数を呼んでいる・経路が間接的"
        "（条件を破る入力では終了コード 70 で止まる。理由は confidence_reason）",
        f"スタブの作り方: サブモジュールなら generate_submodule({entry!r}, <親>, <サブモジュール>{py})、パッケージなら generate(...)。"
        "適用・復元は build_instructions の手順で",
    ]
    obs = {}
    if "trace" in rd:
        obs = {"trace_conflicts": [f"{c['item']}（{c['kind']}）: {c['reason']}" for c in rd.get("trace_conflicts", [])],
               "trace_candidates": [f"{c['package_name']}（約 {c['estimated_size_mb']:.1f} MB、記録: {c['observed']['state']}）"
                                    "— 静的解析では required。verify で確かめてください" for c in rd.get("trace_candidates", [])]}
    return {
        **obs,
        "checks_done": ("静的解析" + ("、実行の記録（読み込み）" if "trace" in rd else "")
                        + "。verify は未実施（確認の状態は verify の status_summary に出ます）"),
        "proven_savings_mb": round(proven_mb, 1),
        "conditional_high_savings_mb": round(high_mb, 1),
        "conditional_high_count": high_n,
        "recommendations": [r for _, _, r in cond_recs if r.startswith("[high]")] + recs + [
            r for _, _, r in cond_recs if not r.startswith("[high]")],
        "next_steps": steps,
        "note": "stubbable / submodule_stub_hints は静的解析で使用が見つからなかった候補、[high] / [low] は条件付きの候補です。どちらも保証ではありません。実際に外せるかは verify（既定は import だけ）とビルド後の実行で確かめます。エントリーを動かして安全なら run_entry=True と実行の記録でより強く確かめられます。",
        "size_note": "MB はインストール済みのファイルの大きさ（__pycache__ を除く）で、exe の削減量ではありません"
                     "（PyInstaller が同梱する範囲・圧縮で変わります）。",
    }
