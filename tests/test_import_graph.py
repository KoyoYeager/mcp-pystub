"""import_graph のユニットテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.import_graph import build_import_graph
from src.module_resolver import ModuleResolver

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def resolver():
    return ModuleResolver(
        project_root=str(FIXTURES / "simple_project"),
        python_path=str(FIXTURES / "site_packages_mock"),
    )


class TestGraphConstruction:
    def test_builds_from_entry_point(self, resolver):
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "main.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
        )
        assert len(graph.nodes) > 0
        assert len(graph.edges) > 0

    def test_entry_point_is_local(self, resolver):
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "main.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
        )
        # エントリーポイントはローカル
        entry_nodes = [n for n in graph.nodes.values() if n.depth == 0]
        assert len(entry_nodes) >= 1
        assert entry_nodes[0].classification == "local"

    def test_detects_third_party(self, resolver):
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "main.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
        )
        third_party = {
            n.top_level_package
            for n in graph.nodes.values()
            if n.classification == "third_party"
        }
        assert "heavylib" in third_party

    def test_max_depth_limits_recursion(self, resolver):
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "main.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
            max_depth=1,
        )
        max_recorded = max(n.depth for n in graph.nodes.values())
        # depth 1 までのノード + depth 2 のノード（エッジは記録されるが再帰停止）
        assert max_recorded <= 2

    def test_nonexistent_entry_point(self, resolver):
        graph = build_import_graph(
            entry_point="/nonexistent.py",
            project_root="/tmp",
            resolver=resolver,
        )
        assert len(graph.warnings) > 0


class TestEdgeMetadata:
    def test_edges_have_import_info(self, resolver):
        graph = build_import_graph(
            entry_point=str(FIXTURES / "simple_project" / "main.py"),
            project_root=str(FIXTURES / "simple_project"),
            resolver=resolver,
        )
        for edge in graph.edges:
            assert edge.import_info is not None
            assert edge.import_info.line_number > 0
