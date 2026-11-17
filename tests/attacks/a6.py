from h import scenario, RESULTS
PD = {"pdx/__init__.py": "class DataFrame:\n    def __init__(self, d):\n        self.d = d\ndef read_csv(p):\n    return DataFrame(p)\n"}
def sp(lib):
    d = dict(PD); d["lib/__init__.py"] = lib; return d

scenario("E01_readme_case", sp("""
    import pdx
    class Client:
        def info(self):
            return "info"
        def to_frame(self):
            return pdx.DataFrame([1])
 """), {"main.py": "import lib\nprint('ok', lib.Client().info())\n"})

scenario("E02_common_method_name", sp("""
    import pdx
    class Client:
        def info(self):
            return "info"
        def read(self):
            return pdx.read_csv("x")
 """), {"main.py": "import io, lib\nc = lib.Client()\nprint('ok', c.info(), io.StringIO('s').read())\n"})

scenario("E03_generic_base", sp("""
    import pdx
    from typing import Generic, TypeVar
    T = TypeVar("T")
    class Box(Generic[T]):
        def get(self):
            return "box"
        def as_frame(self):
            return pdx.DataFrame([1])
 """), {"main.py": "import lib\nprint('ok', lib.Box().get())\n"})

scenario("E04_unknown_decorator_lib", dict(PD, **{"deco/__init__.py": "def register(f):\n    return f\n", "lib/__init__.py": """
    import pdx, functools
    def logged(f):
        @functools.wraps(f)
        def w(*a, **k):
            return f(*a, **k)
        return w
    @logged
    def unused():
        return pdx.read_csv("x")
    def used():
        return "used"
 """}), {"main.py": "import lib\nprint('ok', lib.used())\n"})

scenario("E05_dunder_methods_of_instantiated", sp("""
    import pdx
    class Client:
        def info(self):
            return "info"
        def __repr__(self):
            return repr(pdx.DataFrame([1]))
 """), {"main.py": "import lib\nprint('ok', lib.Client().info())\n"})

scenario("E06_module_level_getattr_dynamic_in_lib", sp("""
    import pdx
    def _handler_a():
        return "a"
    def _other():
        return pdx.read_csv("x")
    def dispatch(name):
        return globals()["_handler_" + name]()
 """), {"main.py": "import lib\nprint('ok', lib.dispatch('a'))\n"})

scenario("E07_method_name_get", sp("""
    import pdx
    class Store:
        def get(self, k):
            return pdx.read_csv(k)
    class Client:
        def info(self):
            return "info"
    _default = Store()
 """), {"main.py": "import lib\nd = {}\nprint('ok', lib.Client().info(), d.get('x'))\n"})

scenario("E08_protected_import_unused", sp("""
    try:
        import pdx
    except ImportError:
        pdx = None
    def info():
        return "info"
    def to_frame():
        return pdx.DataFrame([1])
 """), {"main.py": "import lib\nprint('ok', lib.info())\n"})

for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
