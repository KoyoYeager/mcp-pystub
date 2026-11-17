from h import scenario, RESULTS
scenario("I01_del_after_use", {"hv/__init__.py": "def compute():\n    return 42\n", "lib/__init__.py": "from hv import compute\nRESULT = compute()\ndel compute\n"},
         {"main.py": "import lib\nprint('ok', lib.RESULT)\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
