"""条件付き候補の入口の内訳: python cond_detail.py <scenario> <package-or-submodule>"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.usage_analyzer import get_semantics, prefix_usage, _region_entries, is_certain_use
n, pfx = sys.argv[1], sys.argv[2]
e = Path(rf"C:\tmp\pystub-e2e\{n}\proj\entry.py")
res = ModuleResolver(project_root=str(_detect_project_root(e)), python_path=rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages")
g = build_import_graph(str(e), str(_detect_project_root(e)), res)
sem = get_semantics(g)
u = prefix_usage(sem, pfx)
print("uses", len(u.uses), "certain", sum(1 for x in u.uses if is_certain_use(sem, x[0], x[1])))
ents = collections.Counter()
for unit, ev, sym in u.uses[:50]:
    es, comp = _region_entries(sem, unit.key)
    for ek, t, eev, src, dd in es:
        ents[(ek, t, src[0])] += 1
for k, c in ents.most_common(40):
    print(c, k)
print("distinct entries", len(ents))
