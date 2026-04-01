"""サブモジュールスタブ機能のテスト

C拡張パッケージ（qtlib = PySide6相当）の間接排除を検証する。

テストフィクスチャ構成（Mdf2CsvConverter パターンの再現）:
  gui_project/main.py → heavylib.core.CoreProcessor のみ使用
  heavylib/__init__.py → from heavylib.gui import plot（module-level）
  heavylib/gui/__init__.py → import qtlib
  qtlib/ → C拡張(.pyd)を含む → required
  → heavylib.gui をスタブ化すれば qtlib を排除可能
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.analyzer import analyze, check_usage
from src.import_graph import build_import_graph
from src.module_resolver import ModuleResolver
from src.server import generate_submodule
from src.stub_generator import generate_submodule_stubs
from src.usage_analyzer import analyze_packages

FIXTURES = Path(__file__).parent / "fixtures"
GUI_ENTRY = str(FIXTURES / "gui_project" / "main.py")
SP = str(FIXTURES / "site_packages_mock")

logger = logging.getLogger(__name__)


@pytest.fixture
def gui_graph():
    resolver = ModuleResolver(
        project_root=str(FIXTURES / "gui_project"),
        python_path=SP,
    )
    return build_import_graph(
        entry_point=GUI_ENTRY,
        project_root=str(FIXTURES / "gui_project"),
        resolver=resolver,
        max_depth=10,
    )


# --- 検出テスト ---


class TestSubmoduleHintDetection:
    """C拡張パッケージの間接排除ヒント検出"""

    def test_qtlib_detected_as_required_with_c_extensions(self, gui_graph):
        """qtlib は .pyd を含むため required"""
        results = analyze_packages(gui_graph, target_packages={"qtlib"})
        qtlib = results[0]
        assert qtlib.verdict == "required"
        assert ".pyd" in qtlib.reason or ".so" in qtlib.reason

    def test_qtlib_has_submodule_stub_hints(self, gui_graph):
        """qtlib の required 判定にサブモジュールスタブのヒントが付与される"""
        results = analyze_packages(gui_graph, target_packages={"qtlib"})
        qtlib = results[0]
        assert len(qtlib.submodule_stubs) > 0, (
            "C拡張パッケージにサブモジュールスタブのヒントが付与されていない"
        )

    def test_hint_points_to_heavylib_gui(self, gui_graph):
        """ヒントが heavylib.gui を指している"""
        results = analyze_packages(gui_graph, target_packages={"qtlib"})
        qtlib = results[0]
        submodules = [h.submodule for h in qtlib.submodule_stubs]
        assert any(
            "heavylib.gui" in s for s in submodules
        ), f"heavylib.gui が検出されていない: {submodules}"

    def test_hint_has_correct_parent_package(self, gui_graph):
        """ヒントの parent_package が heavylib"""
        results = analyze_packages(gui_graph, target_packages={"qtlib"})
        qtlib = results[0]
        hint = qtlib.submodule_stubs[0]
        assert hint.parent_package == "heavylib"
        assert hint.target_package == "qtlib"

    def test_hint_has_imported_symbols(self, gui_graph):
        """親パッケージが re-export するシンボルがヒントに含まれる"""
        results = analyze_packages(gui_graph, target_packages={"qtlib"})
        qtlib = results[0]
        hint = qtlib.submodule_stubs[0]
        # heavylib/__init__.py が from heavylib.gui import plot
        assert "plot" in hint.imported_symbols

    def test_analyze_includes_submodule_hints(self):
        """analyze ツールの出力に submodule_stub_hints が含まれる"""
        result = analyze(GUI_ENTRY, python_path=SP)
        assert "submodule_stub_hints" in result, (
            "analyze 結果に submodule_stub_hints がない"
        )
        hints = result["submodule_stub_hints"]
        assert len(hints) > 0

    def test_check_usage_shows_hint_info(self):
        """check ツールで qtlib を調べるとサブモジュールヒントが見える"""
        result = check_usage(GUI_ENTRY, "qtlib", python_path=SP)
        assert result["found"] is True
        assert result["verdict"] == "required"
        # reason にサブモジュールの言及がある
        assert "スタブ化" in result["reason"] or "間接排除" in result["reason"]


# --- スタブ生成テスト ---


class TestSubmoduleStubGeneration:
    """サブモジュールスタブの生成"""

    def test_generate_returns_files(self):
        """スタブファイルが生成される"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        assert "files" in result
        assert len(result["files"]) > 0

    def test_stub_files_are_valid_python(self):
        """生成されたスタブが valid Python"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        for path, code in result["files"].items():
            assert path.endswith(".py"), f"{path} is not a .py file"
            try:
                compile(code, path, "exec")
            except SyntaxError as e:
                pytest.fail(f"Stub {path} has syntax error: {e}")

    def test_stub_exports_required_symbols(self):
        """スタブが親パッケージの import を満たすシンボルを export"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        # heavylib/__init__.py が from heavylib.gui import plot
        all_code = "\n".join(result["files"].values())
        assert "plot" in all_code, "plot シンボルがスタブに含まれていない"

    def test_eliminated_packages_includes_qtlib(self):
        """排除されるパッケージに qtlib が含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        assert "qtlib" in result["eliminated_packages"]

    def test_has_build_instructions(self):
        """ビルド手順が含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        instructions = result["build_instructions"]
        assert "backup_commands" in instructions
        assert "install_commands" in instructions
        assert "uninstall_commands" in instructions
        assert "verify_commands" in instructions

    def test_invalid_submodule(self):
        """存在しないサブモジュールでエラー"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.nonexistent", python_path=SP
        )
        assert "error" in result

    def test_wrong_parent(self):
        """親パッケージが一致しないサブモジュールでエラー"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "pandas.core", python_path=SP
        )
        assert "error" in result


