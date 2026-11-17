"""条件付き候補の入口のうち public_api（プロジェクトが呼ぶ公開関数）があるもの: python api_check.py <scenario>"""
import sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub import usage_analyzer as ua
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
captured = []
orig = ua.build_conditions
def wrap(sem, uses, prefix):
    r = orig(sem, uses, prefix)
    if r is not None:
        captured.append((prefix, r))
    return r
ua.build_conditions = wrap
n = sys.argv[1]
e = Path(rf"C:\tmp\pystub-e2e\{n}\proj\entry.py")
res = ModuleResolver(project_root=str(_detect_project_root(e)), python_path=rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages")
g = build_import_graph(str(e), str(_detect_project_root(e)), res)
ua.analyze_packages(g)
for prefix, r in captured:
    print(prefix, r["confidence"], r["total"])
