import json
from harness import setup, run_py, apply_stub, analyze, generate_submodule_stubs

SP = {
    "qtx/__init__.py": "X = 1\n",
    "qtx/QtCore.cp313-win_amd64.pyd": b"\0fake",
    "numx/__init__.py": "def f():\n    return 7\n",
    "parentp/__init__.py": "import parentp.gui\nfrom parentp.gui.widgets import Plot\nfrom .core import load\n",
    "parentp/core.py": "import numx\ndef load():\n    return numx.f()\n",
    "parentp/gui/__init__.py": "import qtx\nimport numx\nfrom .widgets import Plot\n",
    "parentp/gui/widgets.py": "class Plot:\n    pass\n",
}
root, sp, proj = setup("S30_submodule", SP, {"main.py": "import parentp\nprint('ok', parentp.load())\n"})
ep = str(proj / "main.py")
print("original:", run_py([str(proj), str(sp)], ep))
r = analyze(ep, python_path=str(sp))
print("verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])
print("hints:", [(h["submodule"], h["target_package"], h["imported_symbols"]) for h in r.get("submodule_stub_hints", [])])
g = generate_submodule_stubs(ep, "parentp", "parentp.gui", python_path=str(sp))
print("eliminated_packages:", g["eliminated_packages"])
print("files:", json.dumps(g["files"], ensure_ascii=False))
print("install:", g["build_instructions"]["install_commands"])
# emulate install: rm -rf gui; copy stub
ssp, bad = apply_stub(root, sp, g["files"], "gui", remove="parentp/gui")
print("syntax errors:", bad)
print("stubbed run:", run_py([str(proj), str(ssp)], ep))

# S31 hint ignores third-party importers other than the parent package
SP2 = dict(SP)
SP2["parentp/__init__.py"] = "from .core import load\n"
SP2["libO/__init__.py"] = "from parentp.gui import Plot\ndef show():\n    return Plot()\n"
SP2["parentp/gui/__init__.py"] = "import qtx\nfrom .widgets import Plot\n"
root, sp, proj = setup("S31_submodule_other_importer", SP2, {
    "main.py": "import parentp\nimport libO\nprint('ok', parentp.load(), libO.show())\n"})
ep = str(proj / "main.py")
print("S31 original:", run_py([str(proj), str(sp)], ep))
r = analyze(ep, python_path=str(sp))
print("S31 hints:", [(h["submodule"], h["target_package"], h["imported_symbols"]) for h in r.get("submodule_stub_hints", [])])
g = generate_submodule_stubs(ep, "parentp", "parentp.gui", python_path=str(sp))
print("S31 files:", json.dumps(g["files"], ensure_ascii=False))
ssp, bad = apply_stub(root, sp, g["files"], "gui", remove="parentp/gui")
print("S31 stubbed run:", run_py([str(proj), str(ssp)], ep))

# S32 C extension as a single top-level .pyd module / package with .pyd only (no .py)
SP3 = {
    "fastmod.cp313-win_amd64.pyd": b"\0fake",
    "libF/__init__.py": "import fastmod\ndef unused():\n    return fastmod.go()\n",
}
root, sp, proj = setup("S32_single_pyd", SP3, {"main.py": "import libF\n"})
r = analyze(str(proj / "main.py"), python_path=str(sp))
print("S32 verdicts:", [(p["package_name"], k) for k in ("stubbable", "nofollow", "required") for p in r[k]])
