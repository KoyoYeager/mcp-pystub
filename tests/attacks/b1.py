"""Proven-tier attacks (new angles)."""
from h import scenario, RESULTS

# B01 runtime_checkable Protocol isinstance: structural True without any hv instance
scenario("N01_protocol_isinstance", {
    "hv/__init__.py": "from typing import Protocol, runtime_checkable\n@runtime_checkable\nclass SupportsRead(Protocol):\n    def read(self): ...\n",
}, {"main.py": "import hv\nclass F:\n    def read(self): return 1\nprint('isinst', isinstance(F(), hv.SupportsRead))\n"})

# B02 ABC with __subclasshook__
scenario("N02_abc_subclasshook", {
    "hv/__init__.py": "import abc\nclass Sized(abc.ABC):\n    @classmethod\n    def __subclasshook__(cls, C):\n        return hasattr(C, '__len__') or NotImplemented\n",
}, {"main.py": "import hv\nprint('isinst', isinstance([1], hv.Sized), issubclass(dict, hv.Sized))\n"})

# B03 ABC.register of a builtin at hv import time
scenario("N03_abc_register_at_import", {
    "hv/__init__.py": "import abc\nclass Readable(abc.ABC):\n    pass\nReadable.register(str)\n",
}, {"main.py": "import hv\nprint('isinst', isinstance('x', hv.Readable))\n"})

# B04 metaclass __instancecheck__
scenario("N04_metaclass_instancecheck", {
    "hv/__init__.py": "class _Meta(type):\n    def __instancecheck__(cls, obj):\n        return isinstance(obj, int)\nclass Number(metaclass=_Meta):\n    pass\n",
}, {"main.py": "import hv\nprint('isinst', isinstance(3, hv.Number))\n"})

# B05 isinstance with a tuple constant of builtin types
scenario("N05_isinstance_tuple_const", {
    "hv/__init__.py": "NUMERIC = (int, float)\n",
}, {"main.py": "import hv\nprint('isinst', isinstance(3, hv.NUMERIC))\n"})

# B06 except with a tuple of exceptions defined in hv
scenario("N06_except_tuple", {
    "hv/__init__.py": "ERRORS = (ValueError, KeyError)\n",
}, {"main.py": "import hv\ntry:\n    int('x')\nexcept hv.ERRORS:\n    print('caught')\n"})

# B07 except clause evaluated when another exception passes through (opaque exception class)
scenario("N07_except_opaque_then_other", {
    "hv/__init__.py": "Error = type('Error', (Exception,), {})\n",
}, {"main.py": "import hv\ntry:\n    int('x')\nexcept hv.Error:\n    print('hv')\nexcept ValueError:\n    print('value')\n"})

# B08 module docstring read
scenario("N08_module_doc", {
    "hv/__init__.py": "'''Heavy package docs.'''\nX = 1\n",
}, {"main.py": "import hv\nd = hv.__doc__\nprint('doc', d)\n"})

# B09 class docstring / function docstring
scenario("N09_class_doc", {
    "hv/__init__.py": "class Thing:\n    '''Thing docs.'''\ndef f():\n    '''f docs'''\n",
}, {"main.py": "import hv\nprint('doc', hv.Thing.__doc__, hv.f.__doc__)\n"})

# B10 pkgutil plugin discovery (plugins not in static graph) using hv
scenario("N10_pkgutil_plugins", {
    "hv/__init__.py": "def compute():\n    return 42\n",
    "lib/__init__.py": "import hv\nimport importlib, pkgutil\nfrom lib import plugins\ndef run():\n    out = []\n    for m in pkgutil.iter_modules(plugins.__path__):\n        mod = importlib.import_module('lib.plugins.' + m.name)\n        out.append(mod.go())\n    return out\n",
    "lib/plugins/__init__.py": "",
    "lib/plugins/p1.py": "import hv\ndef go():\n    return hv.compute()\n",
}, {"main.py": "import lib\nprint('plugins', lib.run())\n"})

# B10b same but plugin package separate top-level and loaded via import_module(non-const)
scenario("N10b_plugins_other_pkg", {
    "hv/__init__.py": "def compute():\n    return 42\n",
    "lib/__init__.py": "import hv\nimport importlib, pkgutil\nimport plug\ndef run():\n    out = []\n    for m in pkgutil.iter_modules(plug.__path__):\n        mod = importlib.import_module(plug.__name__ + '.' + m.name)\n        out.append(mod.go())\n    return out\n",
    "plug/__init__.py": "",
    "plug/p1.py": "import hv\ndef go():\n    return hv.compute()\n",
}, {"main.py": "import lib\nprint('plugins', lib.run())\n"})

