"""使用分析 + 判定

各 third_party パッケージについて:
1. プロジェクトが直接 import しているか
2. 依存ライブラリ経由（transitive）のみか
3. gateway 関数（依存ライブラリ内でそのパッケージを使う関数）を特定
4. プロジェクトが gateway 関数を呼んでいるか

最終判定: stubbable / nofollow / required
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from src.models import (
    GatewayFunction,
    ImportGraph,
    ImportGraphEdge,
    PackageAnalysis,
)


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
    # third_party パッケージを収集
    if target_packages is None:
        target_packages = {
            node.top_level_package
            for node in graph.nodes.values()
            if node.classification == "third_party" and node.top_level_package
        }

    # ローカルモジュールの一覧
    local_modules = {
        name
        for name, node in graph.nodes.items()
        if node.classification == "local"
    }

    # パッケージごとのエッジを収集
    edges_by_target_pkg: dict[str, list[ImportGraphEdge]] = defaultdict(list)
    for edge in graph.edges:
        target_node = graph.nodes.get(edge.to_module)
        if target_node and target_node.top_level_package in target_packages:
            edges_by_target_pkg[target_node.top_level_package] = (
                edges_by_target_pkg[target_node.top_level_package] + [edge]
            )

    results: list[PackageAnalysis] = []

    for pkg in sorted(target_packages):
        edges = edges_by_target_pkg.get(pkg, [])
        analysis = _analyze_single_package(pkg, edges, graph, local_modules)
        results = results + [analysis]

    # required 伝播: required パッケージの内部呼び出しを追跡して
    # stubbable を required に格上げする
    results = _propagate_required(results, edges_by_target_pkg, graph, local_modules)

    return results


def _propagate_required(
    results: list[PackageAnalysis],
    edges_by_target_pkg: dict[str, list[ImportGraphEdge]],
    graph: ImportGraph,
    local_modules: set[str],
) -> list[PackageAnalysis]:
    """required パッケージの内部呼び出しチェーンを追跡し、
    stubbable パッケージを required に格上げする。

    ロジック（ターゲット指向）:
    1. stubbable パッケージ P の gateway 関数が所属するモジュール G を特定
    2. G を import しているモジュール M を特定（G の呼び出し元候補）
    3. M が required パッケージに属していて、M 内で gateway 関数を呼んでいれば
       P を required に格上げ
    4. 変更がなくなるまで繰り返し
    """
    changed = True
    while changed:
        changed = False
        required_pkgs = {r.package_name for r in results if r.verdict == "required"}

        for i, pkg in enumerate(results):
            if pkg.verdict != "stubbable":
                continue

            # gateway 関数がある場合: ターゲット指向で呼び出し元を確認
            if pkg.gateway_functions:
                # gateway 関数を持つモジュールを特定
                gateway_modules = {gf.module for gf in pkg.gateway_functions}

                # gateway モジュールを import しているモジュール（required パッケージ内）を特定
                callers_of_gateway: set[str] = set()
                for edge in graph.edges:
                    if edge.to_module in gateway_modules:
                        from_node = graph.nodes.get(edge.from_module)
                        if from_node and from_node.top_level_package in required_pkgs:
                            callers_of_gateway.add(edge.from_module)

                # それらのモジュール内の呼び出しを収集
                caller_calls = _collect_calls_from_modules(graph, callers_of_gateway)

                for gf in pkg.gateway_functions:
                    if not gf.called_by_project and gf.function_name in caller_calls:
                        gf.called_by_project = True

                if any(gf.called_by_project for gf in pkg.gateway_functions):
                    results[i] = PackageAnalysis(
                        package_name=pkg.package_name,
                        verdict="required",
                        reason="required パッケージの内部呼び出しチェーンで使用されています。",
                        is_directly_imported=pkg.is_directly_imported,
                        is_transitively_imported=pkg.is_transitively_imported,
                        imported_by=pkg.imported_by,
                        import_depth=pkg.import_depth,
                        gateway_functions=pkg.gateway_functions,
                        estimated_size_mb=pkg.estimated_size_mb,
                        warnings=pkg.warnings,
                    )
                    changed = True
                    continue

            # gateway 関数がない場合: required パッケージから module-level import → 安全側判定
            if not pkg.gateway_functions:
                has_module_level_from_required = False
                importing_module = ""
                for edge in edges_by_target_pkg.get(pkg.package_name, []):
                    from_node = graph.nodes.get(edge.from_module)
                    if (
                        from_node
                        and from_node.top_level_package in required_pkgs
                        and edge.import_info.is_module_level
                        and not edge.import_info.is_protected
                    ):
                        has_module_level_from_required = True
                        importing_module = edge.from_module
                        break

                if has_module_level_from_required:
                    results[i] = PackageAnalysis(
                        package_name=pkg.package_name,
                        verdict="required",
                        reason=f"required パッケージ ({importing_module}) から"
                        "モジュールレベルで import されており、"
                        "gateway 分析で使用パターンを特定できませんでした（安全側判定）。",
                        is_directly_imported=pkg.is_directly_imported,
                        is_transitively_imported=pkg.is_transitively_imported,
                        imported_by=pkg.imported_by,
                        import_depth=pkg.import_depth,
                        gateway_functions=pkg.gateway_functions,
                        estimated_size_mb=pkg.estimated_size_mb,
                        warnings=pkg.warnings + [
                            f"{pkg.package_name}: gateway 関数を特定できなかったため安全側で required 判定"
                        ],
                    )
                    changed = True

    return results


def _collect_calls_from_modules(
    graph: ImportGraph,
    module_names: set[str],
) -> set[str]:
    """指定モジュール内の全関数呼び出し名を収集する。"""
    calls: set[str] = set()

    for module_name in module_names:
        node = graph.nodes.get(module_name)
        if not node or not node.file_path or not node.file_path.endswith(".py"):
            continue

        path = Path(node.file_path)
        if not path.is_file():
            continue

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue

        for ast_node in ast.walk(tree):
            if isinstance(ast_node, ast.Call):
                name = _extract_call_name(ast_node)
                if name:
                    calls.add(name)

    return calls


def _analyze_single_package(
    package_name: str,
    edges: list[ImportGraphEdge],
    graph: ImportGraph,
    local_modules: set[str],
) -> PackageAnalysis:
    """単一パッケージの分析と判定を行う。"""
    imported_by: list[str] = []
    is_directly_imported = False
    is_transitively_imported = False
    all_protected = True  # 全 import が try/except 保護されているか
    min_depth = 999
    warnings: list[str] = []

    for edge in edges:
        from_node = graph.nodes.get(edge.from_module)
        if from_node is None:
            continue

        imported_by = imported_by + [edge.from_module]

        if from_node.classification == "local":
            is_directly_imported = True
        else:
            is_transitively_imported = True

        if not edge.import_info.is_protected:
            all_protected = False

        if from_node.depth < min_depth:
            min_depth = from_node.depth

    if not edges:
        return PackageAnalysis(
            package_name=package_name,
            verdict="required",
            reason="import エッジが見つかりません",
        )

    # 判定 1: 全 import が try/except 保護 → nofollow
    if all_protected:
        return PackageAnalysis(
            package_name=package_name,
            verdict="nofollow",
            reason="全ての import が try/except ImportError で保護されています。"
            "--nofollow-import-to で除外可能。",
            is_directly_imported=is_directly_imported,
            is_transitively_imported=is_transitively_imported,
            imported_by=_unique(imported_by),
            import_depth=min_depth + 1,
            warnings=warnings,
        )

    # 判定 2: プロジェクトコードが直接 import → gateway 分析
    # 注: パッケージ内部の自己import で is_transitively_imported も True になることがある
    #      直接importがある場合はそちらを優先して分析する
    if is_directly_imported:
        # ローカルコード内での使用状況を確認
        gateway_funcs = _find_gateway_functions_in_local(
            package_name, edges, graph, local_modules
        )
        project_uses = any(gf.called_by_project for gf in gateway_funcs)

        if project_uses:
            return PackageAnalysis(
                package_name=package_name,
                verdict="required",
                reason="プロジェクトコードが直接 import し、実際に使用しています。",
                is_directly_imported=True,
                is_transitively_imported=is_transitively_imported,
                imported_by=_unique(imported_by),
                import_depth=min_depth + 1,
                gateway_functions=gateway_funcs,
                warnings=warnings,
            )
        else:
            return PackageAnalysis(
                package_name=package_name,
                verdict="stubbable",
                reason="プロジェクトコードが import していますが、"
                "実際のコードパスでは使用されていません。",
                is_directly_imported=True,
                imported_by=_unique(imported_by),
                import_depth=min_depth + 1,
                gateway_functions=gateway_funcs,
                warnings=warnings,
            )

    # 判定 3: transitive import のみ → gateway 関数分析
    if is_transitively_imported:
        gateway_funcs = _find_gateway_functions_in_deps(
            package_name, edges, graph, local_modules
        )
        project_calls_gateway = any(gf.called_by_project for gf in gateway_funcs)

        if project_calls_gateway:
            return PackageAnalysis(
                package_name=package_name,
                verdict="required",
                reason="依存ライブラリ経由で import され、プロジェクトが "
                "gateway 関数を呼んでいます。",
                is_directly_imported=is_directly_imported,
                is_transitively_imported=True,
                imported_by=_unique(imported_by),
                import_depth=min_depth + 1,
                gateway_functions=gateway_funcs,
                warnings=warnings,
            )
        else:
            return PackageAnalysis(
                package_name=package_name,
                verdict="stubbable",
                reason="依存ライブラリ経由でのみ import され、"
                "プロジェクトは該当機能を使用していません。",
                is_transitively_imported=True,
                imported_by=_unique(imported_by),
                import_depth=min_depth + 1,
                gateway_functions=gateway_funcs,
                warnings=warnings,
            )

    # フォールバック
    return PackageAnalysis(
        package_name=package_name,
        verdict="required",
        reason="分析が完了しませんでした（安全側に判定）。",
        is_directly_imported=is_directly_imported,
        is_transitively_imported=is_transitively_imported,
        imported_by=_unique(imported_by),
        import_depth=min_depth + 1,
        warnings=warnings,
    )


def _find_gateway_functions_in_deps(
    package_name: str,
    edges: list[ImportGraphEdge],
    graph: ImportGraph,
    local_modules: set[str],
) -> list[GatewayFunction]:
    """依存ライブラリ内で package_name を使う関数を特定し、
    プロジェクトコードがそれらを呼んでいるか追跡する。"""
    gateway_funcs: list[GatewayFunction] = []

    # package_name を import している依存ライブラリのファイルを特定
    dep_files: dict[str, str] = {}  # module_name -> file_path
    for edge in edges:
        from_node = graph.nodes.get(edge.from_module)
        if (
            from_node
            and from_node.classification == "third_party"
            and from_node.file_path
            and from_node.file_path.endswith(".py")
        ):
            dep_files[edge.from_module] = from_node.file_path

    # 各依存ファイルを AST 解析して gateway 関数を特定
    for dep_module, dep_file in dep_files.items():
        funcs = _extract_functions_using_package(dep_file, package_name)
        for func_name, symbols in funcs.items():
            gateway_funcs = gateway_funcs + [
                GatewayFunction(
                    module=dep_module,
                    function_name=func_name,
                    symbols_from_package=symbols,
                    called_by_project=False,
                )
            ]

    # プロジェクトコードが gateway 関数を呼んでいるか確認
    project_calls = _collect_project_calls(graph, local_modules)
    for gf in gateway_funcs:
        if gf.function_name in project_calls:
            gf.called_by_project = True

    return gateway_funcs


def _find_gateway_functions_in_local(
    package_name: str,
    edges: list[ImportGraphEdge],
    graph: ImportGraph,
    local_modules: set[str],
) -> list[GatewayFunction]:
    """ローカルコード内で package_name のシンボルを実際に使う関数を特定する。"""
    gateway_funcs: list[GatewayFunction] = []

    # package_name を import しているローカルファイルを特定
    local_files: dict[str, str] = {}
    for edge in edges:
        from_node = graph.nodes.get(edge.from_module)
        if (
            from_node
            and from_node.classification == "local"
            and from_node.file_path
            and from_node.file_path.endswith(".py")
        ):
            local_files[edge.from_module] = from_node.file_path

    for local_module, local_file in local_files.items():
        funcs = _extract_functions_using_package(local_file, package_name)
        # module-level での使用 = required
        if "__module_level__" in funcs:
            return [
                GatewayFunction(
                    module=local_module,
                    function_name="__module_level__",
                    symbols_from_package=funcs["__module_level__"],
                    called_by_project=True,
                )
            ]

        for func_name, symbols in funcs.items():
            gateway_funcs = gateway_funcs + [
                GatewayFunction(
                    module=local_module,
                    function_name=func_name,
                    symbols_from_package=symbols,
                    called_by_project=False,
                )
            ]

    # プロジェクト内の他ファイルからの呼び出しを確認
    project_calls = _collect_project_calls(graph, local_modules)
    for gf in gateway_funcs:
        if gf.function_name in project_calls:
            gf.called_by_project = True

    return gateway_funcs


def _extract_functions_using_package(
    file_path: str, package_name: str
) -> dict[str, list[str]]:
    """ファイル内の各関数が package_name のシンボルを使用しているかを AST で解析する。

    Returns:
        {function_name: [used_symbols]} の辞書。
        モジュールレベルでの使用は "__module_level__" キーで返す。
    """
    path = Path(file_path)
    if not path.is_file():
        return {}

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return {}

    # ファイル全体（関数内含む）で package_name から import された名前を収集
    imported_names: set[str] = set()
    # 関数内で import している関数名も記録（import 自体が使用の証拠）
    functions_with_import: dict[str, list[str]] = {}
    _collect_imports_visitor = _ImportCollectorVisitor(package_name)
    _collect_imports_visitor.visit(tree)
    imported_names = _collect_imports_visitor.imported_names
    functions_with_import = _collect_imports_visitor.functions_with_import

    if not imported_names and not functions_with_import:
        return {}

    # 各関数内での使用を追跡
    visitor = _SymbolUsageVisitor(imported_names)
    visitor.visit(tree)

    # 関数内 import も usage として統合（import 自体が使用の証拠）
    result = dict(visitor.usage)
    for func_name, symbols in functions_with_import.items():
        if func_name not in result:
            result[func_name] = []
        for s in symbols:
            if s not in result[func_name]:
                result[func_name] = result[func_name] + [s]

    return result


class _ImportCollectorVisitor(ast.NodeVisitor):
    """ファイル全体（関数内含む）から特定パッケージの import を収集する。"""

    def __init__(self, package_name: str) -> None:
        self.package_name = package_name
        self.imported_names: set[str] = set()
        # 関数内で import している場合: {function_name: [imported_names]}
        self.functions_with_import: dict[str, list[str]] = {}
        self._current_function: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == self.package_name or alias.name.startswith(f"{self.package_name}."):
                name = alias.asname or alias.name
                self.imported_names.add(name)
                if self._current_function:
                    if self._current_function not in self.functions_with_import:
                        self.functions_with_import[self._current_function] = []
                    self.functions_with_import[self._current_function] = (
                        self.functions_with_import[self._current_function] + [name]
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if module == self.package_name or module.startswith(f"{self.package_name}."):
            for alias in (node.names or []):
                name = alias.asname or alias.name
                self.imported_names.add(name)
                if self._current_function:
                    if self._current_function not in self.functions_with_import:
                        self.functions_with_import[self._current_function] = []
                    self.functions_with_import[self._current_function] = (
                        self.functions_with_import[self._current_function] + [name]
                    )
        self.generic_visit(node)


class _SymbolUsageVisitor(ast.NodeVisitor):
    """関数ごとに特定シンボルの使用を追跡する。

    クラスメソッド内での使用はクラス名でも登録する。
    例: class HTTPTransport の __init__ 内で使用 → "HTTPTransport" と "__init__" 両方登録
    これにより HTTPTransport() 呼び出しと __init__ 内の使用がマッチする。
    """

    def __init__(self, target_names: set[str]) -> None:
        self.target_names = target_names
        self.usage: dict[str, list[str]] = {}
        self._current_function: str | None = None
        self._current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        old_class = self._current_class
        self._current_class = node.name
        self.generic_visit(node)
        self._current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        old = self._current_function
        self._current_function = node.name
        self.generic_visit(node)
        self._current_function = old

    def _record(self, scope: str, symbol: str) -> None:
        """使用を記録する。クラスメソッド内ならクラス名でも登録。"""
        if scope not in self.usage:
            self.usage[scope] = []
        if symbol not in self.usage[scope]:
            self.usage[scope] = self.usage[scope] + [symbol]

        # クラスメソッド内の使用 → クラス名でも登録
        # (コンストラクタ呼び出し ClassName() と __init__ 内使用をマッチさせるため)
        if self._current_class and scope != "__module_level__":
            cls = self._current_class
            if cls not in self.usage:
                self.usage[cls] = []
            if symbol not in self.usage[cls]:
                self.usage[cls] = self.usage[cls] + [symbol]

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in self.target_names:
            scope = self._current_function or "__module_level__"
            self._record(scope, node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        root = _get_attribute_root(node)
        if root in self.target_names:
            scope = self._current_function or "__module_level__"
            full_name = _get_full_attribute(node)
            self._record(scope, full_name)
        self.generic_visit(node)


def _get_attribute_root(node: ast.Attribute) -> str:
    """属性アクセスのルート名を取得する。例: a.b.c → a"""
    current = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    return ""


def _get_full_attribute(node: ast.Attribute) -> str:
    """属性アクセスの完全な名前を取得する。例: a.b.c → 'a.b.c'"""
    parts: list[str] = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        parts = [current.attr] + parts
        current = current.value
    if isinstance(current, ast.Name):
        parts = [current.id] + parts
    return ".".join(parts)


def _collect_project_calls(
    graph: ImportGraph, local_modules: set[str]
) -> set[str]:
    """プロジェクトコード内の全関数呼び出し名を収集する。"""
    calls: set[str] = set()

    for module_name in local_modules:
        node = graph.nodes.get(module_name)
        if not node or not node.file_path or not node.file_path.endswith(".py"):
            continue

        path = Path(node.file_path)
        if not path.is_file():
            continue

        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue

        for ast_node in ast.walk(tree):
            if isinstance(ast_node, ast.Call):
                name = _extract_call_name(ast_node)
                if name:
                    calls.add(name)

    return calls


def _extract_call_name(node: ast.Call) -> str:
    """関数呼び出しの名前を抽出する。"""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _unique(items: list[str]) -> list[str]:
    """重複を除去しつつ順序を保持する。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result = result + [item]
    return result
