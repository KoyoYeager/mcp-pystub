"""スタブコード生成

analyzer の分析結果を元に、stubbable パッケージの最小スタブコードを生成する。
ファイル書き出しは行わない — {パス: コード} の辞書を返す。
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from src.import_graph import build_import_graph
from src.models import ImportGraph
from src.module_resolver import ModuleResolver


def generate_stubs(
    entry_point: str,
    package_name: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """stubbable パッケージのスタブコードを生成する。

    Args:
        entry_point: プロジェクトのエントリーポイント
        package_name: スタブ化するパッケージ名
        python_path: site-packages パス

    Returns:
        {
            "package": str,
            "files": {relative_path: code_content},
            "referenced_symbols": {module: [symbols]},
            "original_size_files": int,
            "stub_size_bytes": int,
        }
    """
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return {"error": f"エントリーポイントが見つかりません: {entry_point}"}

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    # import グラフ構築
    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    # パッケージの実際のファイル位置を特定
    pkg_root = _find_package_root(package_name, resolver)
    if not pkg_root:
        return {"error": f"パッケージ '{package_name}' が見つかりません"}

    # グラフ内でこのパッケージを参照しているモジュールを収集
    referenced_symbols = _collect_referenced_symbols(graph, package_name)

    # パッケージ内のモジュール構造を取得
    pkg_modules = _get_package_modules(graph, package_name)

    # スタブファイルを生成
    files = _generate_stub_files(
        package_name, pkg_root, pkg_modules, referenced_symbols
    )

    # サイズ計算
    stub_size = sum(len(code.encode("utf-8")) for code in files.values())
    original_files = _count_original_files(pkg_root)

    return {
        "package": package_name,
        "files": files,
        "referenced_symbols": {
            mod: syms for mod, syms in referenced_symbols.items()
        },
        "original_file_count": original_files,
        "stub_file_count": len(files),
        "stub_total_bytes": stub_size,
    }


def _detect_project_root(entry: Path) -> Path:
    """プロジェクトルートを推定する。"""
    current = entry.parent
    for _ in range(20):
        if (current / "pyproject.toml").exists():
            return current
        if (current / "setup.py").exists():
            return current
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return entry.parent


def _find_package_root(package_name: str, resolver: ModuleResolver) -> Path | None:
    """パッケージの site-packages 内のルートディレクトリを取得。"""
    for sp in resolver._site_packages:
        pkg_dir = sp / package_name
        if pkg_dir.is_dir():
            return pkg_dir
        # 単一ファイルモジュール
        pkg_file = sp / f"{package_name}.py"
        if pkg_file.is_file():
            return pkg_file
    return None


def _collect_referenced_symbols(
    graph: ImportGraph, package_name: str
) -> dict[str, list[str]]:
    """グラフ内でこのパッケージから参照されているシンボルを収集する。

    Returns:
        {importing_module: [referenced_symbols]} — パッケージ外のモジュールが参照するシンボル
    """
    referenced: dict[str, list[str]] = defaultdict(list)

    for edge in graph.edges:
        target_node = graph.nodes.get(edge.to_module)
        if not target_node or target_node.top_level_package != package_name:
            continue

        from_node = graph.nodes.get(edge.from_module)
        if not from_node or from_node.top_level_package == package_name:
            continue  # パッケージ内部の import はスキップ

        # from X import Y, Z の場合
        imp = edge.import_info
        if imp.names_imported:
            for name in imp.names_imported:
                if name != "*" and name not in referenced[edge.to_module]:
                    referenced[edge.to_module] = referenced[edge.to_module] + [name]
        else:
            # import X の場合 — モジュール自体が参照される
            if edge.to_module not in referenced:
                referenced[edge.to_module] = []

    return dict(referenced)


def _get_package_modules(
    graph: ImportGraph, package_name: str
) -> dict[str, str | None]:
    """グラフ内のパッケージモジュール一覧を取得。

    Returns:
        {module_name: file_path}
    """
    modules: dict[str, str | None] = {}
    for name, node in graph.nodes.items():
        if node.top_level_package == package_name and node.classification == "third_party":
            modules[name] = node.file_path
    return modules


def _generate_stub_files(
    package_name: str,
    pkg_root: Path,
    pkg_modules: dict[str, str | None],
    referenced_symbols: dict[str, list[str]],
) -> dict[str, str]:
    """スタブファイルを生成する。

    Returns:
        {relative_path: code_content}
    """
    files: dict[str, str] = {}

    if pkg_root.is_file():
        # 単一ファイルモジュール
        symbols = _collect_all_symbols(referenced_symbols)
        code = _generate_stub_code(symbols, package_name)
        files[f"{package_name}.py"] = code
        return files

    # パッケージ — サブモジュール構造を構築
    # まず、参照されているモジュールのスタブを生成
    modules_needing_stubs: set[str] = set()

    # 外部から参照されるモジュール
    for mod in referenced_symbols:
        modules_needing_stubs.add(mod)

    # パッケージ内で import されるモジュール（内部依存）
    for mod in pkg_modules:
        if mod.startswith(f"{package_name}.") or mod == package_name:
            modules_needing_stubs.add(mod)

    # 各モジュールのスタブ生成
    for mod_name in sorted(modules_needing_stubs):
        rel_path = _module_to_path(mod_name, package_name, pkg_root)

        # このモジュールに必要なシンボル
        symbols = referenced_symbols.get(mod_name, [])

        # 実際のファイルがある場合、内部 import も解析してスタブに反映
        file_path = pkg_modules.get(mod_name)
        internal_imports = []
        if file_path and Path(file_path).is_file() and Path(file_path).suffix == ".py":
            internal_imports = _extract_internal_imports(
                file_path, package_name
            )

        code = _generate_module_stub(
            mod_name, package_name, symbols, internal_imports
        )
        files[rel_path] = code

    # __init__.py が無い中間ディレクトリに空の __init__.py を追加
    dirs_needing_init: set[str] = set()
    for path in list(files.keys()):
        parts = Path(path).parts
        for i in range(1, len(parts)):
            dir_path = "/".join(parts[:i])
            init_path = f"{dir_path}/__init__.py"
            if init_path not in files:
                dirs_needing_init.add(init_path)

    for init_path in sorted(dirs_needing_init):
        files[init_path] = '"""Auto-generated stub"""\n'

    return files


def _module_to_path(
    module_name: str, package_name: str, pkg_root: Path
) -> str:
    """モジュール名をスタブ内の相対パスに変換。"""
    parts = module_name.split(".")

    # 元のファイルがパッケージ（__init__.py）かモジュール（.py）か確認
    if len(parts) == 1:
        return f"{package_name}/__init__.py"

    # サブモジュールのパス
    sub_parts = parts[1:]  # package_name 以降
    candidate = pkg_root / "/".join(sub_parts)

    if candidate.is_dir() and (candidate / "__init__.py").exists():
        return f"{package_name}/{'/'.join(sub_parts)}/__init__.py"
    else:
        return f"{package_name}/{'/'.join(sub_parts)}.py"


def _generate_stub_code(symbols: list[str], module_name: str) -> str:
    """シンボルリストからスタブコードを生成。"""
    lines = [f'"""Auto-generated stub for {module_name}"""', ""]

    if not symbols:
        return "\n".join(lines) + "\n"

    for sym in sorted(set(symbols)):
        if sym.startswith("_") and sym.endswith("_") and sym not in ("__all__", "__version__"):
            continue  # dunder は基本スキップ
        if sym[0].isupper():
            # 大文字始まり → クラスと推定
            lines = lines + [f"class {sym}: pass", ""]
        else:
            # 小文字始まり → 関数と推定
            lines = lines + [
                f"def {sym}(*args, **kwargs):",
                f'    raise RuntimeError("{module_name}.{sym} is not available in stub")',
                "",
            ]

    return "\n".join(lines) + "\n"


def _generate_module_stub(
    module_name: str,
    package_name: str,
    symbols: list[str],
    internal_imports: list[str],
) -> str:
    """個別モジュールのスタブコードを生成。"""
    lines = [f'"""Auto-generated stub for {module_name}"""', ""]

    # 内部 import を再現（パッケージ内のモジュール間依存）
    for imp in internal_imports:
        lines = lines + [imp]
    if internal_imports:
        lines = lines + [""]

    if not symbols:
        return "\n".join(lines) + "\n"

    for sym in sorted(set(symbols)):
        if sym == "*":
            continue
        if sym.startswith("_") and sym.endswith("_") and sym not in ("__all__", "__version__"):
            continue
        if sym[0].isupper():
            lines = lines + [f"class {sym}: pass", ""]
        else:
            lines = lines + [
                f"def {sym}(*args, **kwargs):",
                f'    raise RuntimeError("{module_name}.{sym} is not available in stub")',
                "",
            ]

    return "\n".join(lines) + "\n"


def _extract_internal_imports(file_path: str, package_name: str) -> list[str]:
    """ファイルからパッケージ内部の import 文を抽出する。

    スタブでも内部 import 構造を維持しないと AttributeError になるため。
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return []

    imports: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0

            # 相対 import（パッケージ内部）
            if level > 0:
                dots = "." * level
                names = ", ".join(
                    f"{a.name} as {a.asname}" if a.asname else a.name
                    for a in node.names
                )
                if module:
                    imports = imports + [f"try:\n    from {dots}{module} import {names}\nexcept ImportError:\n    pass"]
                else:
                    imports = imports + [f"try:\n    from {dots} import {names}\nexcept ImportError:\n    pass"]

            # 絶対 import でパッケージ内部のもの
            elif module.startswith(f"{package_name}."):
                names = ", ".join(
                    f"{a.name} as {a.asname}" if a.asname else a.name
                    for a in node.names
                )
                imports = imports + [f"try:\n    from {module} import {names}\nexcept ImportError:\n    pass"]

    return imports


def _collect_all_symbols(
    referenced: dict[str, list[str]]
) -> list[str]:
    """全モジュールの参照シンボルを統合。"""
    all_syms: list[str] = []
    for syms in referenced.values():
        for s in syms:
            if s not in all_syms:
                all_syms = all_syms + [s]
    return all_syms


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
