from h import scenario, RESULTS
HV = {"hv/__init__.py": "def compute(): return 42\n"}
scenario("B40d_modlevel_instance_method_arg", HV, {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\nc = C()\ndef call(f):\n    return f()\nprint('ok', call(c.go))\n"})
scenario("B40e_modlevel_instance_method_list", HV, {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\nc = C()\nL = [c.go]\nprint('ok', L[0]())\n"})
scenario("B40f_func_instance_method_arg", HV, {"main.py": "import hv\nclass C:\n    def go(self):\n        return hv.compute()\ndef call(f):\n    return f()\ndef main():\n    c = C()\n    return call(c.go)\nprint('ok', main())\n"})
scenario("B40g_threading", HV, {"main.py": "import hv, threading\nclass W:\n    def work(self):\n        print('ok', hv.compute())\nw = W()\nt = threading.Thread(target=w.work)\nt.start(); t.join()\n"})
scenario("B40h_self_attr_callback", HV, {"main.py": "import hv\nclass C:\n    def __init__(self):\n        self.cb = self.go\n    def go(self):\n        return hv.compute()\n    def run(self):\n        return self.cb()\nprint('ok', C().run())\n"})
scenario("B43_instance_from_lib_factory", {"hv/__init__.py": "def compute(): return 42\n", "fw/__init__.py": """
    _classes = {}
    def make(kind):
        return _classes[kind]()
    def add(kind, cls):
        _classes[kind] = cls
 """}, {"main.py": "import hv, fw\nclass Impl:\n    def go(self):\n        return hv.compute()\nfw.add('i', Impl)\nprint('ok', fw.make('i').go())\n"})
scenario("B44_inherited_method_from_local_base", HV, {"main.py": "import hv\nclass A:\n    def go(self):\n        return hv.compute()\nclass B(A):\n    pass\nprint('ok', B().go())\n"})
scenario("B45_staticmethod_ref_in_class_dict", HV, {"main.py": "import hv\nclass C:\n    @staticmethod\n    def a():\n        return hv.compute()\n    OPS = {'a': a}\n    def run(self, k):\n        return self.OPS[k]()\nprint('ok', C().run('a'))\n"})
scenario("B46_class_level_list_of_methods", HV, {"main.py": "import hv\nclass C:\n    def a(self):\n        return hv.compute()\n    steps = [a]\n    def run(self):\n        return [s(self) for s in self.steps]\nprint('ok', C().run())\n"})
scenario("B47_nested_function_returned", HV, {"main.py": "import hv\ndef outer():\n    def inner():\n        return hv.compute()\n    return inner\nprint('ok', outer()())\n"})
scenario("B48_closure_via_partial", HV, {"main.py": "import hv, functools\ndef f(x):\n    return hv.compute() + x\np = functools.partial(f, 1)\nprint('ok', p())\n"})
scenario("B49_dunder_call_on_instance", HV, {"main.py": "import hv\nclass C:\n    def __call__(self):\n        return hv.compute()\nprint('ok', C()())\n"})
scenario("B50_conditional_def", HV, {"main.py": "import hv, sys\nif sys.platform == 'win32':\n    def f():\n        return hv.compute()\nelse:\n    def f():\n        return 0\nprint('ok', f())\n"})
scenario("B51_redefined_function", HV, {"main.py": "import hv\ndef f():\n    return 0\n_old = f\ndef f():\n    return hv.compute()\nprint('ok', f())\n"})
scenario("B52_try_import_local_fallback", HV, {"main.py": "try:\n    from hv import compute\nexcept ImportError:\n    def compute():\n        return 0\nprint('ok', compute())\n"})
scenario("B53_alias_rebinding", HV, {"main.py": "import hv\nimport json as hv2\nx = hv\nx = hv2\ndef g():\n    return hv.compute()\nh = g\nprint('ok', h())\n"})
for n, rc0, b in RESULTS:
    print("RESULT", n, "orig_rc", rc0, "BROKEN" if b else "fine", [x[:2] for x in b])
