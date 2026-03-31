"""stub_generator のテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.stub_generator import generate_stubs

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY = str(FIXTURES / "simple_project" / "main.py")
SP = str(FIXTURES / "site_packages_mock")


class TestGenerateStubs:
    def test_returns_dict(self):
        result = generate_stubs(ENTRY, "pandas", python_path=SP)
        assert isinstance(result, dict)

    def test_has_files(self):
        result = generate_stubs(ENTRY, "pandas", python_path=SP)
        assert "files" in result
        assert isinstance(result["files"], dict)

    def test_stub_files_are_valid_python(self):
        result = generate_stubs(ENTRY, "pandas", python_path=SP)
        for path, code in result["files"].items():
            assert path.endswith(".py"), f"{path} is not a .py file"
            # コードがパース可能か
            try:
                compile(code, path, "exec")
            except SyntaxError as e:
                pytest.fail(f"Stub {path} has syntax error: {e}")

    def test_nonexistent_package(self):
        result = generate_stubs(ENTRY, "nonexistent_xyz", python_path=SP)
        assert "error" in result

    def test_nonexistent_entry(self):
        result = generate_stubs("/nonexistent.py", "pandas", python_path=SP)
        assert "error" in result

    def test_has_metadata(self):
        result = generate_stubs(ENTRY, "pandas", python_path=SP)
        if "error" not in result:
            assert "stub_total_bytes" in result
            assert "original_file_count" in result
            assert result["stub_total_bytes"] > 0


class TestStubFromServer:
    def test_server_tool(self):
        from src.server import generate
        result = generate(ENTRY, "pandas", python_path=SP)
        assert isinstance(result, dict)
