"""analyze の条件付き候補を見る: python cond_show.py <scenario>"""
import json, sys, time, psutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze
n = sys.argv[1]
t = time.perf_counter()
r = analyze(rf"C:\tmp\pystub-e2e\{n}\proj\entry.py", python_path=rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages")
print(n, f"{time.perf_counter()-t:.1f}s peak={psutil.Process().memory_info().peak_wset/1048576:.0f}MB")
print(" top:", r["summary"]["recommendations"][:3])
for h in r["conditional_submodule_hints"] + r["conditional"]:
    print(" ", h.get("submodule", h.get("package_name")), "=>", h.get("target_package", ""), h["confidence"], "total", h["conditions_total"], "complete", h.get("conditions_complete"), "|", h.get("confidence_reason", "")[:150])
    for c in h["conditions"][:6]:
        print("     -", c["entry"], "| uses", c["project_uses_count"], c["project_uses"][:2], "| unresolved", c.get("project_unresolved_calls", [])[:2])
