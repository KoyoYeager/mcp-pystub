import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.test_round2 import _write
from mcp_pystub.verifier import verify
from mcp_pystub.analyzer import analyze

d = Path(tempfile.mkdtemp())
sp, proj = d / "sp", d / "proj"
sp.mkdir(); proj.mkdir(); (proj / "pyproject.toml").write_text("")
_write(sp, {"hv/__init__.py": "class Frame:\n    pass\n", "lib/__init__.py": "import hv\ndef f(x):\n    return isinstance(x, hv.Frame)\n"})
_write(proj, {"main.py": "import lib\nprint('ok', lib.f(1))\n"})
print(verify(str(proj / "main.py"), str(sp), run_entry=True))

d2 = Path(tempfile.mkdtemp())
sp2, p2 = d2 / "sp", d2 / "proj"
sp2.mkdir(); p2.mkdir(); (p2 / "pyproject.toml").write_text("")
_write(sp2, {"pdx/__init__.py": "def read_csv(p):\n    return p\n", "lib/__init__.py": "import sys\ndef maybe():\n    if 'pdx' not in sys.modules:\n        return 'no-pdx'\n    import pdx\n    return pdx.read_csv('x')\n"})
_write(p2, {"main.py": "import lib\nprint('ok', lib.maybe())\n"})
r = analyze(str(p2 / "main.py"), python_path=str(sp2))
print([(p["package_name"], p["reason"]) for p in r["required"]])
