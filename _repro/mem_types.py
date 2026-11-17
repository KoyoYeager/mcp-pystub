"""意味解析の後、型ごとのオブジェクト数と大きさ（sys.getsizeof の合計）: python mem_types.py <entry> [site-packages]"""
import gc, sys, collections, psutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.usage_analyzer import get_semantics, analyze_packages
gc.disable()
e = Path(sys.argv[1]).resolve()
sp = sys.argv[2] if len(sys.argv) > 2 else ""
res = ModuleResolver(project_root=str(_detect_project_root(e)), python_path=sp)
g = build_import_graph(str(e), str(_detect_project_root(e)), res)
sem = get_semantics(g)
analyze_packages(g)
gc.collect()
cnt = collections.Counter(); size = collections.Counter()
for o in gc.get_objects():
    t = type(o).__name__
    if type(o).__module__ == "ast" or t in ("Load", "Store"):
        t = "ast." + t
    cnt[t] += 1
    try:
        size[t] += sys.getsizeof(o)
    except TypeError:
        pass
prog = sem.program
nev = sum(len(u.events) for u in prog.units.values())
print(f"rss={psutil.Process().memory_info().rss/1048576:.0f}MB peak={psutil.Process().memory_info().peak_wset/1048576:.0f}MB "
      f"modules={len(prog.modules)} units={len(prog.units)} events={nev} edges={len(sem.reach.edges)} refs={len(prog._refs)}")
for t, s in size.most_common(25):
    print(f"  {t:24s} n={cnt[t]:>9,} {s/1048576:8.1f} MB")
print("  ast.* total", f"{sum(v for k, v in size.items() if k.startswith('ast.'))/1048576:.1f} MB",
      sum(v for k, v in cnt.items() if k.startswith('ast.')))
