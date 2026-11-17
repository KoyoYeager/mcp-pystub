"""Checker5: proven-tier attacks on the ANALYZED entry (main.py executes the operation for real)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

HV = {"hv/__init__.py": """
    class Renderer:
        __slots__ = ('a',)
        __custom_hook__ = 1
        def render(self, v):
            return 'R%s' % v
    def native():
        return 'n'
"""}

def case(name, libexpr):
    cscenario(name, {**HV, "lib/__init__.py": f"import hv\ndef check():\n    return {libexpr}\n"},
              {"main.py": "import lib\nprint('ok', lib.check())\n"})

case("P01_hasattr_slots", "hasattr(hv.Renderer, '__slots__')")
case("P02_hasattr_custom_dunder", "hasattr(hv.Renderer, '__custom_hook__')")
case("P03_getattr_dunder_default", "getattr(hv.Renderer, '__custom_hook__', None) is None")
case("P04_hasattr_init_defined", "'__init__' in hv.Renderer.__dict__")
case("P05_hasattr_render", "hasattr(hv.Renderer, 'render')")

# P06: class-decorator registry (K09 variant with a print that differs instead of crash)
cscenario("P06_class_decorator_registry_silent", {**HV,
    "fmt/__init__.py": """
        _CLS = {}
        def backend(name):
            def deco(c):
                _CLS[name] = c
                return c
            return deco
        @backend('f')
        class FloatFmt:
            def __init__(self):
                import hv
                self.s = 'has_render=%s' % hasattr(hv.Renderer, '__slots__')
        def make(name):
            return _CLS[name]().s
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.make('f'))\n"})

# P07: inherited metaclass registry, plugin uses hv.native()
cscenario("P07_inherited_metaclass", {**HV,
    "fmt/__init__.py": """
        REG = {}
        class Meta(type):
            def __new__(m, name, bases, ns):
                c = super().__new__(m, name, bases, ns)
                REG[name] = c
                return c
        class Base(metaclass=Meta):
            def go(self): return 'base'
        class Native(Base):
            def go(self):
                import hv
                return hv.native()
        def run(name):
            return REG[name]().go()
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.run('Native'))\n"})

for n, f in RESULTS:
    print("SUMMARY", n, f)
