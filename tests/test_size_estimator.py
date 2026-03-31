"""size_estimator のユニットテスト"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.size_estimator import estimate_package_size

FIXTURES = Path(__file__).parent / "fixtures"


class TestSizeEstimation:
    def test_existing_package(self):
        size = estimate_package_size(
            "heavylib",
            [str(FIXTURES / "site_packages_mock")],
        )
        assert size > 0

    def test_nonexistent_package(self):
        size = estimate_package_size(
            "nonexistent_pkg",
            [str(FIXTURES / "site_packages_mock")],
        )
        assert size == 0.0

    def test_single_file_module(self, tmp_path):
        sp = tmp_path / "sp"
        sp.mkdir()
        (sp / "single.py").write_text("x = 1\n" * 1000)
        size = estimate_package_size("single", [str(sp)])
        assert size > 0

    def test_empty_site_packages(self):
        size = estimate_package_size("anything", [])
        assert size == 0.0
