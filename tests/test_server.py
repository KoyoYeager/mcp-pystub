"""MCP サーバーツールのテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.server import analyze, check, graph, generate

FIXTURES = Path(__file__).parent / "fixtures"
ENTRY = str(FIXTURES / "simple_project" / "main.py")
SP = str(FIXTURES / "site_packages_mock")


class TestAnalyze:
    def test_returns_structured_result(self):
        result = analyze(ENTRY, python_path=SP)
        assert isinstance(result, dict)
        assert "stubbable" in result
        assert "nofollow" in result
        assert "required" in result

    def test_with_max_depth(self):
        result = analyze(ENTRY, python_path=SP, max_depth=2)
        assert isinstance(result, dict)


class TestGraph:
    def test_returns_graph_data(self):
        result = graph(ENTRY, python_path=SP)
        assert "nodes" in result
        assert "edges" in result

    def test_respects_max_depth(self):
        result = graph(ENTRY, python_path=SP, max_depth=1)
        assert isinstance(result, dict)


class TestCheck:
    def test_returns_usage_data(self):
        result = check(ENTRY, "heavylib", python_path=SP)
        assert "verdict" in result

    def test_unknown_package(self):
        result = check(ENTRY, "xyz_nonexistent", python_path=SP)
        assert result["found"] is False


class TestGenerate:
    def test_returns_stub_data(self):
        result = generate(ENTRY, "pandas", python_path=SP)
        assert isinstance(result, dict)
