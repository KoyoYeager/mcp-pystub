"""Checker4: conditional tier after d61db4b (class ==/bool/str/format no longer stop) + high-confidence rule attacks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

OTHER = {"other/__init__.py": "def show(obj):\n    return obj.plot()\n"}
MAIN_P = "import sig, other\nclass P:\n    def plot(self): return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n"
ALT_P = "import sig, other\nprint('ok', other.show(sig.Signal(3)))\n"

# E01: class attribute (non-literal) read through the stub class -> silent placeholder class in str/format
cscenario("E01_class_attr_nonlit_format", {
    **OTHER,
    "hv/__init__.py": "import os\nclass Renderer:\n    BACKEND = os.environ.get('HVB_X', 'agg')\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                return 'backend=%s' % (hv.Renderer.BACKEND,)
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E02: class attribute compared with == in a branch -> silently takes other branch
cscenario("E02_class_attr_eq_branch", {
    **OTHER,
    "hv/__init__.py": "import os\nclass Renderer:\n    BACKEND = os.environ.get('HVB_X', 'agg')\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                if hv.Renderer.BACKEND == 'agg':
                    return 'agg path'
                return 'other path'
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E03: class attribute truthiness
cscenario("E03_class_attr_bool", {
    **OTHER,
    "hv/__init__.py": "import os\nclass Renderer:\n    ENABLED = bool(os.environ.get('HVB_X'))\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                if hv.Renderer.ENABLED:
                    return 'enabled'
                return 'disabled'
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E04: class __doc__ (dunder, never stopped)
cscenario("E04_class_doc", {
    **OTHER,
    "hv/__init__.py": "class Renderer:\n    \"\"\"Fast renderer.\"\"\"\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                return 'doc=%s' % (hv.Renderer.__doc__ or 'none')
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E05: enum class str
cscenario("E05_enum_class_str", {
    **OTHER,
    "hv/__init__.py": "import enum\nclass Color(enum.Enum):\n    RED = 1\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                return 'kind=%s' % (hv.Color,)
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E06: hasattr on a stub class on the CERTAIN path, while hv is conditional because of another use
cscenario("E06_certain_hasattr_cls", {
    **OTHER,
    "hv/__init__.py": "class Renderer:\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        import hv
        FAST = hasattr(hv.Renderer, 'fast_path')
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return (self.v, FAST)
            def plot(self):
                return hv.Renderer().render(self.v)
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# E07: getattr(cls, name, default) probe on certain path
cscenario("E07_certain_getattr_default_cls", {
    **OTHER,
    "hv/__init__.py": "class Renderer:\n    def render(self, v): return 'R%s' % v\n",
    "sig/__init__.py": """
        import hv
        class Signal:
            def __init__(self, v): self.v = v
            def value(self):
                f = getattr(hv.Renderer, 'fast_path', None)
                return (self.v, f is None)
            def plot(self):
                return hv.Renderer().render(self.v)
    """,
}, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

# ---- confidence high attacks: analyzed entry itself hits the stub
# H01: functools.singledispatch registered impl uses heavy; project calls the dispatcher
cscenario("H01_singledispatch", {
    "hv/__init__.py": "def fmt_float(v): return 'F%.1f' % v\n",
    "fmt/__init__.py": """
        import functools
        @functools.singledispatch
        def render(v):
            return 'generic %r' % (v,)
        @render.register(float)
        def _(v):
            import hv
            return hv.fmt_float(v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render(3.5))\n"})

# H02: decorator registry + dict lookup call inside a function (not module body)
cscenario("H02_registry_subscript_call", {
    "hv/__init__.py": "def fmt_float(v): return 'F%.1f' % v\n",
    "fmt/__init__.py": """
        _REG = {}
        def register(name):
            def deco(f):
                _REG[name] = f
                return f
            return deco
        @register('float')
        def _f(v):
            import hv
            return hv.fmt_float(v)
        @register('int')
        def _i(v):
            return 'I%d' % v
        def render(v):
            return _REG[type(v).__name__](v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render(3.5))\n"})

# H03: dynamic getattr method dispatch on self
cscenario("H03_getattr_self_dispatch", {
    "hv/__init__.py": "def fmt_float(v): return 'F%.1f' % v\n",
    "fmt/__init__.py": """
        class Formatter:
            def render(self, v):
                return getattr(self, 'fmt_' + type(v).__name__)(v)
            def fmt_float(self, v):
                import hv
                return hv.fmt_float(v)
            def fmt_int(self, v):
                return 'I%d' % v
        def render(v):
            return Formatter().render(v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render(3.5))\n"})

# H04: input-dependent branch in a private helper; analyzed entry's input takes the heavy side
cscenario("H04_branch_private_helper", {
    "hv/__init__.py": "def fmt_float(v): return 'F%.1f' % v\n",
    "fmt/__init__.py": """
        def _render(v):
            if isinstance(v, float):
                import hv
                return hv.fmt_float(v)
            return 'I%d' % v
        def process(v):
            return _render(v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.process(3.5))\n"})

# H05: callback list fired in a loop (function, not module body)
cscenario("H05_callback_list", {
    "hv/__init__.py": "def note(): return 'hv-note'\n",
    "ev/__init__.py": """
        _CBS = []
        def on_start(f):
            _CBS.append(f)
            return f
        @on_start
        def _hv_hook():
            import hv
            print('hook', hv.note())
        def start():
            for cb in _CBS:
                cb()
            return 'started'
    """,
}, {"main.py": "import ev\nprint('ok', ev.start())\n"})

# H06: __init_subclass__ plugin registry + factory by name
cscenario("H06_init_subclass_factory", {
    "hv/__init__.py": "def fmt(v): return 'HV%s' % v\n",
    "plug/__init__.py": """
        class Base:
            _reg = {}
            def __init_subclass__(cls, **kw):
                super().__init_subclass__(**kw)
                Base._reg[cls.__name__.lower()] = cls
            def run(self, v): return 'base'
        class Fast(Base):
            def run(self, v):
                import hv
                return hv.fmt(v)
        class Slow(Base):
            def run(self, v): return 'slow%s' % v
        def make(name):
            return Base._reg[name]()
    """,
}, {"main.py": "import plug\nprint('ok', plug.make('fast').run(2))\n"})

# H07: operator.methodcaller by constant name
cscenario("H07_methodcaller", {
    "hv/__init__.py": "def fmt(v): return 'HV%s' % v\n",
    "lib/__init__.py": """
        import operator
        class Doc:
            def __init__(self, v): self.v = v
            def export(self):
                import hv
                return hv.fmt(self.v)
        def export_all(objs):
            return [operator.methodcaller('export')(o) for o in objs]
        def make(v): return Doc(v)
    """,
}, {"main.py": "import lib\nprint('ok', lib.export_all([lib.make(1)]))\n"})

for n, f in RESULTS:
    print("SUMMARY", n, f)