# --- サーバーツールテスト ---


class TestServerGenerateSubmodule:
    """server.py の generate_submodule ツール"""

    def test_tool_returns_result(self):
        result = generate_submodule(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        assert isinstance(result, dict)
        assert "files" in result

    def test_tool_with_invalid_entry(self):
        result = generate_submodule(
            "/nonexistent.py", "heavylib", "heavylib.gui", python_path=SP
        )
        assert "error" in result


# --- 復元安全性テスト ---


class TestRestorationSafety:
    """復元手順の安全性検証"""

    def test_backup_commands_present(self):
        """バックアップコマンドが必ず含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        backup = result["build_instructions"]["backup_commands"]
        assert len(backup) > 0
        assert any("bak" in cmd for cmd in backup)

    def test_uninstall_has_backup_restore(self):
        """復元手順にバックアップからの復元（方法1）が含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        uninstall = result["build_instructions"]["uninstall_commands"]
        assert any("bak" in cmd for cmd in uninstall), (
            "バックアップからの復元手順がない"
        )

    def test_uninstall_has_pip_fallback(self):
        """復元手順に pip フォールバック（方法2）が含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        uninstall = result["build_instructions"]["uninstall_commands"]
        assert any("pip" in cmd for cmd in uninstall), (
            "pip フォールバックがない"
        )

    def test_verify_commands_present(self):
        """検証コマンドが含まれる"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        verify = result["build_instructions"]["verify_commands"]
        assert len(verify) > 0
        assert any("import" in cmd for cmd in verify)

    def test_full_package_build_instructions_have_backup(self):
        """パッケージスタブの手順にもバックアップが含まれる"""
        from src.stub_generator import generate_stubs
        result = generate_stubs(
            str(FIXTURES / "simple_project" / "main.py"),
            "pandas",
            python_path=SP,
        )
        if "error" not in result:
            instructions = result["build_instructions"]
            assert "backup_commands" in instructions
            assert "verify_commands" in instructions


# --- 削減量ログ ---


class TestReductionLogging:
    """テスト実行時の削減量ログ出力"""

    def test_log_submodule_stub_reduction(self, caplog):
        """サブモジュールスタブの削減量をログ出力"""
        result = generate_submodule_stubs(
            GUI_ENTRY, "heavylib", "heavylib.gui", python_path=SP
        )
        original = result.get("original_size_bytes", 0)
        stub = result.get("stub_size_bytes", 0)
        reduction = original - stub if original > 0 else 0
        pct = (reduction / original * 100) if original > 0 else 0

        # テスト出力に削減量を表示
        msg = (
            f"\n{'='*60}\n"
            f"[削減量ログ] サブモジュールスタブ: heavylib.gui\n"
            f"  元サイズ:       {original:>8,} bytes\n"
            f"  スタブサイズ:   {stub:>8,} bytes\n"
            f"  削減量:         {reduction:>8,} bytes ({pct:.1f}%)\n"
            f"  排除パッケージ: {', '.join(result.get('eliminated_packages', []))}\n"
            f"  スタブファイル: {list(result.get('files', {}).keys())}\n"
            f"{'='*60}"
        )
        with caplog.at_level(logging.INFO):
            logger.info(msg)
        print(msg)  # pytest -s で確認可能

        # 基本アサーション
        assert stub < original or original == 0, "スタブが元より大きい"

    def test_log_full_analysis_reduction(self, caplog):
        """analyze 結果の全体的な削減可能性をログ出力"""
        result = analyze(GUI_ENTRY, python_path=SP)

        stubbable_pkgs = result.get("stubbable", [])
        submodule_hints = result.get("submodule_stub_hints", [])
        required_pkgs = result.get("required", [])

        lines = [
            f"\n{'='*60}",
            f"[削減量ログ] プロジェクト分析: gui_project",
            f"  分析時間: {result.get('analysis_time_ms', 0)}ms",
            f"  stubbable パッケージ: {len(stubbable_pkgs)}",
        ]
        for pkg in stubbable_pkgs:
            lines = lines + [
                f"    - {pkg['package_name']}: "
                f"{pkg.get('estimated_size_mb', 0):.1f} MB 削減可能"
            ]

        lines = lines + [
            f"  サブモジュールスタブ候補: {len(submodule_hints)}",
        ]
        for hint in submodule_hints:
            lines = lines + [
                f"    - {hint['submodule']} → {hint['target_package']} 排除可能"
            ]

        lines = lines + [
            f"  required パッケージ: {len(required_pkgs)}",
        ]
        for pkg in required_pkgs:
            sub_hints = pkg.get("submodule_stubs", [])
            suffix = f" (間接排除可能: {len(sub_hints)}件)" if sub_hints else ""
            lines = lines + [f"    - {pkg['package_name']}{suffix}"]

        lines = lines + [f"{'='*60}"]
        msg = "\n".join(lines)

        with caplog.at_level(logging.INFO):
            logger.info(msg)
        print(msg)

        # submodule_stub_hints があることを確認
        assert len(submodule_hints) > 0, "サブモジュールヒントが検出されていない"
