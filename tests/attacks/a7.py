from h import scenario, RESULTS
EN = {"hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 'red'\n    BLUE = 'blue'\nclass Frame:\n    pass\nfrom . import core\n", "hv/core.py": "from hv import Frame as _F\nFrame = _F\n"}
scenario("F01_enum_iter", EN, {"main.py": "import hv\nnames = []\nfor c in hv.Color:\n    names.append(c.name)\nprint('ok', names)\n"})
scenario("F02_enum_getitem", EN, {"main.py": "import hv\nm = hv.Color['RED']\nprint('ok', m is not None, m == hv.Color.RED)\n"})
scenario("F03_enum_contains", EN, {"main.py": "import hv\nprint('ok', 'zzz' in hv.Color.__members__ if False else 'x', hv.Color.RED in hv.Color)\n"})
scenario("F04_same_symbol_two_paths", EN, {"main.py": "import hv, hv.core\nprint('ok', hv.Frame is hv.core.Frame)\n"})
scenario("F05_class_attr_value_compare", EN, {"main.py": "import hv\nmode = 'red'\nprint('ok', mode == hv.Color.RED.value)\n"})
scenario("F06_class_truthiness_attr", {"hv/__init__.py": "class Config:\n    debug = bool(0)\n"}, {"main.py": "import hv\nif hv.Config.debug:\n    print('ok debug')\nelse:\n    print('ok nodebug')\n"})
scenario("F07_module_flag_unknown", {"hv/__init__.py": "import os\nHAS_GPU = os.environ.get('NOPE_GPU_X') is not None\n"}, {"main.py": "import hv\nprint('ok', 'gpu' if hv.HAS_GPU else 'cpu')\n"})
scenario("F08_module_flag_unknown_ifstmt", {"hv/__init__.py": "import os\nHAS_GPU = os.environ.get('NOPE_GPU_X') is not None\n"}, {"main.py": "import hv\nif hv.HAS_GPU:\n    print('ok gpu')\nelse:\n    print('ok cpu')\n"})
scenario("F09_not_flag", {"hv/__init__.py": "import os\nHAS_GPU = os.environ.get('NOPE_GPU_X') is not None\n"}, {"main.py": "import hv\nflag = not hv.HAS_GPU\nprint('ok', flag)\n"})
scenario("F10_bool_and", {"hv/__init__.py": "import sys\nIS_WIN = sys.platform.startswith('linux')\n"}, {"main.py": "import hv\nuse_x = hv.IS_WIN and True\nprint('ok', bool(use_x))\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
