"""analyzer 統合テスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analyzer import analyze, inspect_graph, check_usage

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY = str(FIXTURES / "simple_project" / "main.py")
SP = str(FIXTURES / "site_packages_mock")


class TestAnalyze:
    def test_returns_dict(self):
        result = analyze(ENTRY, python_path=SP)
        assert isinstance(result, dict)
        assert "stubbable" in result
        assert "nofollow" in result
        assert "required" in result
        assert "warnings" in result

    def test_has_analysis_time(self):
        result = analyze(ENTRY, python_path=SP)
        assert result["analysis_time_ms"] >= 0

    def test_nonexistent_entry(self):
        result = analyze("/nonexistent.py", python_path=SP)
        assert len(result["warnings"]) > 0

    def test_detects_packages(self):
        result = analyze(ENTRY, python_path=SP)
        all_packages = (
            [p["package_name"] for p in result["stubbable"]]
            + [p["package_name"] for p in result["nofollow"]]
            + [p["package_name"] for p in result["required"]]
        )
        assert len(all_packages) > 0


class TestInspectGraph:
    def test_returns_nodes_and_edges(self):
        result = inspect_graph(ENTRY, python_path=SP)
        assert "nodes" in result
        assert "edges" in result
        assert "stats" in result
        assert len(result["nodes"]) > 0

    def test_stats_are_present(self):
        result = inspect_graph(ENTRY, python_path=SP)
        stats = result["stats"]
        assert "total_modules" in stats
        assert "third_party" in stats
        assert "local" in stats

    def test_nonexistent_entry(self):
        result = inspect_graph("/nonexistent.py", python_path=SP)
        assert "error" in result


class TestCheckUsage:
    def test_existing_package(self):
        result = check_usage(ENTRY, "heavylib", python_path=SP)
        assert result["found"] is True
        assert result["verdict"] in ("stubbable", "nofollow", "required")

    def test_nonexistent_package(self):
        result = check_usage(ENTRY, "nonexistent_xyz", python_path=SP)
        assert result["found"] is False

    def test_has_import_chains(self):
        result = check_usage(ENTRY, "heavylib", python_path=SP)
        assert "import_chains" in result
