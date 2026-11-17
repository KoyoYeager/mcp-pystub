"""提案の出るシナリオで verify(run_entry=True) を 1 本ずつ回す: python verify10.py <out_dir> <scenario> ...

各シナリオで analyze の全提案（stubbable / nofollow / submodule_stub_hints / conditional / conditional_submodule_hints）を渡す。
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze  # noqa: E402
from mcp_pystub.verifier import verify  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for n in sys.argv[2:]:
    E = rf"C:\tmp\pystub-e2e\{n}\proj\entry.py"
    SP = rf"C:\tmp\pystub-e2e\{n}\venv\Lib\site-packages"
    a = analyze(E, python_path=SP)
    pk = [p["package_name"] for p in a["stubbable"]] + [p["package_name"] for p in a["conditional"]]
    subs = [f"{h['submodule']}=>{h['target_package']}" for h in a["submodule_stub_hints"] + a["conditional_submodule_hints"]]
    nof = [p["package_name"] for p in a["nofollow"]]
    t = time.time()
    v = verify(E, python_path=SP, packages=pk, submodules=subs, nofollow=nof, run_entry=True)
    dt = time.time() - t
    v["_input"] = {"packages": pk, "submodules": subs, "nofollow": nof, "verify_s": round(dt, 1)}
    (out / f"{n}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"scenario": n, "items": pk + subs + nof, "baseline_failed": v.get("baseline_failed"),
                      "passed": v["passed"], "rejected": [(x["item"], (x.get("reason") or "")[:40], (x.get("error") or "")[:120])
                                                          for x in v["rejected"]],
                      "unresolved": [x["item"] for x in v.get("unresolved", [])], "entry_matched": v.get("entry_matched"),
                      "verify_s": round(dt, 1)}, ensure_ascii=False), flush=True)
