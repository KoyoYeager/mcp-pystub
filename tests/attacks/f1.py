"""Checker5: confidence=high rule attacks. main.py is the analyzed entry. If any COND/CHINT is high, main.py must be SAME."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

HV = {"hv/__init__.py": "def fmt_float(v): return 'F%.1f' % v\n"}
SIG = """
    class Signal:
        def __init__(self, v): self.v = v
        def value(self): return self.v
        def plot(self):
            import hv
            return hv.fmt_float(self.v)
"""
# K01: name dispatch two levels below the project call (project calls lib3.top, lib2._dispatch does o.plot())
cscenario("K01_dispatch_two_levels", {**HV, "sig/__init__.py": SIG,
    "lib2/__init__.py": "def _dispatch(objs):\n    return [o.plot() for o in objs]\ndef run(objs):\n    return _dispatch(objs)\n",
    "lib3/__init__.py": "import lib2\ndef top(objs):\n    return lib2.run(objs)\n",
}, {"main.py": "import sig, lib3\nprint('ok', sig.Signal(3).value(), lib3.top([sig.Signal(3.0)]))\n"})

# K01b: three private helpers in one module
cscenario("K01b_dispatch_three_levels", {**HV, "sig/__init__.py": SIG,
    "lib2/__init__.py": "def _a(objs):\n    return [o.plot() for o in objs]\ndef _b(objs):\n    return _a(objs)\ndef _c(objs):\n    return _b(objs)\ndef run(objs):\n    return _c(objs)\n",
}, {"main.py": "import sig, lib2\nprint('ok', lib2.run([sig.Signal(3.0)]))\n"})

# K02: registry decorator that registers via dict.update({k: f})
cscenario("K02_registry_update_dict", {**HV,
    "fmt/__init__.py": """
        _REG = {}
        def register(name):
            def deco(f):
                _REG.update({name: f})
                return f
            return deco
        @register('float')
        def _float(v):
            import hv
            return hv.fmt_float(v)
        def render(kind, v):
            return _REG[kind](v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render('float', 3.5))\n"})

