import time, traceback
from h import scenario, RESULTS
def tryit(name, sp, proj):
    t = time.time()
    try:
        scenario(name, sp, proj)
        print(f"   time {time.time()-t:.1f}s")
    except Exception as e:
        print(f"   EXCEPTION in {name}: {type(e).__name__}: {str(e)[:200]}  ({time.time()-t:.1f}s)")
HV = {"hv/__init__.py": "def compute(): return 42\n"}
for n in (300, 1200):
    code = "import hv\nclass C0:\n    def go(self):\n        return 1\n" + "".join(f"class C{i}(C{i-1}):\n    pass\n" for i in range(1, n)) + f"print('ok', C{n-1}().go())\n"
    tryit(f"P01_deep_inherit_{n}", HV, {"main.py": code})
code = "import hv\nx = " + "+".join(["1"] * 3000) + "\nprint('ok', x)\n"
tryit("P02_long_binop_3000", HV, {"main.py": code})
tryit("P03_alias_cycle", {"hv/__init__.py": "a = b\nb = a\ndef compute(): return 1\n"}, {"main.py": "import hv\ntry:\n    print(hv.a)\nexcept NameError:\n    print('ok')\n"})
tryit("P04_star_cycle", {"hv/__init__.py": "from .m1 import *\n", "hv/m1.py": "from .m2 import *\nA = 1\n", "hv/m2.py": "from .m1 import *\nB = 2\n"}, {"main.py": "import hv\nprint('ok', hv.A)\n"})
code = "import hv\n" + "".join(f"def f{i}():\n    return f{i+1}()\n" for i in range(3000)) + "def f3000():\n    return 1\nprint('ok', f0.__name__)\n"
tryit("P05_call_chain_3000", HV, {"main.py": code})
code = "import hv\n" + "".join(f"def f{i}(x):\n    return x + {i}\n" for i in range(20000)) + "print('ok')\n"
tryit("P06_huge_20000_funcs", HV, {"main.py": code})
code = "import hv\ndef rec(n):\n    return rec(n-1) if n else hv.compute()\nprint('ok', rec(3))\n"
tryit("P07_recursive", HV, {"main.py": code})
code = "import hv\nx = " + "[" * 90 + "1" + "]" * 90 + "\nprint('ok')\n"
tryit("P08_nested_list_90", HV, {"main.py": code})
code = "import hv\nclass A(B):\n    pass\nclass B(A):\n    pass\n"
tryit("P09_inherit_cycle_names", HV, {"main.py": "import hv\nclass A:\n    pass\nclass B(A):\n    pass\nA = B\nclass C(A):\n    def go(self):\n        return 1\nprint('ok', C().go())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
