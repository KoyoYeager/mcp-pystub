"""analyze の段階ごとの時間・RSS と、主な構造の件数を出す: python prof_mem.py <entry> [site-packages]"""
import gc, sys, time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import _detect_project_root  # noqa: E402
from mcp_pystub.import_graph import build_import_graph  # noqa: E402
from mcp_pystub.module_resolver import ModuleResolver  # noqa: E402
from mcp_pystub import usage_analyzer as ua  # noqa: E402

P = psutil.Process()


def mb():
    m = P.memory_info()
    return f"rss={m.rss / 1048576:.0f}MB peak={m.peak_wset / 1048576:.0f}MB"


entry = Path(sys.argv[1]).resolve()
sp = sys.argv[2] if len(sys.argv) > 2 else ""
t = time.perf_counter()
root = _detect_project_root(entry)
res = ModuleResolver(project_root=str(root), python_path=sp)
g = build_import_graph(entry_point=str(entry), project_root=str(root), resolver=res, max_depth=10)
print(f"graph: {time.perf_counter() - t:.1f}s nodes={len(g.nodes)} edges={len(g.edges)} {mb()}", flush=True)
t = time.perf_counter()
sem = ua.get_semantics(g)
print(f"semantics: {time.perf_counter() - t:.1f}s {mb()}", flush=True)
prog, reach = sem.program, sem.reach
nev = sum(len(u.events) for u in prog.units.values())
print(f"  reparsed={getattr(prog, 'reparsed', None)} modules={len(prog.modules)} units={len(prog.units)} events={nev} reached={len(reach.reached)} "
      f"edges={len(reach.edges)} certain={len(sem.certain)} entries={len(sem.entries)}", flush=True)
for k in dir(reach):
    if k.startswith("_"):
        continue
    v = getattr(reach, k)
    if isinstance(v, (list, set, dict)):
        print(f"  reach.{k}: {len(v)}")
for k in dir(prog):
    if k.startswith("_"):
        continue
    v = getattr(prog, k)
    if isinstance(v, (list, set, dict)):
        print(f"  program.{k}: {len(v)}")
t = time.perf_counter()
an = ua.analyze_packages(g)
print(f"analyze_packages: {time.perf_counter() - t:.1f}s {mb()} pkgs={len(an)}", flush=True)
gc.collect()
print("objects:", len(gc.get_objects()))
