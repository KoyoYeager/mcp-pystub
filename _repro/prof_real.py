"""実プロジェクト（S40）の analyze を段階ごとに計測し、意味解析をプロファイルする。"""
import cProfile, pstats, sys, time, threading, faulthandler
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer

ep = Path(__file__).resolve().parent / "runs" / "S40_real" / "proj" / "main.py"
faulthandler.dump_traceback_later(int(sys.argv[1]) if len(sys.argv) > 1 else 300, exit=True)
t = time.time()
root = _detect_project_root(ep)
res = ModuleResolver(str(root))
g = build_import_graph(str(ep), str(root), res, max_depth=10)
print(f"graph {time.time()-t:.1f}s nodes={len(g.nodes)} edges={len(g.edges)}", flush=True)
pr = cProfile.Profile()
t = time.time()
box = {}
def work():
    pr.enable()
    box["sem"] = usage_analyzer.get_semantics(g)
    pr.disable()
th = threading.Thread(target=work, daemon=True)
th.start()
th.join(float(sys.argv[2]) if len(sys.argv) > 2 else 120)
if "sem" not in box:
    pr.disable()
    print("semantics not finished", flush=True)
    pstats.Stats(pr).sort_stats("tottime").print_stats(25)
    sys.exit(0)
sem = box["sem"]
print(f"semantics {time.time()-t:.1f}s units={len(sem.program.units)} reached={len(sem.reach.reached)} loaded={len(sem.reach.loaded_modules)}", flush=True)
pstats.Stats(pr).sort_stats("tottime").print_stats(25)
t = time.time()
r = usage_analyzer.analyze_packages(g)
print(f"analyze_packages {time.time()-t:.1f}s", flush=True)
