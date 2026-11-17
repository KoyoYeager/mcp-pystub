"""import グラフ構築

エントリーポイントから BFS で全 import を再帰的に解決し、グラフを構築する。
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path

from mcp_pystub.import_extractor import extract_imports
from mcp_pystub.models import ImportGraph, ImportGraphEdge, ImportGraphNode, ImportInfo, ModuleResolution
from mcp_pystub.module_resolver import ModuleResolver, is_extension_file


def _is_package_file(file_path: str | None) -> bool:
    """解決先がパッケージ（__init__.py または名前空間ディレクトリ）か。"""
    if not file_path:
        return False
    p = Path(file_path)
    if p.is_dir():
        return True
    return p.name.split(".")[0] == "__init__"


def _package_dir(file_path: str) -> Path:
    p = Path(file_path)
    return p if p.is_dir() else p.parent


def import_targets(
    imp: ImportInfo, source_file: str, resolver: ModuleResolver
) -> list[tuple[ModuleResolution, ImportInfo]]:
    """1 つの import 文が実行時に読み込むモジュールを全て返す。

    - `import a.b.c` → a, a.b, a.b.c（親パッケージの __init__ も実行される）
    - `from P import n` → P（とその親）と、n がサブモジュールなら P.n
    - 相対 import も同様

    親・サブモジュールのエッジは names_imported を空にした ImportInfo を持つ。
    """
    results: list[tuple[ModuleResolution, ImportInfo]] = []
    main = resolver.resolve(
        module_name=imp.module_name,
        relative_level=imp.relative_level,
        source_file=source_file,
    )

    # 親パッケージ（絶対名で解決できる場合）
    abs_name = main.module_name if main.classification != "unresolvable" else (
        imp.module_name if imp.relative_level == 0 else ""
    )
    if abs_name and main.classification not in ("stdlib", "builtin"):
        parts = abs_name.split(".")
        for i in range(1, len(parts)):
            parent_name = ".".join(parts[:i])
            if imp.relative_level > 0 or main.classification == "local":
                # 相対 / ローカルはファイル位置から親を求める
                parent_res = _resolve_parent_by_path(main, len(parts) - i, resolver, parent_name)
            else:
                parent_res = resolver.resolve(parent_name)
            if parent_res and parent_res.classification in ("local", "third_party"):
                results.append(
                    (parent_res, replace(imp, names_imported=[], import_type="parent_import"))
                )

    results.append((main, imp))

    # from P import n — n がサブモジュールなら P.n も読み込まれる
    if (
        imp.import_type == "from_import"
        and main.classification in ("local", "third_party")
        and _is_package_file(main.file_path)
    ):
        pkg_dir = _package_dir(main.file_path or "")
        for name in imp.names_imported:
            if name == "*":
                continue
            sub_file = resolver._resolve_path(pkg_dir / name, allow_namespace=True)
            if not sub_file:
                continue
            sub_name = f"{main.module_name}.{name}" if main.module_name else name
            classification, abs_sub, top = resolver.classify_path(sub_file, sub_name)
            results.append((
                ModuleResolution(
                    module_name=abs_sub if classification != "local" or abs_sub else sub_name,
                    file_path=sub_file,
                    classification=classification,
                    top_level_package=top,
                ),
                replace(imp, names_imported=[], import_type="submodule_import"),
            ))
    return results


def _resolve_parent_by_path(
    main: ModuleResolution, up: int, resolver: ModuleResolver, parent_name: str
) -> ModuleResolution | None:
    if not main.file_path:
        return None
    p = Path(main.file_path)
    d = p if p.is_dir() else p.parent
    if not _is_package_file(main.file_path):
        up -= 1  # モジュールファイルの親ディレクトリが直上のパッケージ
        if up < 0:
            return None
    for _ in range(up):
        d = d.parent
    f = resolver._resolve_path(d, allow_namespace=False) or resolver._resolve_path(d, allow_namespace=True)
    if not f:
        return None
    classification, abs_name, top = resolver.classify_path(f, parent_name)
    return ModuleResolution(
        module_name=abs_name or parent_name,
        file_path=f,
        classification=classification,
        top_level_package=top,
    )


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
    graph.resolver = resolver  # type: ignore[attr-defined]  # 意味解析で再利用

    entry = Path(entry_point).resolve()
    if not entry.is_file():
        graph.warnings.append(f"エントリーポイントが見つかりません: {entry_point}")
        return graph

    # スクリプトのディレクトリは sys.path[0]。パッケージの中のエントリー（python -m pkg 形式）なら
    # パッケージの外側が起点で、モジュール名は pkg.__main__ 等（相対 import を正しく解決する: 仕様 4 節）
    entry_module, pkg_root = entry_module_name(entry)
    resolver.add_local_root(pkg_root)
    graph.entry_module = entry_module
    graph.entry_packages = _parents(entry_module)  # type: ignore[attr-defined]  # -m で先に実行される親パッケージ

    # BFS キュー: (file_path, module_name, depth)
    queue: deque[tuple[str, str, int]] = deque()
    queue.append((str(entry), entry_module, 0))
    # -m 形式: 親パッケージの __init__.py も（エントリーより先に）実行される
    for i, parent in enumerate(graph.entry_packages):  # type: ignore[attr-defined]
        init = pkg_root.joinpath(*parent.split("."), "__init__.py")
        if init.is_file():
            queue.append((str(init), parent, 1))

    # 訪問済みファイルパス
    visited: set[str] = set()
    truncated: list[str] = []

    while queue:
        file_path, module_name, depth = queue.popleft()

        normalized = _resolved(file_path)
        if normalized in visited:
            continue
        visited.add(normalized)

        # ノード追加
        if module_name not in graph.nodes:
            if depth == 0:
                classification, abs_name, top_level = "local", module_name, module_name.split(".")[0]
            else:
                classification, abs_name, top_level = resolver.classify_path(
                    normalized, module_name
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

        if not normalized.endswith((".py", ".pyw")):
            continue

        if depth >= max(max_depth, 0):
            truncated.append(module_name)
            continue

        # import 抽出
        imports, warnings = extract_imports(file_path)
        graph.warnings.extend(warnings)
        if warnings and not imports and any("構文エラー" in w for w in warnings):
            graph.parse_errors.append(normalized)

        for imp in imports:
            for resolution, info in import_targets(imp, file_path, resolver):
                # エッジ記録用のターゲットモジュール名
                target_module = resolution.module_name or imp.module_name
                if not target_module:
                    continue

                # ノードが未登録なら追加
                if target_module not in graph.nodes:
                    graph.nodes[target_module] = ImportGraphNode(
                        module_name=target_module,
                        file_path=resolution.file_path,
                        classification=resolution.classification,
                        depth=depth + 1,
                        top_level_package=resolution.top_level_package,
                    )

                graph.edges.append(
                    ImportGraphEdge(
                        from_module=module_name,
                        to_module=target_module,
                        import_info=info,
                    )
                )

                # stdlib / builtin / unresolvable は再帰しない
                if resolution.classification in ("stdlib", "builtin", "unresolvable"):
                    continue
                # if TYPE_CHECKING: 内は実行時に import されない（エッジは残す:
                # PyInstaller はバイトコード上の import を拾うため同梱され、スタブ化の候補になる）
                if imp.is_type_checking:
                    continue

                # 解決先ファイルがあれば BFS キューに追加
                if resolution.file_path:
                    resolved = _resolved(resolution.file_path)
                    if resolved not in visited and (
                        resolved.endswith((".py", ".pyw")) or is_extension_file(resolved)
                    ):
                        queue.append((resolved, target_module, depth + 1))

    graph.truncated = truncated
    if truncated:
        sample = ", ".join(truncated[:5])
        graph.warnings.append(
            f"max_depth={max_depth} で {len(truncated)} モジュールの探索を打ち切りました"
            f"（例: {sample}）。打ち切り先の import はグラフに含まれません"
            "（使用判定は打ち切りに依存せず到達可能なコード全体で行います）"
        )
    graph.warnings.extend(w for w in resolver.warnings if w not in graph.warnings)
    return graph


_RESOLVED: dict[str, str] = {}


def _resolved(path: str) -> str:
    """Path.resolve() の結果を覚える（同じファイルへの import が何万回も出る）。"""
    r = _RESOLVED.get(path)
    if r is None:
        r = str(Path(path).resolve())
        if len(_RESOLVED) > 200000:
            _RESOLVED.clear()
        _RESOLVED[path] = r
    return r


def _parents(module: str) -> list[str]:
    parts = module.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


def entry_module_name(entry: Path) -> tuple[str, Path]:
    """エントリーのモジュール名と、sys.path に入る起点のディレクトリ。

    エントリーが __init__.py のあるディレクトリにあり、`__main__.py` であるか相対 import を含む場合は
    `python -m pkg.mod` で実行されるものとして扱う（そうでなければ相対 import は実行時に失敗する）。
    """
    d = entry.parent
    parts: list[str] = []
    while (d / "__init__.py").is_file() and d.name.isidentifier():
        parts.insert(0, d.name)
        if d.parent == d:
            break
        d = d.parent
    if parts:
        as_module = entry.name == "__main__.py"
        if not as_module:
            try:
                import ast as _ast
                from mcp_pystub.import_extractor import parse_file
                tree, _err = parse_file(str(entry))
                as_module = tree is not None and any(
                    isinstance(n, _ast.ImportFrom) and (n.level or 0) > 0 for n in _ast.walk(tree))
            except Exception:  # noqa: BLE001
                as_module = False
        if as_module:
            stem = entry.stem
            return ".".join(parts + ([stem] if stem != "__init__" else [])), d
    return entry.stem, entry.parent


def _path_to_module_name(file_path: Path, project_root: Path) -> str:
    """ファイルパスからモジュール名を推定する。"""
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        return file_path.stem

    parts = list(rel.parts)

    # __init__.py → パッケージ名
    if parts and parts[-1] in ("__init__.py", "__init__.pyw"):
        parts = parts[:-1]
    elif parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    elif parts and parts[-1].endswith(".pyw"):
        parts[-1] = parts[-1][:-4]

    return ".".join(parts) or file_path.stem
