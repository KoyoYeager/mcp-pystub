"""バグ報告（H1〜H14, M1〜M13, L1〜L7）の回帰テスト

各テストは tmp_path に架空の site-packages とプロジェクトを組み立て、
判定を確かめる。stubbable なものは生成スタブに差し替えて実際に実行し、
元と同じ出力になることまで確かめる。
"""

from __future__ import annotations

import importlib.machinery
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from mcp_pystub.analyzer import analyze, check_usage, inspect_graph
from mcp_pystub.import_extractor import extract_imports
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs

# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------

HX = {
    "heavyx/__init__.py": """
        from .core import *
        from . import sub, plotting
        __version__ = "1.0"
        class DataFrame:
            def __init__(self, *a): self.a = a
        class Timestamp: pass
        class ndarray: pass
        MAX_SIZE = 10
        def compute(): return 42
        def read(): return [1]
        def make(): return 1
        def frame(): return 1
        def jit(f): return f
    """,
    "heavyx/core.py": "CORE = 1\n",
    "heavyx/sub.py": "def f(): return 'sub-ok'\n",
    "heavyx/plotting.py": "DEFAULT_STYLE = 'x'\n",
}
HY = {"heavyy.py": "def compute():\n    return 42\n"}


def _write(root: Path, files: dict) -> None:
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(code, bytes):
            p.write_bytes(code)
        else:
            p.write_text(textwrap.dedent(code), encoding="utf-8")


