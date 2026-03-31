"""module_resolver のユニットテスト"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.module_resolver import ModuleResolver


@pytest.fixture
def project_dir(tmp_path):
    """テスト用プロジェクトディレクトリ"""
    # ローカルモジュール
    (tmp_path / "myapp").mkdir()
    (tmp_path / "myapp" / "__init__.py").write_text("")
    (tmp_path / "myapp" / "utils.py").write_text("# utils")
    (tmp_path / "main.py").write_text("# main")
    return tmp_path


@pytest.fixture
def site_packages(tmp_path):
    """テスト用 site-packages"""
    sp = tmp_path / "site-packages"
    sp.mkdir()
    # third_party パッケージ
    (sp / "requests").mkdir()
    (sp / "requests" / "__init__.py").write_text("# requests")
    (sp / "click.py").write_text("# click single file")
    return sp


@pytest.fixture
def resolver(project_dir, site_packages):
    return ModuleResolver(
        project_root=str(project_dir),
        python_path=str(site_packages),
    )


class TestStdlibClassification:
    def test_os_is_stdlib(self, resolver):
        result = resolver.resolve("os")
        assert result.classification == "stdlib"

    def test_sys_is_stdlib(self, resolver):
        result = resolver.resolve("sys")
        assert result.classification == "stdlib"

    def test_ast_is_stdlib(self, resolver):
        result = resolver.resolve("ast")
        assert result.classification == "stdlib"

    def test_os_path_is_stdlib(self, resolver):
        result = resolver.resolve("os.path")
        assert result.classification == "stdlib"
        assert result.top_level_package == "os"


class TestLocalClassification:
    def test_local_package(self, resolver):
        result = resolver.resolve("myapp")
        assert result.classification == "local"
        assert result.file_path is not None

    def test_local_module(self, resolver):
        result = resolver.resolve("myapp.utils")
        assert result.classification == "local"

    def test_local_single_file(self, resolver):
        result = resolver.resolve("main")
        assert result.classification == "local"


class TestThirdPartyClassification:
    def test_third_party_package(self, resolver):
        result = resolver.resolve("requests")
        assert result.classification == "third_party"
        assert result.file_path is not None

    def test_third_party_single_file(self, resolver):
        result = resolver.resolve("click")
        assert result.classification == "third_party"


class TestUnresolvable:
    def test_nonexistent_module(self, resolver):
        result = resolver.resolve("nonexistent_xyz_123")
        # find_spec フォールバックでも見つからない → unresolvable
        assert result.classification in ("unresolvable", "third_party", "stdlib")


class TestRelativeImport:
    def test_relative_import_resolves(self, project_dir, site_packages):
        resolver = ModuleResolver(
            project_root=str(project_dir),
            python_path=str(site_packages),
        )
        init_path = str(project_dir / "myapp" / "__init__.py")
        result = resolver.resolve("utils", relative_level=1, source_file=init_path)
        assert result.file_path is not None
        assert result.classification == "local"
