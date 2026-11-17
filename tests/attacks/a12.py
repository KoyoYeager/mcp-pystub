from h import scenario, RESULTS
scenario("H08_platform_const", {"hv/__init__.py": "import sys\nif sys.platform == 'linux':\n    SEP = 'P'\nelse:\n    SEP = 'W'\nif sys.version_info >= (3, 99):\n    NEWAPI = True\nelse:\n    NEWAPI = False\n"},
         {"main.py": "import hv\nprint('ok', hv.SEP, hv.NEWAPI)\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
