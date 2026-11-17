"""e2e venv で、あるユニットが到達した経路を辿る: python e2e_why.py 12_asammdf module:qual [...]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer

ROOT = Path(r"C:\tmp\pystub-e2e")
name = sys.argv[1]
ep = ROOT / name / "proj" / "entry.py"
sp = ROOT / name / "venv" / "Lib" / "site-packages"
root = _detect_project_root(ep)
g = build_import_graph(str(ep), str(root), ModuleResolver(str(root), str(sp)), max_depth=10)
sem = usage_analyzer.get_semantics(g)
print("build_roots:", sem.reach.build_roots)
print("unanalyzable:", sem.reach.unanalyzable[:10])


def fmt(ev):
    if isinstance(ev, str):
        return ev
    if ev.ref is not None:
        return f"{ev.kind}/{ev.ctx} {ev.ref.kind} {'.'.join((ev.ref.module,) + ev.ref.chain)} L{ev.lineno}"
    return f"{ev.kind} {ev.name or ','.join(ev.modules)} L{ev.lineno}"


for arg in sys.argv[2:]:
    mod, _, qual = arg.partition(":")
    key = (mod, qual or "<module>")
    print("=====", arg, "reached" if key in sem.reach.reached else "NOT reached")
    seen = set()
    while key in sem.reach.why and key not in seen:
        seen.add(key)
        src, ev = sem.reach.why[key]
        print(f"  {key[0]}:{key[1]}  <=  {fmt(ev)}")
        if src is None:
            break
        key = src
