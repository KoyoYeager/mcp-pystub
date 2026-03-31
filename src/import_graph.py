"""import グラフ構築

エントリーポイントから BFS で全 import を再帰的に解決し、グラフを構築する。
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from src.import_extractor import extract_imports
from src.models import ImportGraph, ImportGraphEdge, ImportGraphNode
from src.module_resolver import ModuleResolver


def build_import_graph(
    entry_point: str,
    project_root: str,
    resolver: ModuleResolver,
    max_depth: int = 10,
) -> ImportGraph:
    """エントリーポイントからの import グラフを BFS で構築する。

    Args:
        entry_point: 解析開始ファイルのパス
        project_root: プロジェクトルートディレクトリ
        resolver: モジュール解決器
        max_depth: 最大探索深度

    Returns:
        構築された ImportGraph
    """
    graph = ImportGraph(
        entry_point=entry_point,
        project_root=project_root,
    )

    entry = Path(entry_point).resolve()
    if not entry.is_file():
        graph.warnings = graph.warnings + [f"エントリーポイントが見つかりません: {entry_point}"]
        return graph

    # エントリーポイントのモジュール名を推定
    entry_module = _path_to_module_name(entry, Path(project_root).resolve())

    # BFS キュー: (file_path, module_name, depth)
    queue: deque[tuple[str, str, int]] = deque()
    queue.append((str(entry), entry_module, 0))

    # 訪問済みファイルパス
    visited: set[str] = set()

    while queue:
        file_path, module_name, depth = queue.popleft()

        normalized = str(Path(file_path).resolve())
        if normalized in visited:
            continue
        visited.add(normalized)

        # ノード追加
        if module_name not in graph.nodes:
            resolved_path = Path(normalized).resolve()
            classification, abs_name, top_level = resolver._classify_resolved_path(
                resolved_path, module_name
            )
            # 解決で得た絶対名で登録（相対名から修正されることがある）
            if abs_name != module_name and abs_name not in graph.nodes:
                module_name = abs_name
            graph.nodes[module_name] = ImportGraphNode(
                module_name=module_name,
                file_path=normalized,
                classification=classification,
                depth=depth,
                top_level_package=top_level,
            )

        if depth >= max_depth:
            continue

        # import 抽出
        imports, warnings = extract_imports(file_path)
        graph.warnings = graph.warnings + warnings

        for imp in imports:
            # モジュール解決
            resolution = resolver.resolve(
                module_name=imp.module_name,
                relative_level=imp.relative_level,
                source_file=file_path,
            )

            # エッジ記録用のターゲットモジュール名
            target_module = resolution.module_name or imp.module_name

            # ノードが未登録なら追加
            if target_module not in graph.nodes:
                graph.nodes[target_module] = ImportGraphNode(
                    module_name=target_module,
                    file_path=resolution.file_path,
                    classification=resolution.classification,
                    depth=depth + 1,
                    top_level_package=resolution.top_level_package,
                )

            # エッジ追加
            graph.edges = graph.edges + [
                ImportGraphEdge(
                    from_module=module_name,
                    to_module=target_module,
                    import_info=imp,
                )
            ]

            # stdlib / builtin / unresolvable は再帰しない
            if resolution.classification in ("stdlib", "builtin", "unresolvable"):
                continue

            # 解決先ファイルがあれば BFS キューに追加
            if resolution.file_path:
                resolved = str(Path(resolution.file_path).resolve())
                if resolved not in visited and resolved.endswith(".py"):
                    queue.append((resolved, target_module, depth + 1))

    return graph


def _path_to_module_name(file_path: Path, project_root: Path) -> str:
    """ファイルパスからモジュール名を推定する。"""
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        return file_path.stem

    parts = list(rel.parts)

    # __init__.py → パッケージ名
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]

    return ".".join(parts)
