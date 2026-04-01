"""解析オーケストレーター

全モジュールを統合し、エントリーポイントから分析結果を生成する。
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path

from src.import_graph import build_import_graph
from src.models import AnalysisResult, ImportGraph, PackageAnalysis
from src.module_resolver import ModuleResolver
from src.size_estimator import estimate_package_size
from src.usage_analyzer import analyze_packages


def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """プロジェクトを解析し、スタブ可能性を判定する。

    Args:
        entry_point: エントリーポイントファイルパス
        python_path: site-packages パス（空で自動検出）
        max_depth: import グラフの最大探索深度

    Returns:
        AnalysisResult を辞書化したもの
    """
    start = time.monotonic()

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
    analyses = analyze_packages(graph)

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
    if all_hints:
        result_dict["submodule_stub_hints"] = all_hints

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
    graph: ImportGraph, target_package: str
) -> list[list[str]]:
    """エントリーポイントからターゲットパッケージまでの import チェーンを構築する。"""
    # ターゲットパッケージのノードを見つける
    target_nodes = {
        name
        for name, node in graph.nodes.items()
        if node.top_level_package == target_package
    }

    if not target_nodes:
        return []

    # エッジから逆方向の隣接リストを構築
    reverse_adj: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.to_module not in reverse_adj:
            reverse_adj[edge.to_module] = []
        reverse_adj[edge.to_module] = reverse_adj[edge.to_module] + [edge.from_module]

    # エントリーポイントモジュールを特定
    entry_modules = {
        name for name, node in graph.nodes.items() if node.depth == 0
    }

    # DFS で全パスを探索
    chains: list[list[str]] = []
    for target in target_nodes:
        _find_paths(target, entry_modules, reverse_adj, [target], chains, max_paths=5)

    return chains


def _find_paths(
    current: str,
    targets: set[str],
    reverse_adj: dict[str, list[str]],
    path: list[str],
    results: list[list[str]],
    max_paths: int = 5,
) -> None:
    """逆方向にパスを探索する。"""
    if len(results) >= max_paths:
        return

    if current in targets:
        results.append(list(reversed(path)))
        return

    for prev in reverse_adj.get(current, []):
        if prev not in path:  # 循環防止
            _find_paths(prev, targets, reverse_adj, path + [prev], results, max_paths)
