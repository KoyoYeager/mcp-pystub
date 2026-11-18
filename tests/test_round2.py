"""2 回目の修正の回帰テスト（docs/STUB_SAFETY_SPEC.md の 2〜7 節、検証担当の S1〜S14 と実ビルドの破れ）

各シナリオは「全提案の適用」（stubbable はスタブに差し替え、nofollow は除外、サブモジュールのヒントは
スタブ化した上で対象の C 拡張を除外）をして実際に実行し、出力と終了コードが元と同じであることを確かめる。
"""

from __future__ import annotations

import importlib.machinery
import shutil
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mcp_pystub.analyzer import analyze
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
from mcp_pystub.verifier import verify

EXT = importlib.machinery.EXTENSION_SUFFIXES[0]


def _write(root: Path, files: dict) -> None:
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(code, bytes):
            p.write_bytes(code)
        else:
            p.write_text(textwrap.dedent(code), encoding="utf-8")


def _run(proj: Path, site: Path, entry: Path) -> tuple[int, str]:
    code = ("import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')"
            % ([str(proj), str(site)], str(entry)))
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.returncode, (r.stdout + r.stderr).strip()


def _rm(site: Path, name: str) -> None:
    base = site.joinpath(*name.split("."))
    for t in [base, base.with_name(base.name + ".py")] + list(base.parent.glob(base.name + ".*.pyd")) + \
            list(base.parent.glob(base.name + EXT)):
        if t.is_dir():
            shutil.rmtree(t)
        elif t.exists():
            t.unlink()


def _put(site: Path, files: dict[str, str]) -> None:
    for rel, code in files.items():
        compile(code, rel, "exec")
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")


def apply_all_and_compare(tmp_path: Path, sp_files: dict, proj_files: dict) -> dict:
    """全提案を同時に適用して実行し、元と比べる。analyze の結果を返す。"""
    sp, proj = tmp_path / "sp", tmp_path / "proj"
    sp.mkdir(parents=True)
    proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    _write(sp, sp_files)
    _write(proj, proj_files)
    entry = proj / "main.py"
    rc0, out0 = _run(proj, sp, entry)
    res = analyze(str(entry), python_path=str(sp))
    site = tmp_path / "applied"
    shutil.copytree(sp, site)
    for p in res["stubbable"]:
        g = generate_stubs(str(entry), p["package_name"], python_path=str(sp))
        assert "error" not in g, g
        _rm(site, p["package_name"])
        _put(site, g["files"])
    for p in res["nofollow"]:
        _rm(site, p["package_name"])
    for h in res.get("submodule_stub_hints", []):
        g = generate_submodule_stubs(str(entry), h["parent_package"], h["submodule"], python_path=str(sp))
        assert "error" not in g, g
        _rm(site, h["submodule"])
        _put(site, g["files"])
        _rm(site, h["target_package"])
    rc, out = _run(proj, site, entry)
    assert (rc, out) == (rc0, out0), (
        f"stubbable={[p['package_name'] for p in res['stubbable']]} "
        f"nofollow={[p['package_name'] for p in res['nofollow']]} "
        f"hints={[h['submodule'] for h in res.get('submodule_stub_hints', [])]}\norig={out0!r}\nnew={out!r}"
    )
    return res


def verdicts(res: dict) -> dict[str, str]:
    return {p["package_name"]: k for k in ("stubbable", "nofollow", "required") for p in res[k]}


HV = {"hv/__init__.py": "def compute(): return 42\nCONST = 1\n"}
CX = {"cx/__init__.py": "def native(): return 'native'\nclass Widget:\n    pass\n", f"cx/_core{EXT}": b"fake"}

