from h import scenario, RESULTS
PD = {"pdx/__init__.py": "def read_csv(p):\n    return p\n"}
scenario("E10_getattr_self_classconst", dict(PD, **{"lib/__init__.py": "import pdx\nclass Frame:\n    _axis_name = 'columns'\n    columns = ['a']\n    @property\n    def axis(self):\n        return getattr(self, self._axis_name)\n    def to_sql(self):\n        return pdx.read_csv('x')\n"}),
         {"main.py": "import lib\nprint('ok', lib.Frame().axis)\n"})
scenario("E11_sys_modules_guard", dict(PD, **{"lib/__init__.py": "import sys\ndef maybe():\n    if 'pdx' not in sys.modules:\n        return 'no-pdx'\n    import pdx\n    return pdx.read_csv('x')\n"}),
         {"main.py": "import lib\nprint('ok', lib.maybe())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
