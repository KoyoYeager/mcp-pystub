import json
from harness import setup, scenario, run_py, analyze, inspect_graph, generate_stubs, check_usage
from mcp_pystub.import_extractor import extract_imports

HY = {"heavyy.py": "def compute():\n    return 42\n"}

# S60 cp932 file with coding cookie + Japanese identifier
root, sp, proj = setup("S60_cp932", HY, {
    "main.py": "import util\nutil.run()\n",
    "util.py": "# -*- coding: cp932 -*-\nimport heavyy\ndef 計算():\n    return heavyy.compute()\ndef run():\n    print('ok', 計算())\n".encode("cp932"),
})
print("S60 original:", run_py([str(proj), str(sp)], str(proj / "main.py")))
print("S60 extract:", extract_imports(str(proj / "util.py")))
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S60 verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])

# S61 path with spaces and Japanese; build instruction quoting
root, sp, proj = setup("S61 空白 日本語", HY | {"libA/__init__.py": "import heavyy\ndef unused():\n    return heavyy.compute()\n"},
                       {"main.py": "import libA\nprint('ok')\n"})
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S61 verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])
g = generate_stubs(str(proj / "main.py"), "heavyy", python_path=str(sp))
print("S61 backup:", g["build_instructions"]["backup_commands"][1])
import site
print("S61 site.getsitepackages()[0] =", site.getsitepackages()[0])

# S62 max_depth truncation without warning
files = {"main.py": "import m1\n"}
for i in range(1, 13):
    files[f"m{i}.py"] = f"import m{i+1}\n"
files["m13.py"] = "import heavyy\nV = heavyy.compute()\n"
root, sp, proj = setup("S62_depth", HY, files)
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S62 total:", r["total_packages_analyzed"], "warnings:", r["warnings"])

# S63 circular local imports
root, sp, proj = setup("S63_cycle", HY, {"main.py": "import a\na.f()\n", "a.py": "import b\nimport heavyy\ndef f():\n    b.g()\n", "b.py": "import a\nimport heavyy\ndef g():\n    print('ok', heavyy.compute())\n"})
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S63 verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])

# S64 .pyw entry point module name
root, sp, proj = setup("S64_pyw", HY, {"app.pyw": "import heavyy\nheavyy.compute()\n"})
g = inspect_graph(str(proj / "app.pyw"), python_path=str(sp))
print("S64 nodes:", [(n["module"], n["classification"]) for n in g["nodes"]])

# S65 except Exception / builtins.ImportError / ModuleNotFoundError via attribute
for name, handler in [("Exception", "except Exception:"), ("attr", "except builtins.ImportError:"), ("OSError tuple", "except (OSError, ImportError):")]:
    root, sp, proj = setup("S65_" + name.replace(" ", "_"), HY, {"main.py": f"import builtins\ntry:\n    import heavyy\n{handler}\n    heavyy = None\nprint('ok')\n"})
    r = analyze(str(proj / "main.py"), python_path=str(sp))
    print("S65", name, [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])

# S66 star import from package: stub content
root, sp, proj = setup("S66_star", {"pz/__init__.py": "def helper():\n    return 1\nCONST = 3\n",
                                     "libS/__init__.py": "from pz import *\nimport pz\nX = CONST + helper()\ndef unused():\n    return pz.helper()\n"},
                       {"main.py": "import libS\nprint('ok', libS.X)\n"})
print("S66 original:", run_py([str(proj), str(sp)], str(proj / "main.py")))
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S66 verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])
g = generate_stubs(str(proj / "main.py"), "pz", python_path=str(sp))
print("S66 stub:", json.dumps(g["files"]))
