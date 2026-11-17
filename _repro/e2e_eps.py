import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer
ROOT = Path(r"C:\tmp\pystub-e2e")
name = sys.argv[1]
ep = ROOT / name / "proj" / "entry.py"; sp = ROOT / name / "venv" / "Lib" / "site-packages"
root = _detect_project_root(ep)
g = build_import_graph(str(ep), str(root), ModuleResolver(str(root), str(sp)), max_depth=10)
sem = usage_analyzer.get_semantics(g)
print(sorted(sem.reach.entry_point_calls)[:20])
for key in sem.reach.reached:
    u = sem.program.units[key]
    for ev in u.events:
        if ev.kind == "entry_points":
            print("call:", key, "group=", repr(ev.name), "L", ev.lineno)
