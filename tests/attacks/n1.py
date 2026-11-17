"""Checker6 N1: new attacks on judgment 7 (i) transitive rule b only for non-inferred entries,
(ii) registration detection (class decorators strict / helper indirection / wrapper registered)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

HV = {"hv/__init__.py": "def render(v):\n    return 'R%s' % v\nclass Painter:\n    def paint(self, v):\n        return 'P%s' % v\n"}

# ---- (ii) registration through a helper function (no .append/.register attribute call inside the decorator itself)
cscenario("Y1_classdeco_helper_register", {**HV,
  "plug/__init__.py": "_B = {}\ndef _add(name, cls):\n    _B[name] = cls\n"
                      "def backend(name):\n    def deco(cls):\n        _add(name, cls)\n        return cls\n    return deco\n"
                      "def run(name, v):\n    return _B[name]().draw(v)\n",
  "sig/__init__.py": "import plug\n@plug.backend('hv')\nclass HvBackend:\n    def draw(self, v):\n        import hv\n        return hv.render(v)\n",
}, {"main.py": "import sig, plug\nprint('ok', plug.run('hv', 3))\n"})

cscenario("Y2_funcdeco_helper_subscribe", {**HV,
  "bus/__init__.py": "_H = {}\ndef _subscribe(evt, f):\n    _H.setdefault(evt, []).append(f)\n"
                     "def on(evt):\n    def deco(f):\n        _subscribe(evt, f)\n        return f\n    return deco\n"
                     "def emit(evt, v):\n    return [h(v) for h in _H.get(evt, [])]\n",
  "sig/__init__.py": "import bus\n@bus.on('draw')\ndef _draw(v):\n    import hv\n    return hv.render(v)\n",
}, {"main.py": "import sig, bus\nprint('ok', bus.emit('draw', 3))\n"})

cscenario("Y3_funcdeco_registers_wrapper", {**HV,
  "cmds/__init__.py": "import functools\n_C = {}\n"
                      "def command(name):\n    def deco(f):\n        @functools.wraps(f)\n        def wrapper(*a, **k):\n            return f(*a, **k)\n"
                      "        _C[name] = wrapper\n        return wrapper\n    return deco\n"
                      "def call(name, *a):\n    return _C[name](*a)\n",
  "sig/__init__.py": "import cmds\n@cmds.command('draw')\ndef _draw(v):\n    import hv\n    return hv.render(v)\n",
}, {"main.py": "import sig, cmds\nprint('ok', cmds.call('draw', 3))\n"})

cscenario("Y4_classdeco_registers_instance", {**HV,
  "plug/__init__.py": "_B = {}\n"
                      "def backend(name):\n    def deco(cls):\n        _B[name] = cls()\n        return cls\n    return deco\n"
                      "def run(name, v):\n    return _B[name].draw(v)\n",
  "sig/__init__.py": "import plug\n@plug.backend('hv')\nclass HvBackend:\n    def draw(self, v):\n        import hv\n        return hv.render(v)\n",
}, {"main.py": "import sig, plug\nprint('ok', plug.run('hv', 3))\n"})

cscenario("Y5_classdeco_registers_factory_lambda", {**HV,
  "plug/__init__.py": "_B = {}\n"
                      "def backend(name):\n    def deco(cls):\n        _B[name] = lambda *a: cls(*a)\n        return cls\n    return deco\n"
                      "def run(name, v):\n    return _B[name]().draw(v)\n",
  "sig/__init__.py": "import plug\n@plug.backend('hv')\nclass HvBackend:\n    def draw(self, v):\n        import hv\n        return hv.render(v)\n",
}, {"main.py": "import sig, plug\nprint('ok', plug.run('hv', 3))\n"})

cscenario("Y6_init_subclass_registry_attrs", {**HV,
  "base/__init__.py": "_R = {}\nclass Plugin:\n    def __init_subclass__(cls, key=None, **kw):\n        super().__init_subclass__(**kw)\n        _R[key] = cls\n"
                      "def run(k, v):\n    return _R[k]().draw(v)\n",
  "sig/__init__.py": "import dataclasses, base\n@dataclasses.dataclass\nclass HvPlugin(base.Plugin, key='hv'):\n    def draw(self, v):\n        import hv\n        return hv.render(v)\n",
}, {"main.py": "import sig, base\nprint('ok', base.run('hv', 3))\n"})

# ---- (i) inference between the condition entry and the use: does high hide a public call that reaches the entry?
OTHER = {"other/__init__.py": "def show(obj):\n    return obj.plot()\n"}
SIGX = "class Signal:\n    def __init__(self, v, eng=None): self.v = v; self.eng = eng\n    def value(self): return self.v\n" \
       "    def plot(self):\n        return self.eng.paint(self.v)\n"
cscenario("X1_entry_then_inferred_use", {**HV, **OTHER, "sig/__init__.py": SIGX,
  "lib2/__init__.py": "import other, sig, hv\ndef go(v):\n    return other.show(sig.Signal(v, hv.Painter()))\n"},
  {"main.py": "import sig, lib2\nprint('ok', sig.Signal(3).value(), lib2.go(3))\n"})
cscenario("X2_public_reaches_entry_via_dispatch_only", {**HV, **OTHER,
  "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n    def plot(self):\n        import hv\n        return hv.render(self.v)\n",
  "lib2/__init__.py": "import other, sig\ndef go(v):\n    return other.show(sig.Signal(v))\n"},
  {"main.py": "import sig, lib2\nprint('ok', sig.Signal(3).value(), lib2.go(3))\n"})
cscenario("X3_project_calls_public_that_dispatches_by_name_twice", {**HV, **OTHER,
  "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n    def plot(self):\n        import hv\n        return hv.render(self.v)\n",
  "lib2/__init__.py": "import other\ndef go(objs):\n    return [other.show(o) for o in objs]\n"},
  {"main.py": "import sig, lib2\nprint('ok', sig.Signal(3).value(), lib2.go([sig.Signal(3)]))\n"})


# ---- (ii) pandas の accessor 登録の形（pandas/core/accessor.py の _register_accessor を縮めたもの）
PDX = {"pdx/__init__.py": """
class CachedAccessor:
    def __init__(self, name, accessor):
        self._name = name
        self._accessor = accessor
    def __get__(self, obj, cls):
        if obj is None:
            return self._accessor
        return self._accessor(obj)
