from h import scenario, RESULTS
SP = {"hv/__init__.py": "class HvError(Exception):\n    pass\ndef compute():\n    return 1\n",
      "lib/__init__.py": "import hv\n_CHECKS = {}\ndef checks(name, raises=()):\n    def deco(f):\n        _CHECKS[name] = (f, raises)\n        return f\n    return deco\n@checks('idn', raises=hv.HvError)\ndef is_idn(x):\n    return hv.compute()\ndef info():\n    return 'info'\n"}
scenario("E09_exception_class_as_arg", SP, {"main.py": "import lib\nprint('ok', lib.info())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
