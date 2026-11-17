from h import scenario, RESULTS

# A1 except pkg.Error (stub class not BaseException)
scenario("A01_except_stub_class", {
 "hv/__init__.py": """
    class HvError(Exception): pass
    def compute(): return 1
 """}, {"main.py": """
    import hv
    def parse(s):
        try:
            return int(s)
        except hv.HvError:
            return "hv"
        except ValueError:
            return "bad"
    print("ok", parse("x"))
 """})

# A2 except alias of builtin exception re-exported by pkg
scenario("A02_except_alias_builtin", {
 "hv/__init__.py": """
    from json import JSONDecodeError
    DecodeError = ValueError
    def compute(): return 1
 """}, {"main.py": """
    import json, hv
    def load(s):
        try:
            return json.loads(s)
        except hv.JSONDecodeError:
            return "decode-error"
    def load2(s):
        try:
            return int(s)
        except hv.DecodeError:
            return "value-error"
    print("ok", load("{"), load2("x"))
 """})

# A3 isinstance with re-exported stdlib type
scenario("A03_isinstance_reexport", {
 "hv/__init__.py": """
    from collections import OrderedDict
    from pathlib import Path
    string_types = (str,)
    def compute(): return 1
 """}, {"main.py": """
    import collections, pathlib, hv
    print("ok", isinstance(collections.OrderedDict(), hv.OrderedDict), isinstance(pathlib.Path("."), hv.Path), isinstance("s", hv.string_types))
 """})

# A4 computed version_info compare
scenario("A04_version_info_compare", {
 "hv/__init__.py": """
    __version__ = "2.1.0"
    version_info = tuple(int(p) for p in __version__.split("."))
    def compute(): return 1
 """}, {"main.py": """
    import hv
    if hv.version_info >= (2, 0):
        print("ok new")
    else:
        print("ok old")
 """})

# A5 nofollow: library raises when optional dep missing
scenario("A05_nofollow_is_none_raise", {
 "lib5/__init__.py": """
    try:
        import hv
    except ImportError:
        hv = None
    def f():
        if hv is None:
            raise RuntimeError("hv is required")
        return "ok"
 """,
 "hv/__init__.py": "def compute(): return 1\n"}, {"main.py": """
    import lib5
    print(lib5.f())
 """})

# A6 nofollow: HAS flag
scenario("A06_nofollow_flag", {
 "lib6/__init__.py": """
    try:
        import hv
        HAS_HV = True
    except ImportError:
        HAS_HV = False
    def f():
        if not HAS_HV:
            raise ImportError("install hv")
        return "ok"
 """,
 "hv/__init__.py": "def compute(): return 1\n"}, {"main.py": """
    import lib6
    print(lib6.f())
 """})

# A7 local registry decorator
scenario("A07_local_registry_decorator", {
 "hv/__init__.py": "def compute(): return 42\n"}, {"main.py": """
    import hv
    REG = {}
    def register(f):
        REG[f.__name__] = f
        return f
    @register
    def handler():
        return hv.compute()
    print("ok", REG["handler"]())
 """})

# A8 __init_subclass__ registry
scenario("A08_init_subclass_registry", {
 "hv/__init__.py": "def compute(): return 42\n"}, {"main.py": """
    import hv
    class Plugin:
        registry = []
        def __init_subclass__(cls, **kw):
            super().__init_subclass__(**kw)
            Plugin.registry.append(cls)
    class HvPlugin(Plugin):
        def run(self):
            return hv.compute()
    for p in Plugin.registry:
        print("ok", p().run())
 """})

# A9 __subclasses__
scenario("A09_subclasses", {
 "hv/__init__.py": "def compute(): return 42\n"}, {"main.py": """
    import hv
    class Base:
        def run(self): return 0
    class Impl(Base):
        def run(self): return hv.compute()
    print("ok", [c().run() for c in Base.__subclasses__()])
 """})

