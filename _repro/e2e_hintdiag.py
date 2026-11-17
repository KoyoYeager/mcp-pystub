"""ヒント候補の使用箇所を列挙: python e2e_hintdiag.py 12_asammdf asammdf.gui"""
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer

ROOT = Path(r"C:\tmp\pystub-e2e")
name, cand = sys.argv[1], sys.argv[2]
ep = ROOT / name / "proj" / "entry.py"
sp = ROOT / name / "venv" / "Lib" / "site-packages"
root = _detect_project_root(ep)
g = build_import_graph(str(ep), str(root), ModuleResolver(str(root), str(sp)), max_depth=10)
sem = usage_analyzer.get_semantics(g)
u = usage_analyzer.prefix_usage(sem, cand)
print("uses", len(u.uses))
c = Counter((x[0].module + ":" + x[0].qualname) for x in u.uses)
for k, v in c.most_common(15):
    print("  ", v, k)