# (ID, site-packages, project) — 検証担当の _check/a1〜a15 から（S1〜S14）
CASES: list[tuple[str, dict, dict]] = [
    # S1 例外クラス: スタブのクラスは元の基底（Exception 系）を継ぐ
    ("A01_except_stub_class", {"hv/__init__.py": "class HvError(Exception): pass\ndef compute(): return 1\n"},
     {"main.py": "import hv\ndef parse(s):\n    try:\n        return int(s)\n    except hv.HvError:\n        return 'hv'\n    except ValueError:\n        return 'bad'\nprint('ok', parse('x'))\n"}),
    ("A02_except_alias_builtin", {"hv/__init__.py": "from json import JSONDecodeError\nDecodeError = ValueError\ndef compute(): return 1\n"},
     {"main.py": "import json, hv\ndef load(s):\n    try:\n        return json.loads(s)\n    except hv.JSONDecodeError:\n        return 'decode-error'\ndef load2(s):\n    try:\n        return int(s)\n    except hv.DecodeError:\n        return 'value-error'\nprint('ok', load('{'), load2('x'))\n"}),
    ("B42_pkg_class_subclass_in_lib_used_in_except",
     {"hv/__init__.py": "class HvError(Exception): pass\nclass Sub(HvError): pass\n",
      "lib42/__init__.py": "import hv\ndef f(x):\n    try:\n        return 1 / x\n    except hv.HvError:\n        return -1\n"},
     {"main.py": "import lib42\ntry:\n    lib42.f(0)\nexcept ZeroDivisionError:\n    print('ok zde')\n"}),
    ("C02_hint_except_class", dict(CX, **{"big/__init__.py": "from . import gui\ndef core():\n    try:\n        return int('x')\n    except gui.GuiError:\n        return 'gui'\n    except ValueError:\n        return 'core'\n",
                                          "big/gui.py": "import cx\nclass GuiError(Exception):\n    pass\ndef show():\n    return cx.native()\n"}),
     {"main.py": "import big\nprint('ok', big.core())\n"}),
    # S2 サブモジュールの定数
    ("G01_submodule_const_arith", {"hv/__init__.py": "from . import sub\ndef compute(): return 1\n", "hv/sub.py": "X = 1\nNAME = 'n'\n"},
     {"main.py": "import hv\nprint('ok', hv.sub.X + 1)\n"}),
    ("G02_submodule_const_dotted_import", {"hv/__init__.py": "from . import sub\ndef compute(): return 1\n", "hv/sub.py": "X = 1\nNAME = 'n'\n"},
     {"main.py": "import hv.sub\nprint('ok', hv.sub.X + 1)\n"}),
    ("G04_submodule_const_method", {"hv/__init__.py": "from . import sub\ndef compute(): return 1\n", "hv/sub.py": "X = 1\nNAME = 'n'\n"},
     {"main.py": "import hv\nprint('ok', hv.sub.NAME.upper())\n"}),
    ("H02_deep_chain_sub_class_const", {"hv/__init__.py": "from . import a\n", "hv/a.py": "class B:\n    C = 5\n"},
     {"main.py": "import hv\nV = hv.a.B.C\nprint('ok', V * 2)\n"}),
    ("B11_lazy_getattr_pkg", {"lazy/__init__.py": "import importlib\ndef __getattr__(name):\n    if name == 'sub':\n        return importlib.import_module('lazy.sub')\n    raise AttributeError(name)\n", "lazy/sub.py": "VALUE = 'lazy-sub'\n"},
     {"main.py": "import lazy\nprint('ok', lazy.sub.VALUE)\n"}),
    # S3 del
    ("I01_del_after_use", {"hv/__init__.py": "def compute():\n    return 42\n", "lib/__init__.py": "from hv import compute\nRESULT = compute()\ndel compute\n"},
     {"main.py": "import lib\nprint('ok', lib.RESULT)\n"}),
    # S4 再定義・条件付き定義
    ("B50b_platform_else_def", HV, {"main.py": "import hv, sys\nif sys.platform == 'linux':\n    def f():\n        return 0\nelse:\n    def f():\n        return hv.compute()\nprint('ok', f())\n"}),
    ("B50c_version_else_def_in_lib", dict(HV, **{"lib/__init__.py": "import sys, hv\nif sys.version_info < (3, 8):\n    def f():\n        return 0\nelse:\n    def f():\n        return hv.compute()\n"}),
     {"main.py": "import lib\nprint('ok', lib.f())\n"}),
    ("B50d_class_method_platform", HV, {"main.py": "import hv, sys\nclass C:\n    if sys.platform == 'linux':\n        def go(self):\n            return 0\n    else:\n        def go(self):\n            return hv.compute()\nprint('ok', C().go())\n"}),
    ("B51_redefined_function", HV, {"main.py": "import hv\ndef f():\n    return 0\n_old = f\ndef f():\n    return hv.compute()\nprint('ok', f())\n"}),
    # S5 定数でない値の比較
    ("A04_version_info_compare", {"hv/__init__.py": "__version__ = '2.1.0'\nversion_info = tuple(int(p) for p in __version__.split('.'))\ndef compute(): return 1\n"},
     {"main.py": "import hv\nif hv.version_info >= (2, 0):\n    print('ok new')\nelse:\n    print('ok old')\n"}),
    ("C06_hint_version_compare", dict(CX, **{"big/__init__.py": "from . import gui\n", "big/gui.py": "import cx\nVERSION = tuple([1, 2])\ndef show():\n    return cx.native()\n"}),
     {"main.py": "import big\nprint('ok', big.gui.VERSION >= (1, 0))\n"}),
    ("H03_inherited_class_const_compare", {"hv/__init__.py": "class Base:\n    X = 1\nclass Sub(Base):\n    pass\n"},
     {"main.py": "import hv\nprint('ok', hv.Sub.X == 1)\n"}),
    ("F10_bool_and", {"hv/__init__.py": "import sys\nIS_WIN = sys.platform.startswith('linux')\n"},
     {"main.py": "import hv\nuse_x = hv.IS_WIN and True\nprint('ok', bool(use_x))\n"}),
    # S6 条件付き・書き換えられる定数
    ("H08_platform_const", {"hv/__init__.py": "import sys\nif sys.platform == 'linux':\n    SEP = 'P'\nelse:\n    SEP = 'W'\n"},
     {"main.py": "import hv\nprint('ok', hv.SEP)\n"}),
    ("B21_const_mutated_at_import", {"hv/__init__.py": "CONST = 1\ndef _init():\n    global CONST\n    CONST = 2\n_init()\ndef compute(): return 42\n"},
     {"main.py": "import hv\nprint('ok', hv.CONST)\n"}),
    ("B21b_const_set_by_other_module", {"hv/__init__.py": "CONST = 1\nfrom . import _patch\ndef compute(): return 42\n", "hv/_patch.py": "import hv\nhv.CONST = 3\n"},
     {"main.py": "import hv\nprint('ok', hv.CONST)\n"}),
    ("H04_mutable_literal_registry", {"hv/__init__.py": "PLUGINS = []\nPLUGINS.append('a')\ndef reg(n):\n    PLUGINS.append(n)\nreg('b')\n"},
     {"main.py": "import hv\nprint('ok', hv.PLUGINS)\n"}),
    ("A13_const_reassigned", {"hv/__init__.py": "LIMIT = 10\nLIMIT = 20\nMODE = 'a'\nMODE += 'b'\ndef compute(): return 1\n"},
     {"main.py": "import hv\nprint('ok', hv.LIMIT, hv.MODE)\n"}),
    # S7 Enum
    ("A12_enum_member", {"hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 1\n    BLUE = 2\ndef compute(): return 1\n"},
     {"main.py": "import hv\nprint('ok', hv.Color.RED, 1 == hv.Color.RED)\n"}),
    ("F01_enum_iter", {"hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 'red'\n    BLUE = 'blue'\n"},
     {"main.py": "import hv\nnames = []\nfor c in hv.Color:\n    names.append(c.name)\nprint('ok', names)\n"}),
    ("F02_enum_getitem", {"hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 'red'\n"},
     {"main.py": "import hv\nm = hv.Color['RED']\nprint('ok', m is not None, m == hv.Color.RED)\n"}),
    # S8 クラス本体の代入
    ("A14_class_attr_alias_self", {"hv/__init__.py": "DEFAULT_TIMEOUT = 5 * 60\ndef compute(): return 1\n"},
     {"main.py": "import hv\nclass Client:\n    timeout = hv.DEFAULT_TIMEOUT\n    def total(self): return self.timeout + 1\nprint('ok', Client().total())\n"}),
    ("A15_dataclass_default", {"hv/__init__.py": "DEFAULT = [1, 2][0] + 1\ndef compute(): return 1\n"},
     {"main.py": "import dataclasses, hv\n@dataclasses.dataclass\nclass C:\n    x: int = hv.DEFAULT\nprint('ok', C().x * 2)\n"}),
    ("B45_staticmethod_ref_in_class_dict", HV, {"main.py": "import hv\nclass C:\n    @staticmethod\n    def a():\n        return hv.compute()\n    OPS = {'a': a}\n    def run(self, k):\n        return self.OPS[k]()\nprint('ok', C().run('a'))\n"}),
    ("B46_class_level_list_of_methods", HV, {"main.py": "import hv\nclass C:\n    def a(self):\n        return hv.compute()\n    steps = [a]\n    def run(self):\n        return [s(self) for s in self.steps]\nprint('ok', C().run())\n"}),
    # S9 モジュールレベルのインスタンスの bound method
    ("B40d_modlevel_instance_method_arg", HV, {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\nc = C()\ndef call(f):\n    return f()\nprint('ok', call(c.go))\n"}),
    ("B40e_modlevel_instance_method_list", HV, {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\nc = C()\nL = [c.go]\nprint('ok', L[0]())\n"}),
    ("B40g_threading", HV, {"main.py": "import hv, threading\nclass W:\n    def work(self):\n        print('ok', hv.compute())\nw = W()\nt = threading.Thread(target=w.work)\nt.start(); t.join()\n"}),
    # S10 登録デコレータ・クラス族
    ("A07_local_registry_decorator", HV, {"main.py": "import hv\nREG = {}\ndef register(f):\n    REG[f.__name__] = f\n    return f\n@register\ndef handler():\n    return hv.compute()\nprint('ok', REG['handler']())\n"}),
    ("B01_thirdparty_register_decorator", dict(HV, **{"plug/__init__.py": "_REG = []\ndef register(f):\n    _REG.append(f)\n    return f\ndef run_all():\n    return [f() for f in _REG]\n"}),
     {"main.py": "import hv, plug\n@plug.register\ndef h():\n    return hv.compute()\nprint('ok', plug.run_all())\n"}),
    ("A08_init_subclass_registry", HV, {"main.py": "import hv\nclass Plugin:\n    registry = []\n    def __init_subclass__(cls, **kw):\n        super().__init_subclass__(**kw)\n        Plugin.registry.append(cls)\nclass HvPlugin(Plugin):\n    def run(self):\n        return hv.compute()\nfor p in Plugin.registry:\n    print('ok', p().run())\n"}),
    ("A09_subclasses", HV, {"main.py": "import hv\nclass Base:\n    def run(self): return 0\nclass Impl(Base):\n    def run(self): return hv.compute()\nprint('ok', [c().run() for c in Base.__subclasses__()])\n"}),
    ("A10_classmethod_factory", HV, {"main.py": "import hv\nclass Base:\n    @classmethod\n    def create(cls):\n        return cls()\nclass Impl(Base):\n    def go(self): return hv.compute()\nprint('ok', Impl.create().go())\n"}),
    # S11 動的な import・名前の取り出し
    ("B09_import_module_literal", dict(HV, **{"hv/sub.py": "def f(): return 'sub'\n"}),
     {"main.py": "import importlib, hv\nm = importlib.import_module('hv.sub')\nprint('ok', m.f())\n"}),
    ("B10_import_module_dynamic", dict(HV, **{"lib10/__init__.py": "import hv\n"}),
     {"main.py": "import importlib, lib10\nname = 'h' + 'v'\nm = importlib.import_module(name)\nprint('ok', m.compute())\n"}),
    ("B33_globals_dispatch", HV, {"main.py": "import hv\ndef handle_x(): return hv.compute()\ndef dispatch(k): return globals()['handle_' + k]()\nprint('ok', dispatch('x'))\n"}),
    ("B34_getattr_sys_modules", HV, {"main.py": "import sys, hv\ndef handle_x(): return hv.compute()\ndef dispatch(k): return getattr(sys.modules[__name__], 'handle_' + k)()\nprint('ok', dispatch('x'))\n"}),
    ("B38_methodcaller", HV, {"main.py": "import operator, hv\nclass C:\n    def go(self): return hv.compute()\nprint('ok', operator.methodcaller('go')(C()))\n"}),
    ("B39_entry_points", {
        "hv/__init__.py": "def compute(): return 42\n",
        "hv_plugin/__init__.py": "import hv\ndef plugin(): return hv.compute()\n",
        "hostlib/__init__.py": "import hv_plugin\nfrom importlib.metadata import entry_points\ndef run():\n    return [ep.load()() for ep in entry_points(group='host.plugins')]\n",
        "hv_plugin-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: hv_plugin\nVersion: 1.0\n",
        "hv_plugin-1.0.dist-info/entry_points.txt": "[host.plugins]\np = hv_plugin:plugin\n",
    }, {"main.py": "import hostlib\nprint('ok', hostlib.run())\n"}),
    # S12 nofollow
    ("A05_nofollow_is_none_raise", {"lib5/__init__.py": "try:\n    import hv\nexcept ImportError:\n    hv = None\ndef f():\n    if hv is None:\n        raise RuntimeError('hv is required')\n    return 'ok'\n", "hv/__init__.py": "def compute(): return 1\n"},
     {"main.py": "import lib5\nprint(lib5.f())\n"}),
    ("A06_nofollow_flag", {"lib6/__init__.py": "try:\n    import hv\n    HAS_HV = True\nexcept ImportError:\n    HAS_HV = False\ndef f():\n    if not HAS_HV:\n        raise ImportError('install hv')\n    return 'ok'\n", "hv/__init__.py": "def compute(): return 1\n"},
     {"main.py": "import lib6\nprint(lib6.f())\n"}),
    ("D02_nofollow_registry", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "BACKENDS = []\ntry:\n    import hv\n    BACKENDS.append('hv')\nexcept ImportError:\n    pass\ndef f():\n    if not BACKENDS:\n        raise RuntimeError('no backend')\n    return 'ok ' + BACKENDS[0]\n"},
     {"main.py": "import lib\nprint(lib.f())\n"}),
    ("D05_find_spec", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "import importlib.util\ntry:\n    import hv\nexcept ImportError:\n    pass\nHAS = importlib.util.find_spec('hv') is not None\ndef f():\n    if not HAS:\n        raise RuntimeError('missing')\n    return 'ok'\n"},
     {"main.py": "import lib\nprint(lib.f())\n"}),
    ("D10_except_sys_exit", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "import sys\ntry:\n    import hv\nexcept ImportError:\n    print('hv missing'); sys.exit(3)\ndef f():\n    return 'ok'\n"},
     {"main.py": "import lib\nprint(lib.f())\n"}),
    ("D11_except_helper_raises", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "def _missing(n):\n    raise ImportError(n + ' required')\ntry:\n    import hv\nexcept ImportError:\n    _missing('hv')\ndef f():\n    return 'ok'\n"},
     {"main.py": "import lib\nprint(lib.f())\n"}),
    ("D12_except_nested_raise", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "STRICT = True\ntry:\n    import hv\nexcept ImportError:\n    if STRICT:\n        raise\ndef f():\n    return 'ok'\n"},
     {"main.py": "import lib\nprint(lib.f())\n"}),
    ("A17_optional_is_not_none", {"lib17/__init__.py": "try:\n    import hv\nexcept ImportError:\n    hv = None\ndef describe():\n    return 'with-hv' if hv is not None else 'without-hv'\n", "hv/__init__.py": "def compute(): return 1\n"},
     {"main.py": "import lib17\nprint('ok', lib17.describe())\n"}),
    ("D04_sys_modules_check", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "import hv\n"},
     {"main.py": "import sys, lib\nprint('ok', 'hv' in sys.modules)\n"}),
    # S13 hasattr / getattr の既定値
    ("A16_hasattr_feature", {"hv/__init__.py": "def compute(): return 1\n"},
     {"main.py": "import hv\nFAST = hasattr(hv, 'fast_path')\nprint('ok', FAST, getattr(hv, 'missing', None) is None)\n"}),
    ("D08_getattr_default", {"hv/__init__.py": "def compute(): return 42\n"},
     {"main.py": "import hv\nfn = getattr(hv, 'fast_compute', None)\nprint('ok', 'fast' if fn is not None else 'slow')\n"}),
    # S14 stdlib の再エクスポート・同一性
    ("A03_isinstance_reexport", {"hv/__init__.py": "from collections import OrderedDict\nfrom pathlib import Path\nstring_types = (str,)\ndef compute(): return 1\n"},
     {"main.py": "import collections, pathlib, hv\nprint('ok', isinstance(collections.OrderedDict(), hv.OrderedDict), isinstance(pathlib.Path('.'), hv.Path), isinstance('s', hv.string_types))\n"}),
    ("C04_hint_isinstance_reexport", dict(CX, **{"big/__init__.py": "from . import gui\ndef core(x):\n    return isinstance(x, gui.ODict)\n", "big/gui.py": "import cx\nfrom collections import OrderedDict as ODict\ndef show():\n    return cx.native()\n"}),
     {"main.py": "import big, collections\nprint('ok', big.core(collections.OrderedDict()))\n"}),
    ("F04_same_symbol_two_paths", {"hv/__init__.py": "class Frame:\n    pass\nfrom . import core\n", "hv/core.py": "from hv import Frame as _F\nFrame = _F\n"},
     {"main.py": "import hv, hv.core\nprint('ok', hv.Frame is hv.core.Frame)\n"}),
    ("H01_star_dynamic_all", {"hv/__init__.py": "from . import core\nfrom .core import *\n__all__ = list(core.__all__) + ['X']\nX = 1\n", "hv/core.py": "__all__ = ['Frame']\nclass Frame:\n    pass\n"},
     {"main.py": "from hv import *\nprint('ok', isinstance(1, Frame), X)\n"}),
]


