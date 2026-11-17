"""tracemalloc で analyze の保持メモリを割り当て箇所ごとに出す: python prof_trace.py <entry> [site-packages]"""
import sys, time, tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
tracemalloc.start(1)
from mcp_pystub.analyzer import analyze  # noqa: E402

entry = sys.argv[1]
sp = sys.argv[2] if len(sys.argv) > 2 else ""
t = time.perf_counter()
from mcp_pystub.analyzer import _detect_project_root  # noqa: E402
from mcp_pystub.import_graph import build_import_graph  # noqa: E402
from mcp_pystub.module_resolver import ModuleResolver  # noqa: E402
from mcp_pystub import usage_analyzer as ua  # noqa: E402

e = Path(entry).resolve()
root = _detect_project_root(e)
res = ModuleResolver(project_root=str(root), python_path=sp)
g = build_import_graph(entry_point=str(e), project_root=str(root), resolver=res, max_depth=10)
s1 = tracemalloc.take_snapshot()
cur, peak = tracemalloc.get_traced_memory()
print(f"after graph: cur={cur/1e6:.0f}MB peak={peak/1e6:.0f}MB {time.perf_counter()-t:.0f}s", flush=True)
for st in s1.statistics("lineno")[:8]:
    print("   ", st)
sem = ua.get_semantics(g)
an = ua.analyze_packages(g)
s2 = tracemalloc.take_snapshot()
cur, peak = tracemalloc.get_traced_memory()
print(f"after analyze: cur={cur/1e6:.0f}MB peak={peak/1e6:.0f}MB {time.perf_counter()-t:.0f}s", flush=True)
for st in s2.statistics("lineno")[:25]:
    print("   ", st)
