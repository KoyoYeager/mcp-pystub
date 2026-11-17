from hc import cscenario, RESULTS
SIGX = {
    "hv/__init__.py": "def render(v):\n    return 'R%s' % v\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v): self.v = v\n    def value(self): return self.v\n    def plot(self):\n        import hv\n        return hv.render(self.v)\n",
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}
# project triggers plot via getattr string on unknown object -> counted?
cscenario("C16_project_getattr_string", SIGX, {
    "main.py": "import sig, other, sys\nobjs = [sig.Signal(3)]\nname = 'pl' + 'ot'\nprint('ok', sig.Signal(3).value(), other.show(objs[0]) if len(sys.argv) > 5 else 0)\n",
    "alt.py": "import sig\nprint('ok', getattr(sig.Signal(3), 'plot')())\n",
}, entries=("main.py", "alt.py"))
