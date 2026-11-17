from h import scenario, RESULTS
SP = {"hv/__init__.py": "from . import sub\ndef compute(): return 1\n", "hv/sub.py": "X = 1\nNAME = 'n'\n"}
scenario("G01_submodule_const_arith", SP, {"main.py": "import hv\nprint('ok', hv.sub.X + 1)\n"}, show_stub=True)
scenario("G02_submodule_const_dotted_import", SP, {"main.py": "import hv.sub\nprint('ok', hv.sub.X + 1)\n"})
scenario("G03_submodule_const_from_import", SP, {"main.py": "from hv import sub\nprint('ok', sub.X + 1)\n"})
scenario("G04_submodule_const_method", SP, {"main.py": "import hv\nprint('ok', hv.sub.NAME.upper())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
