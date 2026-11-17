"""Proven-tier attacks part 2 (package entry via -m, relative cycles, shadowing)."""
import sys
from pathlib import Path
import h
from h import scenario, RESULTS

# patch run_py to support running entry as module when entry is pkg/__main__.py
_orig = h.run_py
def run_py(paths, entry):
    import subprocess
    if entry.endswith("__main__.py"):
        mod = Path(entry).parent.name
        code = "import sys; sys.path[:0]=%r; import runpy; runpy.run_module(%r, run_name='__main__', alter_sys=True)" % (paths, mod)
        r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr).strip()
    return _orig(paths, entry)
h.run_py = run_py

scenario("N22_relative_entry_m", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"pkg/__init__.py": "", "pkg/__main__.py": "from .util import go\ngo()\n", "pkg/util.py": "import hv\ndef go():\n    print('go', hv.compute())\n"},
    entry="pkg/__main__.py")

scenario("N22b_relative_entry_m_unused_import", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"pkg/__init__.py": "import hv\n", "pkg/__main__.py": "from . import util\nutil.go()\n", "pkg/util.py": "from . import hv_user\ndef go():\n    print('go', hv_user.f())\n", "pkg/hv_user.py": "import hv\ndef f():\n    return hv.compute()\n"},
    entry="pkg/__main__.py")

scenario("N30_relative_cycle", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "from . import a\nfrom .a import fa\n",
    "lib/a.py": "from . import b\ndef fa():\n    return b.fb()\n",
    "lib/b.py": "from . import a as _a\nimport hv\ndef fb():\n    return hv.compute()\n",
}, {"main.py": "import lib\nprint('r', lib.fa())\n"})

scenario("N30b_deep_relative", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "from .x.y.z import deep\n",
    "lib/x/__init__.py": "", "lib/x/y/__init__.py": "",
    "lib/x/y/z.py": "from ... import util\ndef deep():\n    return util.u()\n",
    "lib/util.py": "from hv import compute as _c\ndef u():\n    return _c()\n",
}, {"main.py": "import lib\nprint('r', lib.deep())\n"})

# local module with same name as an unrelated site package that the library imports
scenario("N43_local_shadow", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "import hv\ndef f():\n    return hv.compute()\n",
}, {"main.py": "import lib\nprint('s', lib.f())\n", "hv.py": "def compute():\n    return 'local'\n"})

# use only inside if __name__ == '__main__'
scenario("N44_main_guard", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"main.py": "import hv\ndef main():\n    print('m', hv.compute())\nif __name__ == '__main__':\n    main()\n"})

# getattr default on module for name that exists only conditionally
scenario("N45_getattr_default_conditional", {
    "hv/__init__.py": "import sys\nif sys.maxsize < 0:\n    FEATURE = 'yes'\n",
}, {"main.py": "import hv\nprint('g', getattr(hv, 'FEATURE', 'no'))\n"})

# try: from hv import X except ImportError -> X conditionally bound in hv
scenario("N46_from_import_conditional_name", {
    "hv/__init__.py": "import sys\nif sys.maxsize < 0:\n    X = 1\n",
}, {"main.py": "try:\n    from hv import X\n    r = 'has'\nexcept ImportError:\n    r = 'none'\nprint('f', r)\n"})

print("\n".join(f"RESULT2 {n} {rc} {[(b[0], b[1], b[2][-200:] if len(b) > 2 else '') for b in bb]}" for n, rc, bb in RESULTS))
