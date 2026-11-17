"""Conditional tier attacks."""
from hc import cscenario, RESULTS

HV_SIG = {
    "hv/__init__.py": "import os\nMODE = os.path.join('fa', 'st')\nTABLE = dict(a=1)\nENABLED = bool(os.sep)\ndef draw(v):\n    return 'drawn %s' % v\n",
}
# C01: conditional path reads non-literal values (format / subscript / truth) -> stub _M class silently
cscenario("C01_silent_nonlit_values", {
    **HV_SIG,
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                if hv.ENABLED:
                    return "mode=%s t=%s" % (hv.MODE, hv.TABLE['a'])
                return "off"
    """,
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}, {
    "main.py": "import sig, other\nclass P:\n    def plot(self): return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n",
    "alt_plot.py": "import sig, other\nprint('ok', other.show(sig.Signal(3)))\n",
}, entries=("main.py", "alt_plot.py"))

# C02: two distinct entries to the same heavy-using helper: only one gets listed?
cscenario("C02_two_entries_collapse", {
    "hv/__init__.py": "def render(v):\n    return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self): return self._render()
            def export(self): return self._render()
            def _render(self):
                import hv
                return hv.render(self.v)
    """,
    "aa/__init__.py": "def show(obj):\n    return obj.plot()\n",
    "zz/__init__.py": "def export_all(objs):\n    out = []\n    for o in objs:\n        try:\n            out.append(o.export())\n        except Exception:\n            out.append('n/a')\n    return out\n",
}, {
    "main.py": "import sig, aa, zz\nclass P:\n    def plot(self): return 'p'\n    def export(self): return 'e'\nprint('ok', sig.Signal(3).value(), aa.show(P()), zz.export_all([P()]))\n",
    "alt_export.py": "import sig, zz\nprint('ok', zz.export_all([sig.Signal(3)]))\n",
}, entries=("main.py", "alt_export.py"))

