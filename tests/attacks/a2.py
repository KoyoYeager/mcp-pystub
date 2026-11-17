from h import scenario, RESULTS
HV = {"hv/__init__.py": "def compute(): return 42\nCONST = 1\n"}
def sp(**extra):
    d = dict(HV); d.update(extra); return d

scenario("B01_thirdparty_register_decorator", sp(**{"plug/__init__.py": """
    _REG = []
    def register(f):
        _REG.append(f)
        return f
    def run_all():
        return [f() for f in _REG]
 """}), {"main.py": """
    import hv, plug
    @plug.register
    def h():
        return hv.compute()
    print("ok", plug.run_all())
 """})

scenario("B02_dispatch_table", sp(), {"main.py": """
    import hv
    def a(): return hv.compute()
    TABLE = {"a": a}
    print("ok", TABLE["a"]())
 """})

scenario("B05_property", sp(), {"main.py": """
    import hv
    class C:
        @property
        def v(self): return hv.compute()
    def get(o): return o.v
    print("ok", get(C()))
 """})

scenario("B06_async_gen", sp(), {"main.py": """
    import asyncio, hv
    async def f(): return hv.compute()
    def g():
        yield hv.compute()
    print("ok", asyncio.run(f()), list(g()))
 """})

scenario("B07_override_called_by_lib", sp(**{"fw/__init__.py": """
    class Base:
        def run(self):
            return self.hook()
        def hook(self):
            return 0
 """}), {"main.py": """
    import hv, fw
    class Mine(fw.Base):
        def hook(self): return hv.compute()
    print("ok", Mine().run())
 """})

scenario("B08_multifile_local", sp(), {"main.py": """
    import a
    print("ok", a.go())
 """, "a.py": "import b\ndef go():\n    return b.inner()\n", "b.py": "import hv\ndef inner():\n    return hv.compute()\n"})

scenario("B09_import_module_literal", sp(**{"hv/sub.py": "def f(): return 'sub'\n"}), {"main.py": """
    import importlib, hv
    m = importlib.import_module("hv.sub")
    print("ok", m.f())
 """})

scenario("B10_import_module_dynamic", sp(**{"lib10/__init__.py": "import hv\n"}), {"main.py": """
    import importlib, lib10
    name = "h" + "v"
    m = importlib.import_module(name)
    print("ok", m.compute())
 """})

scenario("B10b_dunder_import_dynamic", sp(**{"lib10/__init__.py": "import hv\n"}), {"main.py": """
    import lib10, sys
    name = "".join(["h", "v"])
    __import__(name)
    print("ok", sys.modules[name].compute())
 """})

scenario("B11_lazy_getattr_pkg", {"lazy/__init__.py": """
    import importlib
    def __getattr__(name):
        if name == "sub":
            return importlib.import_module("lazy.sub")
        raise AttributeError(name)
 """, "lazy/sub.py": "VALUE = 'lazy-sub'\n"}, {"main.py": """
    import lazy
    print("ok", lazy.sub.VALUE)
 """})

scenario("B13_star_import_call", sp(), {"main.py": """
    from hv import *
    print("ok", compute())
 """})

scenario("B21_const_mutated_at_import", {"hv/__init__.py": """
    CONST = 1
    def _init():
        global CONST
        CONST = 2
    _init()
    def compute(): return 42
 """}, {"main.py": """
    import hv
    print("ok", hv.CONST)
 """})

scenario("B21b_const_set_by_other_module", {"hv/__init__.py": """
    CONST = 1
    from . import _patch
    def compute(): return 42
 """, "hv/_patch.py": "import hv\nhv.CONST = 3\n"}, {"main.py": """
    import hv
    print("ok", hv.CONST)
 """})

scenario("B23_class_attr_function_alias", sp(), {"main.py": """
    import hv
    class C:
        handler = hv.compute
        def go(self): return self.handler()
    print("ok", C().go())
 """})

scenario("B33_globals_dispatch", sp(), {"main.py": """
    import hv
    def handle_x(): return hv.compute()
    def dispatch(k): return globals()["handle_" + k]()
    print("ok", dispatch("x"))
 """})

scenario("B34_getattr_sys_modules", sp(), {"main.py": """
    import sys, hv
    def handle_x(): return hv.compute()
    def dispatch(k): return getattr(sys.modules[__name__], "handle_" + k)()
    print("ok", dispatch("x"))
 """})

scenario("B36_exec_string", sp(**{"lib36/__init__.py": "import hv\n"}), {"main.py": """
    import lib36
    ns = {}
    exec("import hv\nr = hv.compute()", ns)
    print("ok", ns["r"])
 """})

scenario("B38_methodcaller", sp(), {"main.py": """
    import operator, hv
    class C:
        def go(self): return hv.compute()
    print("ok", operator.methodcaller("go")(C()))
 """})

scenario("B39_entry_points", {
  "hv/__init__.py": "def compute(): return 42\n",
  "hv_plugin/__init__.py": "import hv\ndef plugin(): return hv.compute()\n",
  "hostlib/__init__.py": """
    import hv_plugin  # static import somewhere (e.g. type registration)
    from importlib.metadata import entry_points
    def run():
        return [ep.load()() for ep in entry_points(group="host.plugins")]
  """,
  "hv_plugin-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: hv_plugin\nVersion: 1.0\n",
  "hv_plugin-1.0.dist-info/entry_points.txt": "[host.plugins]\np = hv_plugin:plugin\n",
 }, {"main.py": """
    import hostlib
    print("ok", hostlib.run())
 """})

scenario("B40_bound_method_stored", sp(), {"main.py": """
    import hv
    class C:
        def go(self): return hv.compute()
    c = C()
    cbs = []
    cbs.append(c.go)
    print("ok", cbs[0]())
 """})

scenario("B41_str_format_method_name", sp(), {"main.py": """
    import hv
    class C:
        def do_go(self): return hv.compute()
        def run(self, cmd): return getattr(self, "do_" + cmd)()
    print("ok", C().run("go"))
 """})

scenario("B42_pkg_class_subclass_in_lib_used_in_except", {"hv/__init__.py": """
    class HvError(Exception): pass
    class Sub(HvError): pass
 """, "lib42/__init__.py": """
    import hv
    def f(x):
        try:
            return 1 / x
        except hv.HvError:
            return -1
 """}, {"main.py": """
    import lib42
    try:
        lib42.f(0)
    except ZeroDivisionError:
        print("ok zde")
 """})

for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
