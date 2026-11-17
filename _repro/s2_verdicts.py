from harness import scenario
from common import HX, lib

HY = {"heavyy.py": "def compute():\n    return 42\n"}  # single-file package, no internal imports

# S08b bare decorator, package otherwise has a gateway so it becomes stubbable
scenario("S08b_bare_decorator", lib("""
    import heavyx
    from heavyx import jit
    @jit
    def f(x): return x
    def unused(): return heavyx.compute()
"""), {"main.py": "import libA\nprint('ok', libA.f(1))\n"}, stub_pkgs=["heavyx"])

# S09b lowercase class -> generated as function
scenario("S09b_lowercase_class", lib("""
    import heavyx
    from heavyx import ndarray
    def is_arr(x): return isinstance(x, ndarray)
    def unused(): return heavyx.compute()
"""), {"main.py": "import libA\nprint('ok', libA.is_arr(1))\n"}, stub_pkgs=["heavyx"])

# S12b protected-but-used, single file package
scenario("S12b_protected_but_used_single", HY, {"main.py": """
    try:
        import heavyy
    except ImportError:
        heavyy = None
    def main():
        print('ok', heavyy.compute())
    main()
"""})

# S13b re-raise handler, single file package
scenario("S13b_protected_reraise_single", HY, {"main.py": """
    try:
        import heavyy
    except ImportError as e:
        raise RuntimeError('heavyy required') from e
    print('ok', heavyy.compute())
"""})

# S17 nofollow unreachable for multi-module package (internal edges counted)
scenario("S17_nofollow_multimodule", HX, {"main.py": """
    try:
        import heavyx
    except ImportError:
        heavyx = None
    print('ok')
"""})

# S18 TYPE_CHECKING-only import in dependency
scenario("S18_type_checking", dict(HX, **{"libA/__init__.py": """
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        import heavyx
    def f(x: "heavyx.DataFrame"): return 1
"""}), {"main.py": "import libA\nprint('ok', libA.f(1))\n"})

# S19 BOM in a project file hides its calls -> gateway considered uncalled
root_files = {
    "main.py": "import helper\nimport gui\ngui.run()\n",
    "helper.py": "import heavyy\ndef compute():\n    return heavyy.compute()\n",
    "gui.py": b"\xef\xbb\xbfimport helper\ndef run():\n    print('ok', helper.compute())\n",
}
scenario("S19_bom", HY, root_files, verbose=True)

# S20 relative `from . import sub` in local package: sub never traversed
scenario("S20_from_dot_import_local", HY, {
    "main.py": "from app import run\nrun()\n",
    "app/__init__.py": "from . import worker\nimport heavyy\ndef run():\n    worker.go()\ndef unused():\n    heavyy.compute()\n",
    "app/worker.py": "import heavyy\nVAL = heavyy.compute()\ndef go():\n    print('ok', VAL)\n",
}, verbose=True)

# S21 `import a.b.c` -> a/__init__ and a/b/__init__ not traversed
scenario("S21_import_dotted_parents", dict(HY, **{
    "libB/__init__.py": "import heavyy\nV = heavyy.compute()\n",
    "libB/x/__init__.py": "",
    "libB/x/y.py": "def f():\n    return 1\n",
}), {"main.py": "import libB.x.y\nprint('ok', libB.x.y.f())\n"}, verbose=True)
