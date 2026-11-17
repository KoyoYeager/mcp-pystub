"""意味解析が読み込むモジュールの数と中身を途中で覗く。"""
import collections, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer, semantics

ep = Path(__file__).resolve().parent / "runs" / "S40_real" / "proj" / "main.py"
root = _detect_project_root(ep)
res = ModuleResolver(str(root))
g = build_import_graph(str(ep), str(root), res, max_depth=10)
box = {}
orig = semantics.Program.__init__
def init(self, *a, **k):
    orig(self, *a, **k)
    box["p"] = self
semantics.Program.__init__ = init
usage_analyzer.Program = semantics.Program
th = threading.Thread(target=lambda: box.setdefault("sem", usage_analyzer.get_semantics(g)), daemon=True)
th.start()
for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 6):
    th.join(10)
    p = box.get("p")
    if p is None:
        continue
    mods = [m for m, i in list(p.modules.items()) if i is not None]
    tops = collections.Counter(m.split(".")[0] for m in mods)
    print(f"t={time.time():.0f} modules={len(mods)} none={len(p.modules)-len(mods)} units={len(p.units)} top={tops.most_common(8)}", flush=True)
    if "sem" in box:
        break
mods = [m for m, i in p.modules.items() if i is not None]
print("sample:", sorted(mods, key=len, reverse=True)[:15])
