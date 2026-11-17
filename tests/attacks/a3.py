from h import scenario, RESULTS
# fake C-extension package: real python package with a dummy .pyd at root
CX = {"cx/__init__.py": "def native(): return 'native'\nclass Widget:\n    pass\n", "cx/_core.cp313-win_amd64.pyd": "not a real binary"}

def big(gui_code, init_code="from . import gui\n", other=None):
    d = dict(CX)
    d["big/__init__.py"] = init_code
    d["big/gui.py"] = gui_code
    if other:
        d.update(other)
    return d

scenario("C01_hint_basic", big("import cx\nclass Window(cx.Widget):\n    pass\ndef show():\n    return cx.native()\nTHEME = 'dark'\n",
         "from . import gui\ndef core(): return 'core'\n"),
         {"main.py": "import big\nprint('ok', big.core(), big.gui.THEME)\n"})

scenario("C02_hint_except_class", big("import cx\nclass GuiError(Exception):\n    pass\ndef show():\n    return cx.native()\n",
         "from . import gui\ndef core():\n    try:\n        return int('x')\n    except gui.GuiError:\n        return 'gui'\n    except ValueError:\n        return 'core'\n"),
         {"main.py": "import big\nprint('ok', big.core())\n"})

scenario("C03_hint_star_reexport", big("import cx\n__all__ = ['THEME', 'show']\nTHEME = 'dark'\ndef show():\n    return cx.native()\n",
         "from .gui import *\ndef core(): return 'core'\n"),
         {"main.py": "import big\nprint('ok', big.core(), big.THEME)\n"})

scenario("C04_hint_isinstance_reexport", big("import cx\nfrom collections import OrderedDict as ODict\ndef show():\n    return cx.native()\n",
         "from . import gui\ndef core(x):\n    return isinstance(x, gui.ODict)\n"),
         {"main.py": "import big, collections\nprint('ok', big.core(collections.OrderedDict()))\n"})

scenario("C05_hint_other_importer_function_level", big("import cx\ndef show():\n    return cx.native()\n",
         "from . import gui\nfrom . import util\n", {"big/util.py": "def need():\n    import cx\n    return cx.native()\n"}),
         {"main.py": "import big\nprint('ok', big.util.need())\n"})

scenario("C06_hint_version_compare", big("import cx\nVERSION = tuple([1, 2])\ndef show():\n    return cx.native()\n",
         "from . import gui\n"),
         {"main.py": "import big\nprint('ok', big.gui.VERSION >= (1, 0))\n"})

# B36 fixed
scenario("B36_exec_string", {"hv/__init__.py": "def compute(): return 42\n", "lib36/__init__.py": "import hv\n"},
         {"main.py": "import lib36\nns = {}\nexec('import hv\nr = hv.compute()', ns)\nprint('ok', ns['r'])\n"})

# B40 variations
scenario("B40b_bound_method_local", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\ndef main():\n    c = C()\n    cbs = []\n    cbs.append(c.go)\n    return cbs[0]()\nprint('ok', main())\n"})
scenario("B40c_bound_method_modlevel_list", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\nc = C()\nf = c.go\nprint('ok', f())\n"})

# nofollow with ModuleNotFoundError name check
scenario("D01_nofollow_except_reraise_other", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": """
    try:
        import hv
    except ImportError as e:
        if e.name != "hv":
            raise
        hv = None
    def f():
        return "ok"
 """}, {"main.py": "import lib\nprint(lib.f())\n"})

# nofollow and protected import but the module object is passed
scenario("D02_nofollow_registry", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": """
    BACKENDS = []
    try:
        import hv
        BACKENDS.append("hv")
    except ImportError:
        pass
    def f():
        if not BACKENDS:
            raise RuntimeError("no backend")
        return "ok " + BACKENDS[0]
 """}, {"main.py": "import lib\nprint(lib.f())\n"})

# stubbable: module object passed to function (stub module returns dummy)
scenario("D03_module_obj_passed", {"hv/__init__.py": "X = 1\ndef compute(): return 42\n"},
         {"main.py": "import hv\ndef name_of(m):\n    return m.__name__\nprint('ok', name_of(hv))\n"})

# sys.modules check
scenario("D04_sys_modules_check", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": "import hv\n"},
         {"main.py": "import sys, lib\nprint('ok', 'hv' in sys.modules)\n"})

# importlib.util.find_spec
scenario("D05_find_spec", {"hv/__init__.py": "X = 1\n", "lib/__init__.py": """
    import importlib.util
    try:
        import hv
    except ImportError:
        pass
    HAS = importlib.util.find_spec("hv") is not None
    def f():
        if not HAS:
            raise RuntimeError("missing")
        return "ok"
 """}, {"main.py": "import lib\nprint(lib.f())\n"})

# local code uses the module as value (dict of modules)
scenario("D06_module_in_dict", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\nMODS = {'hv': hv}\nprint('ok', MODS['hv'].compute())\n"})

# module passed through local function argument then called
scenario("D07_module_param", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\ndef call(m):\n    return m.compute()\nprint('ok', call(hv))\n"})

# getattr with default on stub
scenario("D08_getattr_default", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\nfn = getattr(hv, 'fast_compute', None)\nprint('ok', 'fast' if fn is not None else 'slow')\n"})

for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
