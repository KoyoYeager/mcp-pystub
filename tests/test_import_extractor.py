"""import_extractor のユニットテスト"""

from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from src.import_extractor import extract_imports


def _write_temp(code: str) -> str:
    """一時ファイルにコードを書き込み、パスを返す。"""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8")
    f.write(textwrap.dedent(code))
    f.close()
    return f.name


class TestModuleLevelImport:
    def test_plain_import(self):
        path = _write_temp("import os\nimport sys\n")
        imports, warnings = extract_imports(path)
        assert len(imports) == 2
        assert imports[0].module_name == "os"
        assert imports[0].is_module_level is True
        assert imports[0].import_type == "import"

    def test_from_import(self):
        path = _write_temp("from os.path import join, exists\n")
        imports, _ = extract_imports(path)
        assert len(imports) == 1
        assert imports[0].module_name == "os.path"
        assert imports[0].names_imported == ["join", "exists"]
        assert imports[0].import_type == "from_import"

    def test_aliased_import(self):
        path = _write_temp("import pandas as pd\n")
        imports, _ = extract_imports(path)
        assert imports[0].alias == "pd"
        assert imports[0].module_name == "pandas"


class TestFunctionLevelImport:
    def test_import_inside_function(self):
        path = _write_temp("""
        def foo():
            import heavy_module
        """)
        imports, _ = extract_imports(path)
        assert len(imports) == 1
        assert imports[0].is_module_level is False
        assert imports[0].context == "function:foo"

    def test_import_inside_class(self):
        path = _write_temp("""
        class MyClass:
            import config
        """)
        imports, _ = extract_imports(path)
        assert imports[0].context == "class:MyClass"


class TestProtectedImport:
    def test_try_except_import_error(self):
        path = _write_temp("""
        try:
            import optional
        except ImportError:
            optional = None
        """)
        imports, _ = extract_imports(path)
        assert imports[0].is_protected is True

    def test_try_except_module_not_found(self):
        path = _write_temp("""
        try:
            import optional
        except ModuleNotFoundError:
            optional = None
        """)
        imports, _ = extract_imports(path)
        assert imports[0].is_protected is True

    def test_try_except_tuple(self):
        path = _write_temp("""
        try:
            import optional
        except (ImportError, ModuleNotFoundError):
            optional = None
        """)
        imports, _ = extract_imports(path)
        assert imports[0].is_protected is True

    def test_bare_except(self):
        path = _write_temp("""
        try:
            import optional
        except:
            optional = None
        """)
        imports, _ = extract_imports(path)
        assert imports[0].is_protected is True

    def test_unprotected_import(self):
        path = _write_temp("""
        try:
            x = 1 / 0
        except ZeroDivisionError:
            pass
        import not_protected
        """)
        imports, _ = extract_imports(path)
        assert imports[0].is_protected is False


class TestStarImport:
    def test_star_import_warning(self):
        path = _write_temp("from module import *\n")
        imports, warnings = extract_imports(path)
        assert imports[0].import_type == "star_import"
        assert imports[0].names_imported == ["*"]
        assert len(warnings) == 1
        assert "star import" in warnings[0]


class TestRelativeImport:
    def test_relative_import(self):
        path = _write_temp("from . import utils\n")
        imports, _ = extract_imports(path)
        assert imports[0].relative_level == 1

    def test_parent_relative_import(self):
        path = _write_temp("from ..models import User\n")
        imports, _ = extract_imports(path)
        assert imports[0].relative_level == 2
        assert imports[0].module_name == "models"


class TestDynamicImport:
    def test_importlib_import_module_literal(self):
        path = _write_temp("""
        import importlib
        mod = importlib.import_module("dynamic_module")
        """)
        imports, _ = extract_imports(path)
        # importlib の通常 import + dynamic import
        dynamic = [i for i in imports if i.import_type == "dynamic"]
        assert len(dynamic) == 1
        assert dynamic[0].module_name == "dynamic_module"

    def test_importlib_import_module_variable(self):
        path = _write_temp("""
        import importlib
        name = get_name()
        mod = importlib.import_module(name)
        """)
        _, warnings = extract_imports(path)
        dynamic_warnings = [w for w in warnings if "dynamic import" in w]
        assert len(dynamic_warnings) == 1


class TestEdgeCases:
    def test_file_not_found(self):
        imports, warnings = extract_imports("/nonexistent/path.py")
        assert imports == []
        assert len(warnings) == 1

    def test_syntax_error(self):
        path = _write_temp("def foo(\n")
        imports, warnings = extract_imports(path)
        assert imports == []
        assert len(warnings) == 1
        assert "構文エラー" in warnings[0]

    def test_empty_file(self):
        path = _write_temp("")
        imports, warnings = extract_imports(path)
        assert imports == []
        assert warnings == []