@pytest.mark.parametrize("case_id,sp_files,proj_files", CASES, ids=[c[0] for c in CASES])
def test_apply_all_proposals_same_output(tmp_path, case_id, sp_files, proj_files):
    apply_all_and_compare(tmp_path, sp_files, proj_files)


# ---------------------------------------------------------------------------
# 実ビルドで残った破れの型（仕様 5 節: ビルド時の実行 / スタブの忠実さ）
# ---------------------------------------------------------------------------


class TestBuildTimeAndFidelity:
    def test_hooked_package_body_is_reached_at_build(self, tmp_path):
        """PyInstaller のフックがあるパッケージ（matplotlib 型）は、実行時に使われなくてもビルド時に import される。
        その import 時の使用（cycler / pyparsing 型）は required。"""
        sp = {
            "cyc/__init__.py": "def cycler(*a):\n    return list(a)\n",
            "mpl/__init__.py": "import cyc\nDEFAULT = cyc.cycler('a')\n",
            "lib/__init__.py": "def lazy():\n    import mpl\n    return mpl.DEFAULT\ndef f():\n    return 'f'\n",
            "PyInstaller/hooks/hook-mpl.py": "hiddenimports = []\n",
        }
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "import lib\nprint('ok', lib.f())\n"})
        assert verdicts(res)["cyc"] == "required"

    def test_submodule_imported_by_other_code_is_generated(self, tmp_path):
        """他の解析対象コードが `from P.sub import x` するサブモジュールは全て生成する（numpy.linalg 型）。"""
        sp = {
            "npx/__init__.py": "from . import core\n",
            "npx/core.py": "X = 1\n",
            "npx/linalg/__init__.py": "def inv(a):\n    return a\n",
            "mplx/__init__.py": "from npx.linalg import inv\nfrom npx import core\n",
        }
        tmp = tmp_path
        res = apply_all_and_compare(tmp, sp, {"main.py": "import mplx\nprint('ok')\n"})
        assert verdicts(res)["npx"] == "stubbable"
        g = generate_stubs(str(tmp / "proj" / "main.py"), "npx", python_path=str(tmp / "sp"))
        assert "npx/linalg/__init__.py" in g["files"]

    def test_mpl_toolkits_register_then_del(self, tmp_path):
        """register の後の `del Axes3D`（matplotlib.projections 型）: 引数に渡したクラスは実行（C）。"""
        sp = {
            "tk/__init__.py": "class Axes3D:\n    name = '3d'\n",
            "proj_reg/__init__.py": "REG = {}\ndef register(*cls):\n    for c in cls:\n        REG[c.name] = c\ntry:\n    from tk import Axes3D\nexcept ImportError:\n    pass\nelse:\n    register(Axes3D)\n    del Axes3D\ndef names():\n    return sorted(REG)\n",
        }
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "import proj_reg\nprint('ok', proj_reg.names())\n"})
        assert verdicts(res)["tk"] == "required"

    def test_lazy_loader_dynamic_import_of_own_submodules(self, tmp_path):
        """パッケージ自身の遅延 import（pydantic の __getattr__ + import_module 型）はそのサブモジュールを全て読み込む。"""
        sp = {
            "at/__init__.py": "class Strict:\n    def __init__(self, v=True):\n        self.v = v\n",
            "pd2/__init__.py": "from importlib import import_module\n_MAP = {'BaseModel': '.main'}\ndef __getattr__(name):\n    mod = import_module(_MAP[name], package=__name__)\n    return getattr(mod, name)\n",
            "pd2/main.py": "import at\nSTRICT = at.Strict()\nclass BaseModel:\n    pass\n",
        }
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "from pd2 import BaseModel\nprint('ok', BaseModel.__name__)\n"})
        # pd2.main は静的なグラフに無い（同梱もされない）ので at は提案されない。提案されるなら required
        assert verdicts(res).get("at") in (None, "required")

    def test_case_sensitive_submodule(self, tmp_path):
        """`from P import Signal` は（Windows でも）P/signal.py ではない。"""
        sp = {"pk/__init__.py": "from .signal import Signal\n", "pk/signal.py": "class Signal:\n    pass\n"}
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "from pk import Signal\nprint('ok', Signal.__name__)\n"})
        assert "pk" in verdicts(res)

    def test_entry_points_group_constant_propagation(self, tmp_path):
        """entry_points(group=変数) の群は定数伝播で求め、別の群のプラグインは読み込まない。"""
        sp = {
            "hv/__init__.py": "def compute(): return 42\n",
            "hv_plugin/__init__.py": "import hv\ndef plugin(): return hv.compute()\n",
            "host/__init__.py": "from importlib.metadata import entry_points\ndef run():\n    group = 'other.group'\n    return [ep.load()() for ep in entry_points(group=group)]\n",
            "hv_plugin-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: hv_plugin\nVersion: 1.0\n",
            "hv_plugin-1.0.dist-info/entry_points.txt": "[host.plugins]\np = hv_plugin:plugin\n",
            "lib/__init__.py": "import hv_plugin\n",
        }
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "import host, lib\nprint('ok', host.run())\n"})
        assert verdicts(res)["hv_plugin"] == "stubbable"

    def test_getattr_name_from_parameter_literals(self, tmp_path):
        """getattr(obj, 引数) の名前は呼び出し側のリテラルに絞る（asammdf Signal.__apply_func 型）。"""
        sp = {
            "gx/__init__.py": "def draw():\n    return 'drawn'\n",
            "sig/__init__.py": "class Signal:\n    def __init__(self, v):\n        self.v = v\n    def _apply(self, other, func_name):\n        return getattr(self.v, func_name)(other)\n    def __add__(self, o):\n        return self._apply(o, '__add__')\n    def plot(self):\n        import gx\n        return gx.draw()\n",
        }
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "import sig\nprint('ok', sig.Signal(1) + 2)\n"})
        assert verdicts(res)["gx"] == "stubbable"

    def test_annotation_union_is_annotation(self, tmp_path):
        """`-> P.C | None` の注釈は注釈（A）。演算として数えない。"""
        sp = {"pdx/__init__.py": "class DataFrame:\n    pass\n",
              "lib/__init__.py": "import pdx\ndef f(x) -> pdx.DataFrame | None:\n    return None\ndef g():\n    return 'g'\n"}
        res = apply_all_and_compare(tmp_path, sp, {"main.py": "import lib\nprint('ok', lib.g())\n"})
        assert verdicts(res)["pdx"] == "stubbable"


