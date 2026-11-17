from harness import scenario

HX = {
    "heavyx/__init__.py": """
        from .core import *
        from . import sub, plotting
        __version__ = "1.0"
        class DataFrame:
            def __init__(self, *a): self.a = a
        class Timestamp: pass
        class ndarray: pass
        MAX_SIZE = 10
        def compute(): return 42
        def read(): return [1]
        def make(): return 1
        def frame(): return 1
        def jit(f): return f
    """,
    "heavyx/core.py": "CORE = 1\n",
    "heavyx/sub.py": "def f(): return 'sub-ok'\n",
    "heavyx/plotting.py": "DEFAULT_STYLE = 'x'\n",
}


def lib(code):
    d = dict(HX)
    d["libA/__init__.py"] = code
    return d