# B11 annotation-driven dispatch (pydantic-like): library evaluates field annotations and calls .parse
scenario("N11_annotation_driven", {
    "hv/__init__.py": "class Int:\n    @classmethod\n    def parse(cls, s):\n        return int(s) * 10\n",
    "lib/__init__.py": "import typing\nclass Model:\n    def __init__(self, **kw):\n        hints = typing.get_type_hints(type(self))\n        for k, t in hints.items():\n            setattr(self, k, t.parse(kw[k]))\n",
}, {"main.py": "import lib, hv\nclass M(lib.Model):\n    x: hv.Int\nprint('m', M(x='3').x)\n"})

# B11b dataclasses fields type used
scenario("N11b_dataclass_field_type", {
    "hv/__init__.py": "class Int:\n    @classmethod\n    def parse(cls, s):\n        return int(s) * 10\n",
    "lib/__init__.py": "import dataclasses\ndef load(cls, raw):\n    return cls(**{f.name: f.type.parse(raw[f.name]) for f in dataclasses.fields(cls)})\n",
}, {"main.py": "import lib, hv, dataclasses\n@dataclasses.dataclass\nclass M:\n    x: hv.Int\nprint('m', lib.load(M, {'x': '3'}).x)\n"})

# B12 function default argument with non-literal hv value, returned later
scenario("N12_def_default_value", {
    "hv/__init__.py": "import os\nDEFAULT = os.path.join('a', 'b')\n",
}, {"main.py": "import hv\ndef f(x=hv.DEFAULT):\n    return x\nprint('d', f())\n"})

# B12b keyword-only default
scenario("N12b_kwonly_default", {
    "hv/__init__.py": "import os\nDEFAULT = os.path.join('a', 'b')\n",
}, {"main.py": "import hv\ndef f(*, x=hv.DEFAULT):\n    return x\nprint('d', f())\n"})

# B13 walrus binding then use
scenario("N13_walrus", {
    "hv/__init__.py": "import os\nVALUE = os.path.join('a', 'b')\n",
}, {"main.py": "import hv\nif (y := hv.VALUE):\n    pass\nprint('w', y)\n"})

# B14 f-string
scenario("N14_fstring", {
    "hv/__init__.py": "import os\nNAME = os.path.join('a', 'b')\n",
}, {"main.py": "import hv\nprint(f'n={hv.NAME!r}')\n"})

# B15 __reduce__ referencing hv function, pickled
scenario("N15_reduce_pickle", {
    "hv/__init__.py": "def rebuild(v):\n    return v\n",
}, {"main.py": "import hv, pickle\nclass C:\n    def __init__(self, v): self.v = v\n    def __reduce__(self):\n        return (hv.rebuild, (self.v,))\nprint('p', pickle.loads(pickle.dumps(C(5))))\n"})

# B16 sys.excepthook assignment (stored outside)
scenario("N16_excepthook", {
    "hv/__init__.py": "import sys\ndef hook(t, v, tb):\n    sys.stdout.write('hooked %s\\n' % t.__name__)\n",
}, {"main.py": "import hv, sys\nsys.excepthook = hv.hook\nraise KeyError('x')\n"})

# B17 match class pattern
scenario("N17_match_class_pattern", {
    "hv/__init__.py": "class Point:\n    __match_args__ = ('x',)\n    def __init__(self, x): self.x = x\n",
}, {"main.py": "import hv\ndef m(v):\n    match v:\n        case hv.Point(x=1):\n            return 'pt'\n        case int():\n            return 'int'\n    return 'other'\nprint('m', m(3))\n"})

# B18 __class_getitem__ on hv class used in annotation at module level (evaluated)
scenario("N18_class_getitem_annotation", {
    "hv/__init__.py": "class Box:\n    def __class_getitem__(cls, item):\n        print('getitem', item)\n        return cls\n",
}, {"main.py": "import hv\nx: hv.Box[int] = 1\nprint('x', x)\n"})

