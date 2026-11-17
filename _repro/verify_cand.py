"""記録からの候補を verify(run_entry=True, record=True) で確かめる: python verify_cand.py <out.json> <scenario> <pkg,...>"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.verifier import verify  # noqa: E402
out, n, pk = sys.argv[1], sys.argv[2], sys.argv[3].split(",")
E = rf"C:\tmp\pystub-e2e\{n}\proj\entry.py"
SP = rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages"
t = time.time()
v = verify(E, python_path=SP, packages=pk, submodules=[], nofollow=[], run_entry=True, record=True)
r = {"scenario": n, "packages": pk, "s": round(time.time() - t, 1), "status_summary": v["status_summary"],
     "statuses": {k: (s["status"], (s.get("observed") or {}).get("state")) for k, s in v["statuses"].items()},
     "rejected": [(x["item"], x.get("reason"), (x.get("error") or "")[:200]) for x in v["rejected"]],
     "baseline_failed": v.get("baseline_failed"), "entry_matched": v.get("entry_matched")}
Path(out).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(r, ensure_ascii=False))