# ---------------------------------------------------------------------------
# 削減の取りこぼし（仕様 4 節の絞り込み）— 安全を保ったまま stubbable に残る
# ---------------------------------------------------------------------------

PDX = {"pdx/__init__.py": "class DataFrame:\n    def __init__(self, d):\n        self.d = d\ndef read_csv(p):\n    return DataFrame(p)\n"}


@pytest.mark.parametrize("case_id,lib,main", [
    ("E02_common_method_name", "import pdx\nclass Client:\n    def info(self):\n        return 'info'\n    def read(self):\n        return pdx.read_csv('x')\n",
     "import io, lib\nc = lib.Client()\nprint('ok', c.info(), io.StringIO('s').read())\n"),
    ("E03_generic_base", "import pdx\nfrom typing import Generic, TypeVar\nT = TypeVar('T')\nclass Box(Generic[T]):\n    def get(self):\n        return 'box'\n    def as_frame(self):\n        return pdx.DataFrame([1])\n",
     "import lib\nprint('ok', lib.Box().get())\n"),
    ("E10_getattr_self_classconst", "import pdx\nclass Frame:\n    _axis_name = 'columns'\n    columns = ['a']\n    @property\n    def axis(self):\n        return getattr(self, self._axis_name)\n    def to_sql(self):\n        return pdx.read_csv('x')\n",
     "import lib\nprint('ok', lib.Frame().axis)\n"),
    ("E11_sys_modules_guard", "import sys\ndef maybe():\n    if 'pdx' not in sys.modules:\n        return 'no-pdx'\n    import pdx\n    return pdx.read_csv('x')\n",
     "import lib\nprint('ok', lib.maybe())\n"),
    ("E04_unknown_decorator_lib", "import pdx, functools\ndef logged(f):\n    @functools.wraps(f)\n    def w(*a, **k):\n        return f(*a, **k)\n    return w\n@logged\ndef unused():\n    return pdx.read_csv('x')\ndef used():\n    return 'used'\n",
     "import lib\nprint('ok', lib.used())\n"),
])
def test_narrowing_keeps_stubbable(tmp_path, case_id, lib, main):
    sp = dict(PDX)
    sp["lib/__init__.py"] = lib
    res = apply_all_and_compare(tmp_path, sp, {"main.py": main})
    assert verdicts(res)["pdx"] == "stubbable"


