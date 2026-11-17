from h import scenario, RESULTS
HV = {"hv/__init__.py": "X = 1\n"}
scenario("D10_except_sys_exit", dict(HV, **{"lib/__init__.py": "import sys\ntry:\n    import hv\nexcept ImportError:\n    print('hv missing'); sys.exit(3)\ndef f():\n    return 'ok'\n"}), {"main.py": "import lib\nprint(lib.f())\n"})
scenario("D11_except_helper_raises", dict(HV, **{"lib/__init__.py": "def _missing(n):\n    raise ImportError(n + ' required')\ntry:\n    import hv\nexcept ImportError:\n    _missing('hv')\ndef f():\n    return 'ok'\n"}), {"main.py": "import lib\nprint(lib.f())\n"})
scenario("D12_except_nested_raise", dict(HV, **{"lib/__init__.py": "STRICT = True\ntry:\n    import hv\nexcept ImportError:\n    if STRICT:\n        raise\ndef f():\n    return 'ok'\n"}), {"main.py": "import lib\nprint(lib.f())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
