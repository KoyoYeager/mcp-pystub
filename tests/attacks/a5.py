from h import scenario, RESULTS
HV = {"hv/__init__.py": "def compute(): return 42\n"}
scenario("B50b_platform_else_def", HV, {"main.py": "import hv, sys\nif sys.platform == 'linux':\n    def f():\n        return 0\nelse:\n    def f():\n        return hv.compute()\nprint('ok', f())\n"})
scenario("B50c_version_else_def_in_lib", dict(HV, **{"lib/__init__.py": "import sys, hv\nif sys.version_info < (3, 8):\n    def f():\n        return 0\nelse:\n    def f():\n        return hv.compute()\n"}), {"main.py": "import lib\nprint('ok', lib.f())\n"})
scenario("B50d_class_method_platform", HV, {"main.py": "import hv, sys\nclass C:\n    if sys.platform == 'linux':\n        def go(self):\n            return 0\n    else:\n        def go(self):\n            return hv.compute()\nprint('ok', C().go())\n"})
scenario("B50e_platform_else_class", HV, {"main.py": "import hv, sys\nif sys.platform == 'linux':\n    class K:\n        def go(self):\n            return 0\nelse:\n    class K:\n        def go(self):\n            return hv.compute()\nprint('ok', K().go())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
