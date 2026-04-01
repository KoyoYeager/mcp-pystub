"""AST ベース import 抽出

単一の Python ファイルから全 import 文をコンテキスト付きで抽出する。
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.models import ImportInfo


class _ImportVisitor(ast.NodeVisitor):
    """AST を走査して import 文を収集する NodeVisitor。"""

    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.imports: list[ImportInfo] = []
        self.warnings: list[str] = []

        # コンテキスト追跡用スタック
        self._context_stack: list[str] = ["module"]
        self._protected_depth: int = 0  # try/except ImportError のネスト数

    @property
    def _context(self) -> str:
        return self._context_stack[-1]

    @property
    def _is_module_level(self) -> bool:
        return self._context == "module"

    @property
    def _is_protected(self) -> bool:
        return self._protected_depth > 0

    # --- import 文の処理 ---

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports = self.imports + [
                ImportInfo(
                    module_name=alias.name,
                    alias=alias.asname,
                    is_module_level=self._is_module_level,
                    is_protected=self._is_protected,
                    context=self._context,
                    line_number=node.lineno,
                    import_type="import",
                    source_file=self.source_file,
                )
            ]
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        level = node.level or 0
        names = [a.name for a in node.names] if node.names else []
        import_type = "star_import" if "*" in names else "from_import"

        if import_type == "star_import":
            self.warnings = self.warnings + [
                f"{self.source_file}:{node.lineno}: star import 'from {module} import *' — "
                "静的解析で参照名を特定できません"
            ]

        self.imports = self.imports + [
            ImportInfo(
                module_name=module,
                names_imported=names,
                is_module_level=self._is_module_level,
                is_protected=self._is_protected,
                context=self._context,
                line_number=node.lineno,
                import_type=import_type,
                relative_level=level,
                source_file=self.source_file,
            )
        ]
        self.generic_visit(node)

    # --- コンテキスト追跡 ---

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._context_stack = self._context_stack + [f"function:{node.name}"]
        self.generic_visit(node)
        self._context_stack = self._context_stack[:-1]

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._context_stack = self._context_stack + [f"function:{node.name}"]
        self.generic_visit(node)
        self._context_stack = self._context_stack[:-1]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._context_stack = self._context_stack + [f"class:{node.name}"]
        self.generic_visit(node)
        self._context_stack = self._context_stack[:-1]

    # --- try/except ImportError 検出 ---

    def visit_Try(self, node: ast.Try) -> None:
        catches_import_error = self._handlers_catch_import_error(node.handlers)

        if catches_import_error:
            self._protected_depth += 1

        # body を走査（import はここにある）
        for child in node.body:
            self.visit(child)

        if catches_import_error:
            self._protected_depth -= 1

        # handlers, orelse, finalbody も走査
        for handler in node.handlers:
            self.visit(handler)
        for child in node.orelse:
            self.visit(child)
        for child in node.finalbody:
            self.visit(child)

    # Python 3.11+ の try* (ExceptionGroup)
    def visit_TryStar(self, node: ast.TryStar) -> None:  # type: ignore[attr-defined]
        self.visit_Try(node)  # type: ignore[arg-type]

    def _handlers_catch_import_error(
        self, handlers: list[ast.ExceptHandler]
    ) -> bool:
        """handler が ImportError または ModuleNotFoundError を catch するか。"""
        import_errors = {"ImportError", "ModuleNotFoundError"}

        for handler in handlers:
            if handler.type is None:
                # bare except: はすべてを catch する
                return True
            if isinstance(handler.type, ast.Name) and handler.type.id in import_errors:
                return True
            if isinstance(handler.type, ast.Tuple):
                for elt in handler.type.elts:
                    if isinstance(elt, ast.Name) and elt.id in import_errors:
                        return True
        return False

    # --- dynamic import 検出 ---

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_importlib_call(node):
            self._handle_dynamic_import(node)
        self.generic_visit(node)

    def _is_importlib_call(self, node: ast.Call) -> bool:
        """importlib.import_module(...) の呼び出しか判定。"""
        func = node.func
        # importlib.import_module(...)
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            return True
        # __import__(...)
        if isinstance(func, ast.Name) and func.id == "__import__":
            return True
        return False

    def _handle_dynamic_import(self, node: ast.Call) -> None:
        """dynamic import の引数からモジュール名を抽出する。"""
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(
            node.args[0].value, str
        ):
            module_name = node.args[0].value
            self.imports = self.imports + [
                ImportInfo(
                    module_name=module_name,
                    is_module_level=self._is_module_level,
                    is_protected=self._is_protected,
                    context=self._context,
                    line_number=node.lineno,
                    import_type="dynamic",
                    source_file=self.source_file,
                )
            ]
        else:
            self.warnings = self.warnings + [
                f"{self.source_file}:{node.lineno}: dynamic import — "
                "引数が文字列リテラルではないため静的解析できません"
            ]


def extract_imports(file_path: str) -> tuple[list[ImportInfo], list[str]]:
    """Python ファイルから全 import 文を抽出する。

    Returns:
        (imports, warnings) のタプル
    """
    path = Path(file_path)
    if not path.is_file():
        return [], [f"ファイルが見つかりません: {file_path}"]

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [], [f"ファイル読み込みエラー: {file_path}: {e}"]

    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as e:
        return [], [f"構文エラー: {file_path}:{e.lineno}: {e.msg}"]

    visitor = _ImportVisitor(source_file=str(path.resolve()))
    try:
        visitor.visit(tree)
    except RecursionError:
        return visitor.imports, visitor.warnings + [
            f"{file_path}: AST が深すぎるため一部の解析をスキップしました"
        ]

    return visitor.imports, visitor.warnings