# B18b plain annotation with side-effect metaclass __getitem__ via Optional
scenario("N18b_annotation_list_of", {
    "hv/__init__.py": "class Thing:\n    pass\n",
}, {"main.py": "import hv\nx: list[hv.Thing] = []\nprint('ann', __annotations__)\n"})

# B19 functools.cache decorated function using hv
scenario("N19_functools_cache", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"main.py": "import hv, functools\n@functools.cache\ndef f():\n    return hv.compute()\nprint('c', f())\n"})

# B20 weakref.finalize callback
scenario("N20_weakref_finalize", {
    "hv/__init__.py": "def cleanup(tag):\n    print('cleanup', tag)\n",
}, {"main.py": "import hv, weakref\nclass O: pass\no = O()\nweakref.finalize(o, hv.cleanup, 'o')\ndel o\nprint('end')\n"})

# B21 __del__ calling hv
scenario("N21_del", {
    "hv/__init__.py": "def cleanup():\n    print('cleanup')\n",
}, {"main.py": "import hv\nclass O:\n    def __del__(self):\n        hv.cleanup()\no = O()\nprint('end')\n"})

# B22 entry inside package with relative import (python -m pkg style analyzed via __main__.py path)
scenario("N22_relative_entry", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"pkg/__init__.py": "", "pkg/__main__.py": "from .util import go\ngo()\n", "pkg/util.py": "import hv\ndef go():\n    print('go', hv.compute())\n"},
    entry="pkg/__main__.py")

# B23 module __getattr__ re-export chain
scenario("N23_module_getattr_chain", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "def __getattr__(name):\n    import hv\n    return getattr(hv, name)\n",
}, {"main.py": "import lib\nprint('g', lib.compute())\n"})

# B24 __path__ manipulation: lib extends its __path__ to include hv's dir
scenario("N24_path_manipulation", {
    "hv/__init__.py": "",
    "hv/impl.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "import os, hv\n__path__.append(os.path.dirname(hv.__file__))\n",
}, {"main.py": "import lib\nfrom lib import impl\nprint('i', impl.compute())\n"})

# B25 same name imported twice under different alias; one alias used by call
scenario("N25_double_alias", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "hv/sub.py": "def g():\n    return 8\n",
}, {"main.py": "import hv as a\nimport hv.sub as b\nimport importlib\nc = importlib.import_module('hv')\nprint('v', c.compute())\n"})

# B26 multiprocessing spawn re-import of main (uses hv only in the child path)
scenario("N26_multiprocessing", {
    "hv/__init__.py": "def compute():\n    return 7\n",
}, {"main.py": "import multiprocessing as mp\nimport hv\ndef work(q):\n    q.put(hv.compute())\nif __name__ == '__main__':\n    ctx = mp.get_context('spawn')\n    q = ctx.Queue()\n    p = ctx.Process(target=work, args=(q,))\n    p.start(); print('mp', q.get()); p.join()\n"})

# B27 async with / async for over hv objects
scenario("N27_async_with", {
    "hv/__init__.py": "class Ctx:\n    async def __aenter__(self):\n        return 'in'\n    async def __aexit__(self, *a):\n        return False\nCTX = Ctx()\n",
}, {"main.py": "import hv, asyncio\nasync def main():\n    async with hv.CTX as v:\n        print('a', v)\nasyncio.run(main())\n"})

# B28 yield from hv generator obtained via attribute
scenario("N28_yield_from", {
    "hv/__init__.py": "def gen():\n    yield 1\n    yield 2\n",
}, {"main.py": "import hv\ndef g():\n    yield from hv.gen()\nprint('y', list(g()))\n"})

# B29 copyreg registration
scenario("N29_copyreg", {
    "hv/__init__.py": "def reduce_c(c):\n    return (int, (9,))\n",
}, {"main.py": "import hv, copyreg, pickle\nclass C: pass\ncopyreg.pickle(C, hv.reduce_c)\nprint('p', pickle.loads(pickle.dumps(C())))\n"})

# B30 circular relative imports in lib with use in __init__ after cycle
scenario("N30_relative_cycle", {
    "hv/__init__.py": "def compute():\n    return 7\n",
    "lib/__init__.py": "from . import a\n",
    "lib/a.py": "from . import b\ndef fa():\n    return b.fb()\n",
    "lib/b.py": "from .. import lib as _l\nimport hv\ndef fb():\n    return hv.compute()\n",
}, {"main.py": "import lib\nfrom lib.a import fa\nprint('r', fa())\n"})

