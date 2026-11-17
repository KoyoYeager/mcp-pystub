"""Checker4: confidence=high candidates where the ANALYZED entry itself silently changes output (combines the
high-rule gaps of e1 H01/H04 with the d61db4b relaxation of e1 E01-E03)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import cscenario, RESULTS  # noqa

HV = {"hv/__init__.py": "import os\nclass Renderer:\n    BACKEND = os.environ.get('HVB_X', 'agg')\n"
                        "    ENABLED = bool(os.environ.get('HVB_X'))\n    def render(self, v): return 'R%s' % v\n"}

# H08: singledispatch impl formats a class attribute -> analyzed entry prints a different string, rc 0
cscenario("H08_singledispatch_silent", {**HV,
    "fmt/__init__.py": """
        import functools
        @functools.singledispatch
        def render(v):
            return 'generic %r' % (v,)
        @render.register(float)
        def _(v):
            import hv
            return 'float via %s' % (hv.Renderer.BACKEND,)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.render(3.5))\n"})

# H09: input-dependent branch in private helper tests a class flag -> analyzed entry takes the other branch
cscenario("H09_branch_flag_silent", {**HV,
    "fmt/__init__.py": """
        def _render(v):
            if isinstance(v, float):
                import hv
                if hv.Renderer.ENABLED:
                    return 'accelerated'
                return 'plain %.1f' % v
            return 'int %d' % v
        def process(v):
            return _render(v)
    """,
}, {"main.py": "import fmt\nprint('ok', fmt.process(3.5))\n"})

# H10: callback registered by decorator, compares class attribute with ==
cscenario("H10_callback_eq_silent", {**HV,
    "ev/__init__.py": """
        _CBS = []
        def on_start(f):
            _CBS.append(f)
            return f
        @on_start
        def _hv_hook():
            import hv
            return 'agg' if hv.Renderer.BACKEND == 'agg' else 'non-agg'
        def start():
            return [cb() for cb in _CBS]
    """,
}, {"main.py": "import ev\nprint('ok', ev.start())\n"})

for n, f in RESULTS:
    print("SUMMARY", n, f)