class Scenario:
    def __init__(self, tmp_path: Path, sp_files: dict, proj_files: dict, entry: str = "main.py") -> None:
        self.root = tmp_path
        self.sp = tmp_path / "sp"
        self.proj = tmp_path / "proj"
        self.sp.mkdir(parents=True, exist_ok=True)
        self.proj.mkdir(parents=True, exist_ok=True)
        (self.proj / "pyproject.toml").write_text("[project]\nname='x'\n")
        _write(self.sp, sp_files)
        _write(self.proj, proj_files)
        self.entry = str(self.proj / entry)

    def run(self, site: Path | None = None) -> tuple[int, str]:
        site = site or self.sp
        code = (
            "import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')"
            % ([str(self.proj), str(site)], self.entry)
        )
        r = subprocess.run(
            [sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60
        )
        return r.returncode, (r.stdout + r.stderr).strip()

    def analyze(self) -> dict:
        return analyze(self.entry, python_path=str(self.sp))

    def verdicts(self) -> dict[str, str]:
        res = self.analyze()
        return {p["package_name"]: k for k in ("stubbable", "nofollow", "required") for p in res[k]}

    def stub_site(self, files: dict[str, str], remove: list[str]) -> Path:
        ssp = self.root / f"stubsp_{len(list(self.root.glob('stubsp_*')))}"
        shutil.copytree(self.sp, ssp)
        for r in remove:
            t = ssp / r
            if t.is_dir():
                shutil.rmtree(t)
            elif t.exists():
                t.unlink()
        for rel, code in files.items():
            compile(code, rel, "exec")
            p = ssp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(code, encoding="utf-8")
        return ssp

    def stub_and_run(self, pkg: str) -> tuple[int, str, dict]:
        g = generate_stubs(self.entry, pkg, python_path=str(self.sp))
        assert "error" not in g, g
        ssp = self.stub_site(g["files"], [pkg, f"{pkg}.py"])
        rc, out = self.run(ssp)
        return rc, out, g

    def assert_stub_equivalent(self, pkg: str) -> dict:
        rc0, out0 = self.run()
        assert rc0 == 0, out0
        rc, out, g = self.stub_and_run(pkg)
        assert rc == 0, out
        assert out == out0
        return g


def lib(code: str) -> dict:
    d = dict(HX)
    d["libA/__init__.py"] = code
    return d


# ---------------------------------------------------------------------------
# H1: 呼び出し以外の参照（属性・isinstance・注釈）はスタブを充実させて通す
# ---------------------------------------------------------------------------


class TestH1AttributeReferences:
    def test_module_level_attribute_alias(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx
            DEFAULT = heavyx.Timestamp
            def convert(): return heavyx.frame()
        """), {"main.py": "import libA\nprint('ok', libA.DEFAULT is not None)\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_isinstance_with_module_attribute(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx as hx
            def check(x): return isinstance(x, hx.DataFrame)
            def to_df(x): return hx.DataFrame(x)
        """), {"main.py": "import libA\nprint('ok', libA.check(1))\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_annotation_evaluated_at_def_time(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx as hx
            def f(x: hx.DataFrame) -> None: return None
            def g(): return hx.compute()
        """), {"main.py": "import libA\nprint('ok')\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_default_argument_call_is_required(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx
            def f(x=heavyx.make()): return x
        """), {"main.py": "import libA\nprint('ok')\n"})
        assert s.verdicts()["heavyx"] == "required"

    def test_alias_then_call_is_required(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx
            _c = heavyx.compute
            VALUE = _c()
        """), {"main.py": "import libA\nprint('ok', libA.VALUE)\n"})
        assert s.verdicts()["heavyx"] == "required"


# ---------------------------------------------------------------------------
# H2 / H3: 到達解析
# ---------------------------------------------------------------------------


class TestH2CallChains:
    def test_init_calls_private_method(self, tmp_path):
        sp = dict(HX)
        sp["libA/__init__.py"] = "from .core import MDF\n"
        sp["libA/core.py"] = """
            import heavyx
            class MDF:
                def __init__(self):
                    self._load()
                def _load(self):
                    self.d = heavyx.read()
                def to_frame(self):
                    return heavyx.DataFrame(self.d)
        """
        s = Scenario(tmp_path, sp, {"main.py": "from libA import MDF\nm = MDF()\nprint('ok', m.d)\n"})
        assert s.verdicts()["heavyx"] == "required"

    def test_unused_method_keeps_package_stubbable(self, tmp_path):
        """README の asammdf→pandas パターン: 使わないメソッドだけが使う → stubbable"""
        sp = dict(HX)
        sp["libA/__init__.py"] = "from .core import MDF\n"
        sp["libA/core.py"] = """
            import heavyx
            class MDF:
                def __init__(self):
                    self.d = [1]
                def get(self):
                    return self.d
                def to_dataframe(self):
                    return heavyx.DataFrame(self.d)
        """
        s = Scenario(tmp_path, sp, {"main.py": "from libA import MDF\nprint('ok', MDF().get())\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_cross_module_chain(self, tmp_path):
        sp = dict(HX)
        sp["libA/__init__.py"] = "from .api import top\n"
        sp["libA/api.py"] = "from .helpers import helper\ndef top():\n    return helper()\n"
        sp["libA/helpers.py"] = "import heavyx\ndef helper():\n    return heavyx.compute()\n"
        s = Scenario(tmp_path, sp, {"main.py": "import libA\nprint('ok', libA.top())\n"})
        assert s.verdicts()["heavyx"] == "required"


class TestH3CallbacksAndDunders:
    def test_thread_target_callback(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": """
            import threading
            import heavyx
            def work():
                print('ok', heavyx.compute())
            t = threading.Thread(target=work); t.start(); t.join()
        """})
        assert s.verdicts()["heavyx"] == "required"

    def test_enter_method(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": """
            import heavyx
            class C:
                def __enter__(self):
                    self.v = heavyx.compute(); return self
                def __exit__(self, *a): pass
            with C() as c:
                print('ok', c.v)
        """})
        assert s.verdicts()["heavyx"] == "required"

    def test_framework_subclass_hook(self, tmp_path):
        """stdlib のクラスを継承したフック（Thread.run）は名前で呼ばれなくても到達"""
        s = Scenario(tmp_path, HY, {"main.py": """
            import threading
            import heavyy
            class W(threading.Thread):
                def run(self):
                    print('ok', heavyy.compute())
            w = W(); w.start(); w.join()
        """})
        assert s.verdicts()["heavyy"] == "required"


# ---------------------------------------------------------------------------
# H4 / H6 / H7: import 形式
# ---------------------------------------------------------------------------


class TestImportForms:
    def test_h4_import_dotted_then_call(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": "import heavyx.sub\nprint('ok', heavyx.sub.f())\n"})
        assert s.verdicts()["heavyx"] == "required"

    def test_h6_from_dot_import_submodule_traversed(self, tmp_path):
        s = Scenario(tmp_path, HY, {
            "main.py": "from app import run\nrun()\n",
            "app/__init__.py": "from . import worker\nimport heavyy\ndef run():\n    worker.go()\n",
            "app/worker.py": "import heavyy\nVAL = heavyy.compute()\ndef go():\n    print('ok', VAL)\n",
        })
        assert s.verdicts()["heavyy"] == "required"

    def test_h7_parent_init_executed(self, tmp_path):
        sp = dict(HY)
        sp["libB/__init__.py"] = "import heavyy\nV = heavyy.compute()\n"
        sp["libB/x/__init__.py"] = ""
        sp["libB/x/y.py"] = "def f():\n    return 1\n"
        s = Scenario(tmp_path, sp, {"main.py": "import libB.x.y\nprint('ok', libB.x.y.f())\n"})
        v = s.verdicts()
        assert v["heavyy"] == "required"
        assert v["libB"] == "required"


# ---------------------------------------------------------------------------
# H5: スタブの型を元の AST から決める
# ---------------------------------------------------------------------------


class TestH5StubKinds:
    def test_lowercase_class_isinstance(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx
            from heavyx import ndarray
            def is_arr(x): return isinstance(x, ndarray)
            def unused(): return heavyx.compute()
        """), {"main.py": "import libA\nprint('ok', libA.is_arr(1))\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_uppercase_constant_value(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            from heavyx import MAX_SIZE
            LIMIT = MAX_SIZE * 2
        """), {"main.py": "import libA\nprint('ok', libA.LIMIT)\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")

    def test_submodule_imported_by_name(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            from heavyx import plotting
            STYLE = plotting.DEFAULT_STYLE
        """), {"main.py": "import libA\nprint('ok', libA.STYLE)\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        g = s.assert_stub_equivalent("heavyx")
        assert "heavyx/plotting.py" in g["files"]

    def test_dunder_version_and_all(self, tmp_path):
        s = Scenario(tmp_path, {
            "pq/__init__.py": "from .ver import __version__\n__all__ = ['x']\nx = 1\n",
            "pq/ver.py": "__version__ = '2.0'\n",
            "libD/__init__.py": (
                "import pq\nfrom pq.ver import __version__\nfrom pq import __all__\n"
                "V = __version__.split('.')\nN = len(__all__)\ndef unused():\n    return pq.x()\n"
            ),
        }, {"main.py": "import libD\nprint('ok', libD.V, libD.N)\n"})
        assert s.verdicts()["pq"] == "stubbable"
        s.assert_stub_equivalent("pq")

    def test_bare_decorator_is_required(self, tmp_path):
        s = Scenario(tmp_path, lib("""
            import heavyx
            from heavyx import jit
            @jit
            def f(x): return x
            def unused(): return heavyx.compute()
        """), {"main.py": "import libA\nprint('ok', libA.f(1))\n"})
        assert s.verdicts()["heavyx"] == "required"


# ---------------------------------------------------------------------------
# H8 / H9: 文字コード
# ---------------------------------------------------------------------------


class TestEncodings:
    def test_h8_utf8_bom_file_is_parsed(self, tmp_path):
        s = Scenario(tmp_path, HY, {
            "main.py": "import helper\nimport gui\ngui.run()\n",
            "helper.py": "import heavyy\ndef calc():\n    return heavyy.compute()\n",
            "gui.py": b"\xef\xbb\xbfimport helper\ndef run():\n    print('ok', helper.calc())\n",
        })
        res = s.analyze()
        assert not any("構文エラー" in w for w in res["warnings"])
        assert s.verdicts()["heavyy"] == "required"

    def test_h9_cp932_with_coding_cookie(self, tmp_path):
        s = Scenario(tmp_path, HY, {
            "main.py": "import util\nutil.run()\n",
            "util.py": (
                "# -*- coding: cp932 -*-\nimport heavyy\ndef 計算():\n    return heavyy.compute()\n"
                "def run():\n    print('ok', 計算())\n"
            ).encode("cp932"),
        })
        imports, warnings = extract_imports(str(s.proj / "util.py"))
        assert [i.module_name for i in imports] == ["heavyy"]
        assert warnings == []
        assert s.verdicts()["heavyy"] == "required"


# ---------------------------------------------------------------------------
# H10 / M12 / L5: try/except ImportError
# ---------------------------------------------------------------------------


class TestProtectedImports:
    def test_h10_protected_but_used_is_required(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": """
            try:
                import heavyy
            except ImportError:
                heavyy = None
            def main():
                print('ok', heavyy.compute())
            main()
        """})
        assert s.verdicts()["heavyy"] == "required"

    def test_h10_reraise_handler_is_not_protection(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": """
            try:
                import heavyy
            except ImportError as e:
                raise RuntimeError('heavyy required') from e
            print('ok')
        """})
        imports, _ = extract_imports(s.entry)
        assert imports[0].is_protected is False
        assert s.verdicts()["heavyy"] != "nofollow"

    def test_m12_multimodule_package_can_be_nofollow(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": """
            try:
                import heavyx
            except ImportError:
                heavyx = None
            print('ok')
        """})
        assert s.verdicts()["heavyx"] == "nofollow"

    def test_l5_builtins_importerror_attribute(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": (
            "import builtins\ntry:\n    import heavyy\nexcept builtins.ImportError:\n"
            "    heavyy = None\nprint('ok')\n"
        )})
        assert s.verdicts()["heavyy"] == "nofollow"


# ---------------------------------------------------------------------------
# H11 / H12 / M9: サブモジュールスタブ
# ---------------------------------------------------------------------------

SUB_SP = {
    "qtx/__init__.py": "X = 1\n",
    "qtx/QtCore" + importlib.machinery.EXTENSION_SUFFIXES[0]: b"\0fake",
    "numx/__init__.py": "def f():\n    return 7\n",
    "parentp/__init__.py": "import parentp.gui\nfrom parentp.gui.widgets import Plot\nfrom .core import load\n",
    "parentp/core.py": "import numx\ndef load():\n    return numx.f()\n",
    "parentp/gui/__init__.py": "import qtx\nimport numx\nfrom .widgets import Plot\n",
    "parentp/gui/widgets.py": "class Plot:\n    pass\n",
}


class TestSubmoduleStubs:
    def test_h11_child_modules_generated_and_parent_imports(self, tmp_path):
        s = Scenario(tmp_path, SUB_SP, {"main.py": "import parentp\nprint('ok', parentp.load())\n"})
        res = s.analyze()
        hints = [(h["submodule"], h["target_package"]) for h in res.get("submodule_stub_hints", [])]
        assert ("parentp.gui", "qtx") in hints
        g = generate_submodule_stubs(s.entry, "parentp", "parentp.gui", python_path=str(s.sp))
        assert "parentp/gui/widgets.py" in g["files"]
        ssp = s.stub_site(g["files"], ["parentp/gui"])
        rc, out = s.run(ssp)
        assert rc == 0, out
        assert out == "ok 7"

    def test_m9_eliminated_excludes_packages_used_elsewhere(self, tmp_path):
        s = Scenario(tmp_path, SUB_SP, {"main.py": "import parentp\nprint('ok', parentp.load())\n"})
        g = generate_submodule_stubs(s.entry, "parentp", "parentp.gui", python_path=str(s.sp))
        assert g["eliminated_packages"] == ["qtx"]

    def test_h12_other_package_using_submodule_blocks_hint(self, tmp_path):
        sp = dict(SUB_SP)
        sp["parentp/__init__.py"] = "from .core import load\n"
        sp["libO/__init__.py"] = "from parentp.gui import Plot\ndef show():\n    return Plot()\n"
        sp["parentp/gui/__init__.py"] = "import qtx\nfrom .widgets import Plot\n"
        s = Scenario(tmp_path, sp, {"main.py": "import parentp\nimport libO\nprint('ok', parentp.load(), libO.show() is not None)\n"})
        res = s.analyze()
        assert not any(h["submodule"] == "parentp.gui" for h in res.get("submodule_stub_hints", []))
        g = generate_submodule_stubs(s.entry, "parentp", "parentp.gui", python_path=str(s.sp))
        assert any("警告" in w for w in g["warnings"])


# ---------------------------------------------------------------------------
# H13 / H14 / M8 / L1 / L2 / L4: 入力検証と手順
# ---------------------------------------------------------------------------


class TestInstructions:
    @pytest.mark.parametrize("name", ["", "..", "../../etc", "a/b", "x y", "import"])
    def test_h13_invalid_package_names_rejected(self, tmp_path, name):
        s = Scenario(tmp_path, HY, {"main.py": "import heavyy\n"})
        assert "error" in generate_stubs(s.entry, name, python_path=str(s.sp))

    @pytest.mark.parametrize("parent,sub", [("", ""), ("x", "x.."), ("heavyy", "../x"), ("a", "b.c")])
    def test_h13_invalid_submodule_names_rejected(self, tmp_path, parent, sub):
        s = Scenario(tmp_path, HY, {"main.py": "import heavyy\n"})
        assert "error" in generate_submodule_stubs(s.entry, parent, sub, python_path=str(s.sp))

    def test_h14_uses_actual_site_packages_path(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": "import heavyx\n"})
        g = generate_stubs(s.entry, "heavyx", python_path=str(s.sp))
        bi = g["build_instructions"]
        all_cmds = "\n".join(bi["backup_commands"] + bi["install_commands"] + bi["uninstall_commands"])
        assert "getsitepackages" not in all_cmds
        assert str(s.sp.resolve()).replace("\\", "/") in all_cmds
        assert bi["site_packages"] == str(s.sp.resolve())

    def test_m8_single_file_module_instructions(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": "import heavyy\n"})
        g = generate_stubs(s.entry, "heavyy", python_path=str(s.sp))
        assert list(g["files"]) == ["heavyy.py"]
        cmds = "\n".join(g["build_instructions"]["backup_commands"] + g["build_instructions"]["install_commands"])
        assert "heavyy.py" in cmds
        assert "cp -r" not in "\n".join(g["build_instructions"]["install_commands"])

    def test_l1_no_pip_uninstall_and_backup_outside_site_packages(self, tmp_path):
        s = Scenario(tmp_path, HX, {"main.py": "import heavyx\n"})
        bi = generate_stubs(s.entry, "heavyx", python_path=str(s.sp))["build_instructions"]
        assert not any("pip uninstall" in c for c in bi["install_commands"] + bi["backup_commands"])
        assert not Path(bi["backup_dir"]).resolve().is_relative_to(s.sp.resolve())

    def test_l2_paths_with_spaces_are_quoted(self, tmp_path):
        base = tmp_path / "dir with space 日本語"
        base.mkdir()
        s = Scenario(base, HX, {"main.py": "import heavyx\n"})
        bi = generate_stubs(s.entry, "heavyx", python_path=str(s.sp))["build_instructions"]
        mv = [c for c in bi["backup_commands"] if c.startswith("mv ")][0]
        args = shlex.split(mv.split("  #")[0])
        assert args[0] == "mv" and len(args) == 3
        assert args[1].endswith("/heavyx")

    def test_l4_single_file_parent_message(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": "import heavyy\n"})
        g = generate_submodule_stubs(s.entry, "heavyy", "heavyy.x", python_path=str(s.sp))
        assert "単一ファイル" in g["error"]


# ---------------------------------------------------------------------------
# M1〜M4: モジュール解決
# ---------------------------------------------------------------------------


class TestResolution:
    def test_m1_missing_python_path_warns_and_not_stdlib(self, tmp_path):
        s = Scenario(tmp_path, {}, {"main.py": "import requests_does_not_exist_xyz\nimport heavyy_zz\n"})
        res = analyze(s.entry, python_path=str(tmp_path / "nope"))
        assert any("python_path" in w for w in res["warnings"])
        g = inspect_graph(s.entry, python_path=str(tmp_path / "nope"))
        assert all(n["classification"] != "stdlib" for n in g["nodes"] if n["module"] != "main")

    def test_m2_venv_inside_project(self, tmp_path):
        s = Scenario(tmp_path, {}, {
            "main.py": "import pkgv\nprint('ok', pkgv.run())\n",
            ".venv/Lib/site-packages/pkgv/__init__.py": "from .core import run\n",
            ".venv/Lib/site-packages/pkgv/core.py": "from . import util\ndef run():\n    return util.X\n",
            ".venv/Lib/site-packages/pkgv/util.py": "X = 1\n",
        })
        spv = s.proj / ".venv/Lib/site-packages"
        g = inspect_graph(s.entry, python_path=str(spv))
        nodes = {n["module"]: (n["classification"], n["top_level_package"]) for n in g["nodes"]}
        assert nodes["pkgv.core"] == ("third_party", "pkgv")
        assert not any(m.startswith(".venv") for m in nodes)

    def test_m3_namespace_dir_does_not_shadow_regular_package(self, tmp_path):
        s = Scenario(tmp_path, {"heavyz/__init__.py": "def f():\n    return 1\n"}, {
            "main.py": "import heavyz\nprint('ok', heavyz.f())\n",
            "heavyz/data.csv": "a,b\n",
        })
        g = inspect_graph(s.entry, python_path=str(s.sp))
        assert {n["module"]: n["classification"] for n in g["nodes"]}["heavyz"] == "third_party"

    def test_m4_parent_folder_named_like_package(self, tmp_path):
        base = tmp_path / "qtx"
        base.mkdir()
        s = Scenario(base, {
            "qtx/__init__.py": "X=1\n",
            "qtx/QtCore" + importlib.machinery.EXTENSION_SUFFIXES[0]: b"\0",
            "libQ/__init__.py": "import qtx\ndef unused():\n    return qtx.f()\n",
        }, {"main.py": "import libQ\n"})
        assert s.verdicts()["qtx"] == "required"

    def test_m4_single_extension_module(self, tmp_path):
        s = Scenario(tmp_path, {
            "fastmod" + importlib.machinery.EXTENSION_SUFFIXES[0]: b"\0fake",
            "libF/__init__.py": "import fastmod\ndef unused():\n    return fastmod.go()\n",
        }, {"main.py": "import libF\n"})
        assert s.verdicts()["fastmod"] == "required"

    def test_l7_longest_site_root_wins(self, tmp_path):
        sp = tmp_path / "site-packages"
        (sp / "win32" / "lib").mkdir(parents=True)
        f = sp / "win32" / "lib" / "win32con.py"
        f.write_text("X = 1\n")
        r = ModuleResolver(project_root=str(tmp_path / "proj"), python_path=str(sp))
        r._site_packages.append((sp / "win32" / "lib").resolve())
        cls, name, top = r.classify_path(f)
        assert (cls, name, top) == ("third_party", "win32con", "win32con")

    def test_binary_mention_from_c_extension(self, tmp_path):
        """C 拡張から import されるパッケージ（pandas→pytz 型）は required"""
        s = Scenario(tmp_path, {
            "heavyy.py": "def compute():\n    return 1\n",
            "libX/__init__.py": "from . import _ext\nimport heavyy\ndef unused():\n    return heavyy.compute()\n",
            "libX/_ext" + importlib.machinery.EXTENSION_SUFFIXES[0]: b"\0\0from heavyy\0heavyy\0\0",
        }, {"main.py": "import libX\n"})
        v = s.verdicts()
        assert v["heavyy"] == "required"


# ---------------------------------------------------------------------------
# M5〜M7, M10, M11, M13, L3
# ---------------------------------------------------------------------------


class TestMisc:
    def test_m5_relative_import_resolution_in_module(self, tmp_path):
        s = Scenario(tmp_path, {
            "pk/__init__.py": "from .api import Client\nimport pk.models\n",
            "pk/api.py": "from .models import Client\n",
            "pk/models.py": "class Client:\n    pass\n",
            "libC/__init__.py": "import pk\nfrom pk.models import Client\ndef unused():\n    return pk.Client()\n",
        }, {"main.py": "import libC\nprint('ok')\n"})
        g = generate_stubs(s.entry, "pk", python_path=str(s.sp))
        assert "pk.api.models" not in g["referenced_symbols"]
        s.assert_stub_equivalent("pk")

    def test_m6_star_import_constants(self, tmp_path):
        s = Scenario(tmp_path, {
            "pz/__init__.py": "def helper():\n    return 1\nCONST = 3\n",
            "libS/__init__.py": "from pz import *\nimport pz\nX = CONST * 2\ndef unused():\n    return pz.helper()\n",
        }, {"main.py": "import libS\nprint('ok', libS.X)\n"})
        assert s.verdicts()["pz"] == "stubbable"
        s.assert_stub_equivalent("pz")

    def test_m7_scm_version_file(self, tmp_path):
        s = Scenario(tmp_path, {
            "scmpkg_imp/__init__.py": "from ._version import __version__\ndef f():\n    return 1\n",
            "scmpkg_imp/_version.py": (
                "# file generated by setuptools_scm\n"
                "__all__ = [\"__version__\", \"__version_tuple__\", \"version\", \"version_tuple\"]\n"
                "__version__ = version = '8.0.4'\n"
            ),
            "libE/__init__.py": "import scmpkg_imp\ndef unused():\n    return scmpkg_imp.f()\n",
        }, {"main.py": "import libE\nprint('ok')\n"})
        g = generate_stubs(s.entry, "scmpkg_imp", python_path=str(s.sp))
        init = g["files"]["scmpkg_imp/__init__.py"]
        compile(init, "x", "exec")
        assert "__version__ = '8.0.4'" in init

    def test_m7_version_from_python_path_metadata(self, tmp_path):
        s = Scenario(tmp_path, {
            "requests/__init__.py": "__version__='0.0.1'\ndef get():\n    pass\n",
            "requests-0.0.1.dist-info/METADATA": "Metadata-Version: 2.1\nName: requests\nVersion: 0.0.1\n",
            "requests-0.0.1.dist-info/top_level.txt": "requests\n",
            "libR/__init__.py": "import requests\ndef unused():\n    return requests.get()\n",
        }, {"main.py": "import libR\n"})
        g = generate_stubs(s.entry, "requests", python_path=str(s.sp))
        assert "__version__ = '0.0.1'" in g["files"]["requests/__init__.py"]
        assert any("requests==0.0.1" in c for c in g["build_instructions"]["uninstall_commands"])

    def test_m10_import_chains_polynomial(self, tmp_path):
        n = 16
        sp = {"heavyy.py": "def compute():\n    return 1\n", "dep/__init__.py": "import dep.m0\n"}
        for i in range(n):
            imps = "".join(f"import dep.m{j}\n" for j in range(n) if j != i)
            sp[f"dep/m{i}.py"] = imps + ("import heavyy\n" if i == n - 1 else "")
        s = Scenario(tmp_path, sp, {"main.py": "import dep\n"})
        t = time.time()
        r = check_usage(s.entry, "heavyy", python_path=str(s.sp), max_depth=50)
        assert time.time() - t < 10
        assert r["import_chains"] and r["import_chains"][0][0] == "main"

    def test_m11_truncation_warning(self, tmp_path):
        files = {"main.py": "import m1\n"}
        for i in range(1, 13):
            files[f"m{i}.py"] = f"import m{i + 1}\n"
        files["m13.py"] = "import heavyy\nV = heavyy.compute()\n"
        s = Scenario(tmp_path, HY, files)
        res = s.analyze()
        assert any("max_depth" in w for w in res["warnings"])
        assert "heavyy" not in [p["package_name"] for p in res["stubbable"]]

    def test_m11_use_beyond_depth_is_still_seen(self, tmp_path):
        """候補が浅い所にあっても、深い所の使用は到達解析で拾う"""
        files = {"main.py": "import heavyy\nimport m1\n"}
        for i in range(1, 13):
            files[f"m{i}.py"] = f"import m{i + 1}\n"
        files["m13.py"] = "import heavyy\nV = heavyy.compute()\n"
        s = Scenario(tmp_path, HY, files)
        assert s.verdicts()["heavyy"] == "required"

    def test_m13_type_checking_only_import(self, tmp_path):
        s = Scenario(tmp_path, dict(HX, **{"libA/__init__.py": """
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                import heavyx
            def f(x: "heavyx.DataFrame"): return 1
        """}), {"main.py": "import libA\nprint('ok', libA.f(1))\n"})
        assert s.verdicts()["heavyx"] in ("stubbable", "nofollow")

    def test_l3_pyw_entry_module_name(self, tmp_path):
        s = Scenario(tmp_path, HY, {"app.pyw": "import heavyy\nheavyy.compute()\n"}, entry="app.pyw")
        g = inspect_graph(s.entry, python_path=str(s.sp))
        assert "app" in [n["module"] for n in g["nodes"]]


# ---------------------------------------------------------------------------
# 到達解析の絞り込み（安全側を保ったまま過大評価を抑える）
# ---------------------------------------------------------------------------


class TestReachabilityPrecision:
    def test_getattr_dispatch_on_self_is_reached(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": """
            import heavyy
            class Cmd:
                def run(self, name):
                    return getattr(self, "do_" + name)()
                def do_go(self):
                    return heavyy.compute()
            print('ok', Cmd().run("go"))
        """})
        assert s.verdicts()["heavyy"] == "required"

    def test_getattr_on_module_is_reached(self, tmp_path):
        s = Scenario(tmp_path, HY, {
            "main.py": "import ops\nname = 'go'\nprint('ok', getattr(ops, 'op_' + name)())\n",
            "ops.py": "import heavyy\ndef op_go():\n    return heavyy.compute()\n",
        })
        assert s.verdicts()["heavyy"] == "required"

    def test_node_visitor_hooks_are_reached(self, tmp_path):
        s = Scenario(tmp_path, HY, {"main.py": """
            import ast
            import heavyy
            class V(ast.NodeVisitor):
                def visit_Name(self, node):
                    print('ok', heavyy.compute())
            V().visit(ast.parse("x"))
        """})
        assert s.verdicts()["heavyy"] == "required"

    def test_subclassing_alone_does_not_instantiate_base(self, tmp_path):
        sp = dict(HX)
        sp["libA/__init__.py"] = """
            import heavyx
            class Base:
                def export(self):
                    return heavyx.DataFrame()
            class Child(Base):
                pass
            def make():
                return 1
        """
        s = Scenario(tmp_path, sp, {"main.py": "import libA\nprint('ok', libA.make())\n"})
        assert s.verdicts()["heavyx"] == "stubbable"
        s.assert_stub_equivalent("heavyx")
