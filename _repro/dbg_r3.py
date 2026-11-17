import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_conditional import _write
from mcp_pystub.analyzer import analyze

d = Path(tempfile.mkdtemp())
sp = {
    "gx/__init__.py": "def draw(v):\n    return 'drawn %s' % v\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v):\n        self.v = v\n    def value(self):\n        return self.v\n    def plot(self):\n        import gx\n        return gx.draw(self.v)\n",
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}
_write(d / "sp", sp)
_write(d / "proj", {"main.py": "import sig, other\nclass P:\n    def plot(self):\n        return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n", "pyproject.toml": ""})
r = analyze(str(d / "proj" / "main.py"), python_path=str(d / "sp"), include_small_conditional=True)
for k in ("stubbable", "nofollow", "required", "conditional"):
    print(k, [(p["package_name"], p["reason"][:150]) for p in r[k]])
