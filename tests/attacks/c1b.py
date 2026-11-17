from hc import cscenario, RESULTS
# C14: iteration / comparison in conditional path -> TypeError without the notice, swallowed by except TypeError
cscenario("C14_noncall_ops_no_notice", {
    "hv/__init__.py": "import os\nITEMS = list(os.sep)\nLEVEL = len(os.sep)\n",
    "lib/__init__.py": """
        def items(want):
            if want:
                import hv
                try:
                    return [x for x in hv.ITEMS] + [hv.LEVEL > 0]
                except TypeError:
                    return []
            return None
    """,
}, {"main.py": "import lib\nprint('ok', lib.items(False))\n", "alt.py": "import lib\nprint('ok', lib.items(True))\n"},
    entries=("main.py", "alt.py"))

# C15: try: import hv / except ImportError: hv = None inside conditional function -> presence check result changes?
cscenario("C15_presence_in_cond", {
    "hv/__init__.py": "def fast(v):\n    return v * 2\n",
    "lib/__init__.py": """
        def info(detail):
            if detail:
                try:
                    import hv
                except ImportError:
                    hv = None
                return 'hv available' if hv is not None else 'hv missing'
            return 'no detail'
        def use(v):
            if v > 100:
                import hv
                return hv.fast(v)
            return v
    """,
}, {"main.py": "import lib\nprint('ok', lib.info(False), lib.use(1))\n", "alt.py": "import lib\nprint('ok', lib.info(True))\n"},
    entries=("main.py", "alt.py"))

print("\n".join(f"{n}: {f}" for n, f in RESULTS))
