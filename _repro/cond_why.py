"""条件の列挙が完了しない理由の内訳: python cond_why.py <scenario> <prefix>"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.usage_analyzer import get_semantics, prefix_usage, _region_entries_multi, _UNRESOLVED_EV, project_entry_uses, _api_name
n, pfx = sys.argv[1], sys.argv[2]
e = Path(rf"C:\tmp\pystub-e2e\{n}\proj\entry.py")
res = ModuleResolver(project_root=str(_detect_project_root(e)), python_path=rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages")
g = build_import_graph(str(e), str(_detect_project_root(e)), res)
sem = get_semantics(g)
u = prefix_usage(sem, pfx)
keys = list(dict.fromkeys(x[0].key for x in u.uses if not (x[0].key in sem.certain and x[1].cond)))
found, comp = _region_entries_multi(sem, keys)
kinds = collections.Counter(getattr(ev, "kind", "") for (ev, s, d, o) in found.values())
print("entries", len(found), "kinds", kinds.most_common())
for (k, t), (ev, src, d, o) in list(found.items()):
    if getattr(ev, "kind", "") in _UNRESOLVED_EV:
        print("  unresolved:", k, t, ev.kind, ev.name, "from", src)
        break
loose = collections.Counter()
for (k, t) in found:
    nm = k[1].split(".")[-1].split("#")[0]
    h, l = project_entry_uses(sem, k, nm)
    if h:
        print("  resolved project use:", _api_name(sem, k), h[:2])
    if l:
        loose[nm] += len(l)
print("unresolved project calls by name:", loose.most_common(10))