def _register_accessor(name, cls):
    def decorator(accessor):
        setattr(cls, name, CachedAccessor(name, accessor))
        cls._accessors.add(name)
        return accessor
    return decorator
class DataFrame:
    _accessors = set()
    def __init__(self, v):
        self.v = v
def register_dataframe_accessor(name):
    return _register_accessor(name, DataFrame)
"""}
cscenario("Y7_pandas_style_accessor", {**HV, **PDX,
  "geo/__init__.py": "import pdx\n@pdx.register_dataframe_accessor('geo')\nclass GeoAccessor:\n    def __init__(self, df):\n        self._df = df\n"
                     "    def area(self):\n        import hv\n        return hv.render(self._df.v)\n",
}, {"main.py": "import pdx, geo\ndf = pdx.DataFrame(3)\nprint('ok', df.geo.area())\n"})
cscenario("Y7b_pandas_style_accessor_unused", {**HV, **PDX,
  "geo/__init__.py": "import pdx\n@pdx.register_dataframe_accessor('geo')\nclass GeoAccessor:\n    def __init__(self, df):\n        self._df = df\n"
                     "    def area(self):\n        import hv\n        return hv.render(self._df.v)\n",
}, {"main.py": "import pdx, geo\ndf = pdx.DataFrame(3)\nprint('ok', df.v, 'geo' in pdx.DataFrame._accessors)\n", "alt.py": "import pdx, geo\nprint('ok', pdx.DataFrame(3).geo.area())\n"}, entries=("main.py", "alt.py"))

for n, f in RESULTS:
    print("SUMMARY", n, f)