# ---------------------------------------------------------------------------
# verify ツール
# ---------------------------------------------------------------------------


class TestVerifyTool:
    def _setup(self, tmp_path, sp_files, main):
        sp, proj = tmp_path / "sp", tmp_path / "proj"
        sp.mkdir(parents=True)
        proj.mkdir(parents=True)
        (proj / "pyproject.toml").write_text("")
        _write(sp, sp_files)
        _write(proj, {"main.py": main})
        return str(proj / "main.py"), str(sp)

    def test_verify_passes_safe_stub(self, tmp_path):
        entry, sp = self._setup(tmp_path, {"hv/__init__.py": "class Frame:\n    pass\n", "lib/__init__.py": "import hv\ndef f(x):\n    return isinstance(x, hv.Frame)\n"},
                                "import lib\nprint('ok', lib.f(1))\n")
        r = verify(entry, sp, run_entry=True)
        assert r["passed"]["packages"] == ["hv"], r
        assert r["entry_matched"] is True

    def test_verify_rejects_stub_that_breaks_import(self, tmp_path):
        """静的判定を外して、import 時に実体を使うパッケージをわざとスタブにすると verify が落とす。"""
        entry, sp = self._setup(tmp_path, {"hv/__init__.py": "def compute():\n    return 1\n", "lib/__init__.py": "import hv\nV = hv.compute()\n"},
                                "import lib\nprint('ok', lib.V)\n")
        r = verify(entry, sp, packages=["hv"], submodules=[], nofollow=[])
        assert r["passed"]["packages"] == []
        assert r["rejected"] and r["rejected"][0]["item"] == "hv"
        assert "compute" in (r["rejected"][0]["error"] or "") or "stub" in (r["rejected"][0]["error"] or "")

    def test_verify_does_not_import_hidden_internals_directly(self, tmp_path):
        """スタブに含まれない内部モジュール（hv._native など）や外したモジュールを直接 import して誤って却下しない
        （第 3 回 e2e: 01_tqdm の pandas が pandas._libs の直接 import で却下された）。"""
        entry, sp = self._setup(tmp_path, {
            "hv/__init__.py": "from hv._impl import Frame\nfrom hv import _native\n",
            "hv/_impl.py": "class Frame:\n    pass\n",
            "hv/_native.py": "def run():\n    return 1\n",
            "lib/__init__.py": "import hv\ndef f(x):\n    return isinstance(x, hv.Frame)\n"},
            "import lib\nprint('ok', lib.f(1))\n")
        r = verify(entry, sp, packages=["hv"], submodules=[], nofollow=[], run_entry=True)
        assert r["passed"]["packages"] == ["hv"], r["rejected"]
        assert r["entry_matched"] is True

    def test_verify_does_not_modify_site_packages(self, tmp_path):
        entry, sp = self._setup(tmp_path, {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "import hv\n"}, "import lib\n")
        before = sorted(str(p.relative_to(sp)) for p in Path(sp).rglob("*") if "__pycache__" not in p.parts)
        verify(entry, sp)
        after = sorted(str(p.relative_to(sp)) for p in Path(sp).rglob("*") if "__pycache__" not in p.parts)
        assert before == after