# B31 lambda default + default via class attribute reference
scenario("N31_class_body_default", {
    "hv/__init__.py": "import os\nSEP = os.sep\n",
}, {"main.py": "import hv\nclass C:\n    def m(self, s=hv.SEP):\n        return s\nprint('c', repr(C().m()))\n"})

# B32 hv value used as dict key at module level then looked up
scenario("N32_dict_key", {
    "hv/__init__.py": "class Token:\n    pass\n",
}, {"main.py": "import hv\nREG = {hv.Token: 'tok'}\nprint('k', len(REG))\n"})

# B33 str() of an hv class, via print of type name only
scenario("N33_class_name", {
    "hv/__init__.py": "class Thing:\n    pass\n",
}, {"main.py": "import hv\nT = hv.Thing\nprint('n', T.__name__, T.__module__, T.__qualname__)\n"})

# B34 dir() on module / hasattr on non-public name
scenario("N34_hasattr_private", {
    "hv/__init__.py": "_private = 1\nX = 1\n",
}, {"main.py": "import hv\nprint('h', hasattr(hv, '_private'), hasattr(hv, 'X'), hasattr(hv, 'nope'))\n"})

# B35 hasattr on class attribute defined in a method (self.x) or via property
scenario("N35_hasattr_class_attr", {
    "hv/__init__.py": "class Thing:\n    a = 1\n    def m(self): pass\n",
}, {"main.py": "import hv\nprint('h', hasattr(hv.Thing, 'a'), hasattr(hv.Thing, 'm'), hasattr(hv.Thing, 'zz'), hasattr(hv.Thing, 'mro'))\n"})

# B36 `is` comparison of an hv constant against None-like sentinel created in hv
scenario("N36_is_sentinel", {
    "hv/__init__.py": "MISSING = object()\ndef get():\n    return MISSING\n",
}, {"main.py": "import hv\nx = None\nprint('is', x is hv.MISSING, x is not hv.MISSING)\n"})

# B37 except* group with hv class
scenario("N37_except_star", {
    "hv/__init__.py": "class Err(ValueError):\n    pass\n",
}, {"main.py": "import hv\ntry:\n    raise ExceptionGroup('g', [KeyError('k')])\nexcept* hv.Err:\n    print('hv')\nexcept* KeyError:\n    print('key')\n"})

# B38 class pattern with hv class of exception inheritance chain via another 3rd party base
scenario("N38_except_base_from_other_pkg", {
    "base/__init__.py": "class BaseErr(LookupError):\n    pass\n",
    "hv/__init__.py": "import base\nclass Err(base.BaseErr):\n    pass\n",
}, {"main.py": "import hv\ntry:\n    {}['x']\nexcept hv.Err:\n    print('hv')\nexcept KeyError:\n    print('key')\n"})

# B39 hasattr on module for name only bound conditionally at hv import (platform)
scenario("N39_hasattr_conditional_binding", {
    "hv/__init__.py": "import sys\nif sys.platform == 'nonexistent':\n    special = 1\n",
}, {"main.py": "import hv\nprint('h', hasattr(hv, 'special'))\n"})

# B40 hasattr for name bound by `del` at import (bound then deleted)
scenario("N40_hasattr_deleted", {
    "hv/__init__.py": "tmp = 1\ndel tmp\nX = 2\n",
}, {"main.py": "import hv\nprint('h', hasattr(hv, 'tmp'))\n"})

# B41 hasattr on name injected by globals() write
scenario("N41_hasattr_globals_inject", {
    "hv/__init__.py": "globals()['injected'] = 1\nX = 2\n",
}, {"main.py": "import hv\nprint('h', hasattr(hv, 'injected'))\n"})

# B42 hasattr on submodule attribute that exists only after import of sub elsewhere
scenario("N42_hasattr_submodule_loaded", {
    "hv/__init__.py": "X = 1\n",
    "hv/sub.py": "Y = 2\n",
    "lib/__init__.py": "import hv.sub\n",
}, {"main.py": "import hv\nprint('before', hasattr(hv, 'sub'))\nimport lib\nprint('after', hasattr(hv, 'sub'))\n"})

print("\n".join(f"RESULT2 {n} {rc} {b}" for n, rc, b in RESULTS))
