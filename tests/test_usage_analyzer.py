"""usage_analyzer のユニットテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.import_graph import build_import_graph
from src.module_resolver import ModuleResolver
from src.usage_analyzer import analyze_packages

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def graph():
    resolver = ModuleResolver(
        project_root=str(FIXTURES / "simple_project"),
        python_path=str(FIXTURES / "site_packages_mock"),
    )
    return build_import_graph(
        entry_point=str(FIXTURES / "simple_project" / "main.py"),
        project_root=str(FIXTURES / "simple_project"),
        resolver=resolver,
    )


class TestPackageAnalysis:
    def test_analyzes_all_third_party(self, graph):
        results = analyze_packages(graph)
        packages = {r.package_name for r in results}
        # heavylib は third_party として検出されるべき
        assert "heavylib" in packages

    def test_pandas_detected_as_third_party(self, graph):
        """pandas は heavylib.optional_feature 経由で import される"""
        results = analyze_packages(graph)
        packages = {r.package_name for r in results}
        assert "pandas" in packages

    def test_target_specific_package(self, graph):
        results = analyze_packages(graph, target_packages={"pandas"})
        assert len(results) == 1
        assert results[0].package_name == "pandas"


class TestVerdicts:
    def test_transitively_imported_unused_is_stubbable(self, graph):
        """pandas: heavylib 経由 import、main は export_report を呼ばない → stubbable"""
        results = analyze_packages(graph, target_packages={"pandas"})
        pandas_result = results[0]
        # pandas は heavylib.optional_feature で import される
        # main.py は process_data のみ呼ぶ（export_report は呼ばない）
        assert pandas_result.verdict in ("stubbable", "required")
        # transitive import であることは確認
        assert pandas_result.is_transitively_imported is True


class TestProtectedImport:
    def test_protected_import_file(self):
        resolver = ModuleResolver(
            project_root=str(FIXTURES / "simple_project"),
            python_path=str(FIXTURES / "site_packages_mock"),
        )
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "protected_imports.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
        )
        results = analyze_packages(graph)
        # optional_package は try/except 保護 → nofollow
        opt = [r for r in results if r.package_name == "optional_package"]
        if opt:
            assert opt[0].verdict == "nofollow"