# K03: registry via __setitem__ and a non-registry decorator name
cscenario("K03_registry_setitem_plainname", {**HV,
    "fmt/__init__.py": """
        _REG = {}
        def impl(f):
            _REG.__setitem__(f.__name__, f)
            return f
        @impl
        def flt(v):
            import hv
            return hv.fmt_float(v)
        def render(kind, v):
            return _REG[kind](v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render('flt', 3.5))\n"})

# K04: registry via setattr on a namespace + getattr(ns, name)
cscenario("K04_registry_setattr_ns", {**HV,
    "fmt/__init__.py": """
        import types
        NS = types.SimpleNamespace()
        def expose(f):
            setattr(NS, f.__name__, f)
            return f
        @expose
        def flt(v):
            import hv
            return hv.fmt_float(v)
        def render(kind, v):
            return getattr(NS, kind)(v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render('flt', 3.5))\n"})

# K05: decorator is a call result bound to a plain name
cscenario("K05_registrar_closure_name", {**HV,
    "fmt/__init__.py": """
        def make_registrar():
            reg = []
            def deco(f):
                reg.append(f)
                return f
            deco.items = reg
            return deco
        hook = make_registrar()
        @hook
        def flt():
            import hv
            return hv.fmt_float(1.5)
        def run_all():
            return [f() for f in hook.items]
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.run_all())\n"})

# K06: __getattr__ delegation in a lib wrapper
cscenario("K06_getattr_delegation", {**HV, "sig/__init__.py": SIG,
    "wrap/__init__.py": "class Wrapper:\n    def __init__(self, inner): self._inner = inner\n    def __getattr__(self, n): return getattr(self._inner, n)\n",
}, {"main.py": "import sig, wrap\nw = wrap.Wrapper(sig.Signal(2.0))\nprint('ok', sig.Signal(1).value(), w.plot())\n"})

# K07: functools.partial + getattr name dispatch in lib
cscenario("K07_partial_getattr", {**HV, "sig/__init__.py": SIG,
    "lib2/__init__.py": "import functools\ndef _call(name, o):\n    return getattr(o, name)()\ndef run(objs):\n    return list(map(functools.partial(_call, 'plot'), objs))\n",
}, {"main.py": "import sig, lib2\nprint('ok', lib2.run([sig.Signal(3.0)]))\n"})

PROP = """
    class Signal:
        def __init__(self, v): self.v = v
        def value(self): return self.v
        @property
        def fig(self):
            import hv
            return hv.fmt_float(self.v)
"""
# K08: property read on an unknown receiver in the project
cscenario("K08_property_read_unknown_recv", {**HV, "sig/__init__.py": PROP},
          {"main.py": "import sig\nfor o in [sig.Signal(2.0)]:\n    print('ok', o.fig)\n"})

# K08b: property read in a lib helper two levels down
cscenario("K08b_property_read_lib", {**HV, "sig/__init__.py": PROP,
    "lib2/__init__.py": "def _desc(o):\n    return o.fig\ndef run(objs):\n    return [_desc(o) for o in objs]\n",
    "lib3/__init__.py": "import lib2\ndef top(objs):\n    return lib2.run(objs)\n",
}, {"main.py": "import sig, lib3\nprint('ok', lib3.top([sig.Signal(2.0)]))\n"})

# K09: class decorator registering a class
cscenario("K09_class_decorator_registry", {**HV,
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
                self.s = hv.fmt_float(2.5)
        def make(name):
            return _CLS[name]().s
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.make('f'))\n"})

# K10: metaclass registers classes
cscenario("K10_metaclass_registry", {**HV,
    "fmt/__init__.py": """
        REG = []
        class Meta(type):
            def __init__(cls, name, bases, ns):
                super().__init__(name, bases, ns)
                if bases:
                    REG.append(cls)
        class Base(metaclass=Meta):
            pass
        class FloatPlugin(Base):
            def go(self):
                import hv
                return hv.fmt_float(1.5)
        def run_all():
            return [c().go() for c in REG]
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.run_all())\n"})

# K11: project getattr literal on unknown receiver
cscenario("K11_project_getattr_literal", {**HV, "sig/__init__.py": SIG}, {"main.py":
    "import sig\nfor o in [sig.Signal(2.0)]:\n    print('ok', getattr(o, 'plot')())\n"})

# K12: project operator.attrgetter
cscenario("K12_project_attrgetter", {**HV, "sig/__init__.py": SIG}, {"main.py":
    "import sig, operator\nfor o in [sig.Signal(2.0)]:\n    print('ok', operator.attrgetter('plot')(o)())\n"})

# K13: bound-method dispatch table built in a lib function
cscenario("K13_bound_method_table", {**HV, "sig/__init__.py": SIG,
    "lib2/__init__.py": "def _table(o):\n    return {'p': o.plot, 'v': o.value}\ndef run(o, k):\n    return _helper(o, k)\ndef _helper(o, k):\n    return _table(o)[k]()\n",
}, {"main.py": "import sig, lib2\nprint('ok', lib2.run(sig.Signal(2.0), 'p'))\n"})

# K14: importlib.metadata entry point plugin
cscenario("K14_entry_points", {**HV,
    "plug_a/__init__.py": "def go():\n    import hv\n    return hv.fmt_float(4.0)\n",
    "plug_a-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: plug_a\nVersion: 1.0\n",
    "plug_a-1.0.dist-info/entry_points.txt": "[myapp.plugins]\na = plug_a:go\n",
    "plug_a-1.0.dist-info/RECORD": "",
    "host/__init__.py": "from importlib.metadata import entry_points\ndef run():\n    return [ep.load()() for ep in entry_points(group='myapp.plugins')]\n",
}, {"main.py": "import host\nprint('ok', host.run())\n"})

# K15: mixin: project class inherits lib mixin + lib class
cscenario("K15_mixin_self", {**HV, "sig/__init__.py": SIG,
    "lib2/__init__.py": "class Show:\n    def show(self):\n        return self.plot()\n",
}, {"main.py": "import sig, lib2\nclass S(lib2.Show, sig.Signal):\n    pass\nprint('ok', S(2.0).show())\n"})

# K16: callback list via rebinding _CBS = _CBS + [f]
cscenario("K16_callbacks_rebind", {**HV,
    "ev/__init__.py": """
        _CBS = []
        def on(f):
            global _CBS
            _CBS = _CBS + [f]
            return f
        @on
        def _hook():
            import hv
            return hv.fmt_float(1.0)
        def start():
            return [cb() for cb in _CBS]
    """,
}, {"main.py": "import ev\nprint('ok', ev.start())\n"})

# K17: method decorator named 'listen' on a Bus instance
cscenario("K17_attr_decorator_listen", {**HV,
    "ev/__init__.py": """
        class Bus:
            def __init__(self): self.subs = []
            def listen(self, f):
                self.subs.append(f)
                return f
            def emit(self):
                return [f() for f in self.subs]
        bus = Bus()
        @bus.listen
        def _hook():
            import hv
            return hv.fmt_float(1.0)
        def start():
            return bus.emit()
    """,
}, {"main.py": "import ev\nprint('ok', ev.start())\n"})

# K18: lambdas in a module-level dict
cscenario("K18_lambda_table", {**HV,
    "fmt/__init__.py": """
        def _flt(v):
            import hv
            return hv.fmt_float(v)
        def _int(v):
            return 'I%d' % v
        TABLE = {'f': lambda v: _flt(v), 'i': lambda v: _int(v)}
        def render(k, v):
            return TABLE[k](v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render('f', 2.0))\n"})

for n, f in RESULTS:
    print("SUMMARY", n, f)
