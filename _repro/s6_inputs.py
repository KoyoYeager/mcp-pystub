import asyncio, json, os, sys, traceback
from pathlib import Path
from harness import setup, run_py, analyze, inspect_graph, check_usage, generate_stubs, generate_submodule_stubs, REPO, BASE
from mcp_pystub import server

HY = {"heavyy.py": "def compute():\n    return 42\n"}


async def call(tool, args):
    try:
        res = await server.mcp.call_tool(tool, args)
        txt = str(res)[:300]
        return "OK " + txt
    except Exception as e:
        return f"EXC {type(e).__name__}: {str(e)[:200]}"

root, sp, proj = setup("S50_inputs", HY, {"main.py": "import heavyy\n", "empty.py": "", "notes.txt": "hello"})
cases = [
    ("analyze", {"entry_point": str(root / "nope.py")}),
    ("analyze", {"entry_point": str(proj)}),
    ("analyze", {"entry_point": ""}),
    ("analyze", {"entry_point": str(proj / "notes.txt"), "python_path": str(sp)}),
    ("analyze", {"entry_point": str(proj / "empty.py"), "python_path": str(sp)}),
    ("analyze", {"entry_point": str(proj / "main.py"), "python_path": str(sp), "max_depth": -1}),
    ("graph", {"entry_point": str(proj / "main.py"), "python_path": str(sp), "max_depth": 0}),
    ("check", {"entry_point": str(proj / "main.py"), "package_name": "", "python_path": str(sp)}),
    ("check", {"entry_point": str(proj / "main.py"), "package_name": "os", "python_path": str(sp)}),
    ("generate", {"entry_point": str(proj / "main.py"), "package_name": "", "python_path": str(sp)}),
    ("generate", {"entry_point": str(proj / "main.py"), "package_name": "../../etc", "python_path": str(sp)}),
    ("generate", {"entry_point": str(proj / "main.py"), "package_name": "notinstalled", "python_path": str(sp)}),
    ("generate_submodule", {"entry_point": str(proj / "main.py"), "parent_package": "heavyy", "submodule": "heavyy.x", "python_path": str(sp)}),
    ("generate_submodule", {"entry_point": str(proj / "main.py"), "parent_package": "", "submodule": "", "python_path": str(sp)}),
    ("generate_submodule", {"entry_point": str(proj / "main.py"), "parent_package": "x", "submodule": "x..", "python_path": str(sp)}),
]
for tool, args in cases:
    print(tool, {k: (v[-30:] if isinstance(v, str) else v) for k, v in args.items()}, "->", asyncio.run(call(tool, args)))

# generate with empty package name: what does it return directly?
g = generate_stubs(str(proj / "main.py"), "", python_path=str(sp))
print("generate(''):", {k: (v if k != "files" else list(v)[:5]) for k, v in g.items() if k in ("error", "package", "files", "stub_file_count", "original_file_count")})
g = generate_stubs(str(proj / "main.py"), "..", python_path=str(sp))
print("generate('..'):", {k: (v if k != "files" else list(v)[:5]) for k, v in g.items() if k in ("error", "package", "files", "stub_file_count", "original_file_count")})