# ---------------------------------------------------------------------------
# その他（入力検証の迂回・名前空間の共有・.pth のディレクトリ・深い継承）
# ---------------------------------------------------------------------------


class TestOtherFixes:
    def test_python_path_overlapping_project_is_rejected(self, tmp_path):
        proj = tmp_path / "proj"
        _write(proj, {"main.py": "import helper\n", "helper.py": "X = 1\n", "pyproject.toml": ""})
        g = generate_stubs(str(proj / "main.py"), "helper", python_path=str(tmp_path))
        assert "error" in g

    def test_python_path_stdlib_dir_is_rejected(self, tmp_path):
        import sysconfig
        proj = tmp_path / "proj"
        _write(proj, {"main.py": "import json\n", "pyproject.toml": ""})
        g = generate_stubs(str(proj / "main.py"), "json", python_path=sysconfig.get_paths()["stdlib"])
        assert "error" in g

    def test_namespace_shared_package_lists_all_distributions(self, tmp_path):
        sp = {
            "gns/a/__init__.py": "X = 1\n",
            "gns/b/__init__.py": "Y = 2\n",
            "gns_a-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: gns-a\nVersion: 1.0\n",
            "gns_a-1.0.dist-info/RECORD": "gns/a/__init__.py,,\n",
            "gns_b-2.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: gns-b\nVersion: 2.0\n",
            "gns_b-2.0.dist-info/RECORD": "gns/b/__init__.py,,\n",
        }
        _write(tmp_path / "sp", sp)
        _write(tmp_path / "Scripts", {"python.exe": b""})  # 対象環境の python（pip の再インストールの宛先）
        _write(tmp_path / "proj", {"main.py": "import gns.a\nprint('ok')\n", "pyproject.toml": ""})
        g = generate_stubs(str(tmp_path / "proj" / "main.py"), "gns", python_path=str(tmp_path / "sp"))
        cmd = [c for c in g["build_instructions"]["uninstall_commands"] if "pip install" in c][0]
        assert "gns-a==1.0" in cmd and "gns-b==2.0" in cmd

    def test_pth_subdirectory_is_not_used_as_backup_root(self, tmp_path):
        """pywin32 型: site-packages/win32/lib が sys.path にあっても、バックアップ先は site-packages の外。"""
        from mcp_pystub.module_resolver import ModuleResolver
        from mcp_pystub.stub_generator import _locate, _true_site_root
        sp = tmp_path / "site-packages"
        _write(sp, {"win32/lib/win32con.py": "X = 1\n"})
        r = ModuleResolver(str(tmp_path / "proj"), str(sp / "win32" / "lib"))
        main, moves, given = _locate("win32con", r)
        assert _true_site_root(main, given) == sp.resolve()

    def test_deep_inheritance_does_not_crash(self, tmp_path):
        n = 1200
        code = "import hv\nclass C0:\n    def go(self):\n        return 1\n" + "".join(
            f"class C{i}(C{i-1}):\n    pass\n" for i in range(1, n)) + f"print('ok', C{n-1}().go())\n"
        apply_all_and_compare(tmp_path, {"hv/__init__.py": "def compute(): return 42\n"}, {"main.py": code})
