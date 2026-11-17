from harness import scenario
from common import HX, lib

# S01 module-level attribute access in dependency
scenario("S01_attr_module_level", lib("""
    import heavyx
    DEFAULT = heavyx.Timestamp
    def convert(): return heavyx.frame()
"""), {"main.py": "import libA\nprint('ok', libA.DEFAULT is not None)\n"})

# S02 isinstance via attribute (README says stub-safe)
scenario("S02_isinstance_attr", lib("""
    import heavyx as hx
    def check(x): return isinstance(x, hx.DataFrame)
    def to_df(x): return hx.DataFrame(x)
"""), {"main.py": "import libA\nprint('ok', libA.check(1))\n"})

# S03 annotation evaluated at def time
scenario("S03_annotation", lib("""
    import heavyx as hx
    def f(x: hx.DataFrame) -> None: return None
    def g(): return hx.compute()
"""), {"main.py": "import libA\nprint('ok')\n"})

# S04 intra-library call chain (__init__ -> self._load -> heavyx)
scenario("S04_call_chain", dict(HX, **{
    "libA/__init__.py": "from .core import MDF\n",
    "libA/core.py": """
        import heavyx
        class MDF:
            def __init__(self):
                self._load()
            def _load(self):
                self.d = heavyx.read()
    """,
}), {"main.py": "from libA import MDF\nm = MDF()\nprint('ok', m.d)\n"})

# S04b cross-module chain inside dependency
scenario("S04b_cross_module_chain", dict(HX, **{
    "libA/__init__.py": "from .api import top\n",
    "libA/api.py": "from .helpers import helper\ndef top():\n    return helper()\n",
    "libA/helpers.py": "import heavyx\ndef helper():\n    return heavyx.compute()\n",
}), {"main.py": "import libA\nprint('ok', libA.top())\n"})

# S05 callback passed without call (local direct)
scenario("S05_callback", HX, {"main.py": """
    import threading
    import heavyx
    def work():
        print('ok', heavyx.compute())
    t = threading.Thread(target=work); t.start(); t.join()
"""})

# S06 dunder methods (__enter__) / property
scenario("S06_enter", HX, {"main.py": """
    import heavyx
    class C:
        def __enter__(self):
            self.v = heavyx.compute(); return self
        def __exit__(self, *a): pass
    with C() as c:
        print('ok', c.v)
"""})

# S07 import a.b + a.b.f() at module level
scenario("S07_import_dotted", HX, {"main.py": "import heavyx.sub\nprint('ok', heavyx.sub.f())\n"})

# S08 bare decorator from package in dependency
scenario("S08_bare_decorator", lib("""
    from heavyx import jit
    @jit
    def f(x): return x
"""), {"main.py": "import libA\nprint('ok', libA.f(1))\n"})

# S09 lowercase class used with isinstance
scenario("S09_lowercase_class", lib("""
    from heavyx import ndarray
    def is_arr(x): return isinstance(x, ndarray)
"""), {"main.py": "import libA\nprint('ok', libA.is_arr(1))\n"})

# S10 uppercase constant
scenario("S10_upper_constant", lib("""
    from heavyx import MAX_SIZE
    LIMIT = MAX_SIZE * 2
"""), {"main.py": "import libA\nprint('ok', libA.LIMIT)\n"})

# S11 from pkg import submodule; attribute at module level
scenario("S11_from_import_submodule", lib("""
    from heavyx import plotting
    STYLE = plotting.DEFAULT_STYLE
"""), {"main.py": "import libA\nprint('ok', libA.STYLE)\n"})

# S12 try/except ImportError but actually used
scenario("S12_protected_but_used", HX, {"main.py": """
    try:
        import heavyx
    except ImportError:
        heavyx = None
    def main():
        print('ok', heavyx.compute())
    main()
"""})

# S13 try/except ImportError that re-raises
scenario("S13_protected_reraise", HX, {"main.py": """
    try:
        import heavyx
    except ImportError as e:
        raise RuntimeError('heavyx required') from e
    def main():
        print('ok', heavyx.compute())
    main()
"""})

# S15 default argument evaluated at def time
scenario("S15_default_arg", lib("""
    import heavyx
    def f(x=heavyx.make()): return x
"""), {"main.py": "import libA\nprint('ok')\n"})

# S16 lowercase function passed as value at module level in dep (alias)
scenario("S16_alias_call", lib("""
    import heavyx
    _c = heavyx.compute
    VALUE = _c()
"""), {"main.py": "import libA\nprint('ok', libA.VALUE)\n"})
