from h import scenario, RESULTS
scenario("H01_star_dynamic_all", {"hv/__init__.py": "from . import core\nfrom .core import *\n__all__ = list(core.__all__) + ['X']\nX = 1\n", "hv/core.py": "__all__ = ['Frame']\nclass Frame:\n    pass\n"},
         {"main.py": "from hv import *\nprint('ok', isinstance(1, Frame), X)\n"})
scenario("H02_deep_chain_sub_class_const", {"hv/__init__.py": "from . import a\n", "hv/a.py": "class B:\n    C = 5\n"},
         {"main.py": "import hv\nV = hv.a.B.C\nprint('ok', V * 2)\n"})
scenario("H03_inherited_class_const_compare", {"hv/__init__.py": "class Base:\n    X = 1\nclass Sub(Base):\n    pass\n"},
         {"main.py": "import hv\nprint('ok', hv.Sub.X == 1)\n"})
scenario("H04_mutable_literal_registry", {"hv/__init__.py": "PLUGINS = []\nPLUGINS.append('a')\ndef reg(n):\n    PLUGINS.append(n)\nreg('b')\n"},
         {"main.py": "import hv\nprint('ok', hv.PLUGINS)\n"})
scenario("H05_lambda_default_and_comp", {"hv/__init__.py": "def compute(): return 42\n"},
         {"main.py": "import hv\ndef f(cb=lambda: hv.compute()):\n    return cb()\nprint('ok', f(), [hv.compute() for _ in range(1)])\n"})
scenario("H06_type_checking_runtime_use", {"hv/__init__.py": "class Frame:\n    pass\n"},
         {"main.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import hv\nimport typing\ndef f(x: 'hv.Frame'):\n    return 1\nprint('ok', f(1))\n"})
scenario("H07_get_type_hints_string_ann", {"hv/__init__.py": "class Frame:\n    pass\n", "lib/__init__.py": "import hv\n"},
         {"main.py": "from __future__ import annotations\nimport typing, lib, hv\ndef f(x: hv.Frame) -> int:\n    return 1\nprint('ok', typing.get_type_hints(f)['x'].__name__)\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
