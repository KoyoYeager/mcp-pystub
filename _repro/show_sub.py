import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.stub_generator import generate_submodule_stubs, generate_stubs

T = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
r = generate_submodule_stubs(str(T / "gui_project/main.py"), "heavylib", "heavylib.gui", python_path=str(T / "site_packages_mock"))
print(r.get("error"), r.get("original_size_bytes"), r.get("stub_size_bytes"), r.get("eliminated_packages"))
for k, v in r.get("files", {}).items():
    print("---", k)
    print(v)
for k, v in r.get("build_instructions", {}).items():
    print(k, v)
g = generate_stubs(str(T / "simple_project/main.py"), "pandas", python_path=str(T / "site_packages_mock"))
print(g.get("error"), list(g.get("files", {})))
