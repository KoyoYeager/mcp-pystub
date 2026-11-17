"""Checker5: conditional stub object model (_M) attacks. alt_plot.py enters the condition (Signal.plot).
Expected: LOUD (rc 70) or SAME. SILENT_* / CHANGED_OTHER = broken guarantee."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

OTHER = {"other/__init__.py": "def show(obj):\n    return obj.plot()\n"}
MAIN_P = "import sig, other\nclass P:\n    def plot(self): return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n"
ALT_P = "import sig, other\nprint('ok', other.show(sig.Signal(3)))\n"

HVSRC = '''
import os, enum, dataclasses, typing, abc
LEVEL = int(os.environ.get('HVL_X', '3'))
NAMES = tuple(os.environ.get('HVN_X', 'a,b').split(','))
DEF_NONE = None
def render(v):
    """Render docstring."""
    return 'R%s' % v
class Base:
    pass
class Renderer(Base):
    """Fast renderer."""
    __slots__ = ('a',)
    DEFAULT = None
    FLAG = True
    ZERO = 0
    MODES = ('x', 'y')
    TABLE = {'k': 1}
    BACKEND = os.environ.get('HVB_X', 'agg')
    class Options:
        size = 3
    @property
    def prop(self):
        return 1
    @classmethod
    def create(cls):
        return cls()
    @staticmethod
    def helper():
        return 2
    def render(self, v):
        """method doc"""
        return 'R%s' % v
class Color(enum.Enum):
    RED = 1
    GREEN = 2
@dataclasses.dataclass
class Cfg:
    size: int = 3
class Point(typing.NamedTuple):
    x: int = 0
'''

def case(name, expr, pre=""):
    cscenario(name, {
        **OTHER,
        "hv/__init__.py": HVSRC,
        "sig/__init__.py": f"""
            class Signal:
                def __init__(self, v): self.v = v
                def value(self): return self.v
                def plot(self):
                    import hv, inspect, dataclasses, abc
                    {pre}
                    return 'res=%r' % ({expr},)
        """,
    }, {"main.py": MAIN_P, "alt_plot.py": ALT_P}, entries=("main.py", "alt_plot.py"))

case("S01_isinstance_value", "isinstance(hv.LEVEL, int)")
case("S02_dict_get_value", "{3: 'three'}.get(hv.LEVEL, 'unknown')")
case("S02b_set_membership", "hv.LEVEL in {3, 4}")
case("S03_callable_value", "callable(hv.LEVEL)")
case("S04_func_doc", "hv.render.__doc__")
case("S05_nested_class_name", "hv.Renderer.Options.__name__")
case("S06_classmethod_name", "hv.Renderer.create.__name__")
case("S06b_method_name", "hv.Renderer.render.__name__")
case("S06c_method_doc", "hv.Renderer.render.__doc__")
case("S07_isinstance_property", "isinstance(hv.Renderer.prop, property)")
case("S08_enum_getattr_missing", "getattr(hv.Color, 'PURPLE', None) is None")
case("S08b_enum_member_isinstance", "isinstance(hv.Color.RED, hv.Color)")
case("S08c_enum_hasattr_missing", "hasattr(hv.Color, 'PURPLE')")
case("S09_classattr_none_is", "hv.Renderer.DEFAULT is None")
case("S09b_classattr_true_is", "hv.Renderer.FLAG is True")
case("S09c_module_none_is", "hv.DEF_NONE is None")
case("S10_isclass_func", "inspect.isclass(hv.render)")
case("S10b_isfunction", "inspect.isfunction(hv.render)")
case("S11_type_name_value", "type(hv.LEVEL).__name__")
case("S12_issubclass_stub_classes", "issubclass(hv.Renderer, hv.Base)")
case("S13_mro_len", "len(hv.Renderer.__mro__)")
case("S14_slots_hasattr", "hasattr(hv.Renderer, '__slots__')")
case("S15_is_dataclass", "dataclasses.is_dataclass(hv.Cfg)")
case("S16_dataclass_default", "hv.Cfg.size")
case("S17_namedtuple_fields", "hv.Point._fields")
case("S18_isinstance_tuple_classattr", "isinstance(hv.Renderer.MODES, tuple)")
case("S19_classattr_dict_get", "{'agg': 'A'}.get(hv.Renderer.BACKEND, 'none')")
case("S20_func_name_module", "(hv.render.__name__, hv.render.__module__, hv.render.__qualname__)")
case("S21_type_is_value", "type(hv.LEVEL) is int")
case("S22_isabstract", "inspect.isabstract(hv.Renderer)")
case("S23_dir_len", "'render' in dir(hv.Renderer)")
case("S24_vars_keys", "'render' in vars(hv.Renderer)")
case("S25_try_attr_missing", "hv.Renderer.nope", pre="pass")
case("S26_getattr_default_missing_plain", "getattr(hv.Renderer, 'nope', 'dflt')")
case("S27_staticmethod_isfunc", "inspect.isfunction(hv.Renderer.helper)")
case("S28_module_attr_missing", "getattr(hv, 'nope', 'dflt')")
case("S29_names_tuple_isinstance", "isinstance(hv.NAMES, tuple)")
case("S30_id_eq_value_identity", "hv.LEVEL is hv.LEVEL")

for n, f in RESULTS:
    print("SUMMARY", n, f)