# A10 classmethod factory cls()
scenario("A10_classmethod_factory", {
 "hv/__init__.py": "def compute(): return 42\n"}, {"main.py": """
    import hv
    class Base:
        @classmethod
        def create(cls):
            return cls()
    class Impl(Base):
        def go(self): return hv.compute()
    print("ok", Impl.create().go())
 """})

# A11 type(self)() / copy
scenario("A11_type_self", {
 "hv/__init__.py": "def compute(): return 42\n"}, {"main.py": """
    import hv, copy
    class A:
        def clone(self): return type(self)()
        def go(self): return 1
    class B(A):
        def go(self): return hv.compute()
    def make(): return B
    print("ok", make()().clone().go())
 """})

# A12 Enum member printed (const in class)
scenario("A12_enum_member", {
 "hv/__init__.py": """
    import enum
    class Color(enum.Enum):
        RED = 1
        BLUE = 2
    def compute(): return 1
 """}, {"main.py": """
    import hv
    print("ok", hv.Color.RED, 1 == hv.Color.RED)
 """})

# A13 constant reassigned later in module
scenario("A13_const_reassigned", {
 "hv/__init__.py": """
    LIMIT = 10
    LIMIT = 20
    MODE = "a"
    MODE += "b"
    import sys
    if sys.platform == "win32":
        SEP = "W"
    else:
        SEP = "P"
    def compute(): return 1
 """}, {"main.py": """
    import hv
    print("ok", hv.LIMIT, hv.MODE, hv.SEP)
 """})

# A14 class attribute alias then used via self
scenario("A14_class_attr_alias_self", {
 "hv/__init__.py": """
    DEFAULT_TIMEOUT = 5 * 60
    def compute(): return 1
 """}, {"main.py": """
    import hv
    class Client:
        timeout = hv.DEFAULT_TIMEOUT
        def total(self): return self.timeout + 1
    print("ok", Client().total())
 """})

# A15 dataclass default
scenario("A15_dataclass_default", {
 "hv/__init__.py": """
    DEFAULT = [1, 2][0] + 1
    def compute(): return 1
 """}, {"main.py": """
    import dataclasses, hv
    @dataclasses.dataclass
    class C:
        x: int = hv.DEFAULT
    print("ok", C().x * 2)
 """})

# A16 hasattr feature detection
scenario("A16_hasattr_feature", {
 "hv/__init__.py": "def compute(): return 1\n"}, {"main.py": """
    import hv
    FAST = hasattr(hv, "fast_path")
    print("ok", FAST, getattr(hv, "missing", None) is None)
 """})

# A17 try import X except ImportError X=None ; branch on X is not None (stub -> fake present)
scenario("A17_optional_is_not_none", {
 "lib17/__init__.py": """
    try:
        import hv
    except ImportError:
        hv = None
    def describe():
        return "with-hv" if hv is not None else "without-hv"
 """,
 "hv/__init__.py": "def compute(): return 1\n"}, {"main.py": """
    import lib17
    print("ok", lib17.describe())
 """})

# A18 version parse
scenario("A18_version_parse", {
 "hv/__init__.py": """
    from ._version import __version__
    def compute(): return 1
 """, "hv/_version.py": "__version__ = '3.4.5'\n"}, {"main.py": """
    import hv
    major = int(hv.__version__.split(".")[0])
    print("ok", major >= 3)
 """})

# A19 singledispatch register stub type
scenario("A19_singledispatch_register", {
 "hv/__init__.py": """
    class Frame: pass
    def compute(): return 1
 """}, {"main.py": """
    import functools, hv
    @functools.singledispatch
    def show(x): return "generic"
    @show.register(hv.Frame)
    def _(x): return "frame"
    print("ok", show(1))
 """})

# A20 issubclass with stub
scenario("A20_issubclass", {
 "hv/__init__.py": """
    class Frame: pass
    def compute(): return 1
 """}, {"main.py": """
    import hv
    print("ok", issubclass(int, hv.Frame))
 """})

for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