SIGX = {
    "hv/__init__.py": "def render(v):\n    return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                return hv.render(self.v)
    """,
}
MAIN_P = "import sig, other\nclass P:\n    def plot(self): return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n"
ALT_P = "import sig, other\nprint('ok', other.show(sig.Signal(3)))\n"

# C03: swallowed by except RuntimeError (narrow, but PystubConditionalStubError is RuntimeError)
cscenario("C03_except_runtimeerror", {**SIGX,
    "other/__init__.py": "def show(obj):\n    try:\n        return obj.plot()\n    except RuntimeError:\n        return 'unavailable'\n",
}, {"main.py": MAIN_P, "alt.py": ALT_P}, entries=("main.py", "alt.py"))

# C04: swallowed by contextlib.suppress
cscenario("C04_contextlib_suppress", {**SIGX,
    "other/__init__.py": "import contextlib\ndef show(obj):\n    r = 'none'\n    with contextlib.suppress(Exception):\n        r = obj.plot()\n    return r\n",
}, {"main.py": MAIN_P, "alt.py": ALT_P}, entries=("main.py", "alt.py"))

# C05: swallowed two frames up (caller of the dispatcher)
cscenario("C05_swallow_two_frames_up", {**SIGX,
    "other/__init__.py": "def _draw(obj):\n    return obj.plot()\ndef show(obj):\n    try:\n        return _draw(obj)\n    except Exception:\n        return 'unavailable'\n",
}, {"main.py": MAIN_P, "alt.py": ALT_P}, entries=("main.py", "alt.py"))

# C06: project code swallows; project never names the entry
cscenario("C06_project_swallows", {**SIGX,
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}, {"main.py": MAIN_P,
    "alt.py": "import sig, other\ntry:\n    r = other.show(sig.Signal(3))\nexcept Exception:\n    r = 'fallback'\nprint('ok', r)\n"},
    entries=("main.py", "alt.py"))

# C07: decorator-registered function that the library itself runs at import time
cscenario("C07_registry_run_at_import", {
    "hv/__init__.py": "def setup():\n    return 'hv-setup'\n",
    "lib/__init__.py": """
        _hooks = []
        STATE = []
        def hook(f):
            _hooks.append(f)
            return f
        @hook
        def _init_hv():
            import hv
            STATE.append(hv.setup())
        @hook
        def _init_basic():
            STATE.append('basic')
        for _f in _hooks:
            try:
                _f()
            except Exception:
                STATE.append('failed')
    """,
}, {"main.py": "import lib\nprint('ok', lib.STATE)\n"})

# C07b: same, without try (loud at import)
cscenario("C07b_registry_run_at_import_notry", {
    "hv/__init__.py": "def setup():\n    return 'hv-setup'\n",
    "lib/__init__.py": """
        _hooks = []
        STATE = []
        def hook(f):
            _hooks.append(f)
            return f
        @hook
        def _init_hv():
            import hv
            STATE.append(hv.setup())
        for _f in _hooks:
            _f()
    """,
}, {"main.py": "import lib\nprint('ok', lib.STATE)\n"})

# C08: atexit.register decorator in library (runs at exit always)
cscenario("C08_atexit_decorator", {
    "hv/__init__.py": "def bye():\n    return 'hv-bye'\n",
    "lib/__init__.py": """
        import atexit
        @atexit.register
        def _goodbye():
            import hv
            print('exit', hv.bye())
        def f():
            return 1
    """,
}, {"main.py": "import lib\nprint('ok', lib.f())\n"})

# C09: module-level-called function with env-constant branch (always taken at import)
cscenario("C09_import_time_branch", {
    "hv/__init__.py": "def setup():\n    return 'hv-setup'\n",
    "lib/__init__.py": """
        import sys
        STATE = []
        def _init():
            if sys.version_info >= (3, 0):
                import hv
                STATE.append(hv.setup())
        _init()
    """,
}, {"main.py": "import lib\nprint('ok', lib.STATE)\n"})

# C10: project calls via methodcaller (string) -> project_uses_count misses
cscenario("C10_project_methodcaller", {**SIGX, "other/__init__.py": "def show(obj):\n    return obj.plot()\n"}, {
    "main.py": "import sig, other, operator\nclass P:\n    def plot(self): return 'p'\nf = operator.methodcaller('pl' + 'ot')\nprint('ok', sig.Signal(3).value(), other.show(P()), f(P()))\n",
    "alt.py": "import sig, operator\nprint(operator.methodcaller('plot')(sig.Signal(1)))\n",
}, entries=("main.py", "alt.py"))

# C11: method alias under another name (draw = plot)
cscenario("C11_alias_method_name", {
    "hv/__init__.py": "def render(v):\n    return 'R%s' % v\n",
    "sig/__init__.py": """
        class Signal:
            def __init__(self, v): self.v = v
            def value(self): return self.v
            def plot(self):
                import hv
                return hv.render(self.v)
            draw = plot
    """,
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}, {"main.py": MAIN_P, "alt.py": "import sig\nprint('ok', sig.Signal(3).draw())\n"}, entries=("main.py", "alt.py"))

# C12: conditional path returns stub class; caller reads attribute silently
cscenario("C12_returned_stub_object_used", {
    "hv/__init__.py": "import os\nclass Backend:\n    name = os.path.basename('hv/real')\n    def ok(self): return True\n",
    "lib/__init__.py": """
        def get_backend(name):
            if name == 'hv':
                import hv
                return hv.Backend
            return None
        def describe(name):
            b = get_backend(name)
            return 'backend=%s' % (b.name if b is not None else 'none')
    """,
}, {"main.py": "import lib\nprint('ok', lib.describe('x'))\n", "alt.py": "import lib\nprint('ok', lib.describe('hv'))\n"},
    entries=("main.py", "alt.py"))

# C13: hasattr-based feature detection in conditional path, broad except fallback
cscenario("C13_hasattr_detection_cond", {
    "hv/__init__.py": "def fast(v):\n    return v * 2\n",
    "lib/__init__.py": """
        def compute(v, accel=False):
            if accel:
                import hv
                if hasattr(hv, 'fast'):
                    try:
                        return ('fast', hv.fast(v))
                    except Exception:
                        return ('slow', v + v)
            return ('plain', v + v)
    """,
}, {"main.py": "import lib\nprint('ok', lib.compute(3))\n", "alt.py": "import lib\nprint('ok', lib.compute(3, accel=True))\n"},
    entries=("main.py", "alt.py"))

# C14: iteration / comparison in conditional path -> TypeError without the notice, swallowed by except TypeError
cscenario("C14_noncall_ops_no_notice", {
    "hv/__init__.py": "import os\nITEMS = list(os.sep)\nLEVEL = len(os.sep)\n",
    "lib/__init__.py": """
        def items(want):
            if want:
                import hv
                try:
                    return [x for x in hv.ITEMS] + [hv.LEVEL > 0]
                except TypeError:
                    return []
            return None
    """,
}, {"main.py": "import lib\nprint('ok', lib.items(False))\n", "alt.py": "import lib\nprint('ok', lib.items(True))\n"},
    entries=("main.py", "alt.py"))

# C15: try: import hv / except ImportError: hv = None inside conditional function -> presence check result changes?
cscenario("C15_presence_in_cond", {
    "hv/__init__.py": "def fast(v):\n    return v * 2\n",
    "lib/__init__.py": """
        def info(detail):
            if detail:
                try:
                    import hv
                except ImportError:
                    hv = None
                return 'hv available' if hv is not None else 'hv missing'
            return 'no detail'
        def use(v):
            if v > 100:
                import hv
                return hv.fast(v)
            return v
    """,
}, {"main.py": "import lib\nprint('ok', lib.info(False), lib.use(1))\n", "alt.py": "import lib\nprint('ok', lib.info(True))\n"},
    entries=("main.py", "alt.py"))

print("\n".join(f"{n}: {f}" for n, f in RESULTS))
