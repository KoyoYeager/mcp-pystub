"""第 7 回 第 2 段 受け入れ 3・4: 実シナリオで実行の記録 → analyze(trace_files) → verify(run_entry=True, record=True)。

python trace10.py <out_dir> <scenario> ...
"""
import json, os, subprocess, sys, time
from pathlib import Path
W = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, W)
from mcp_pystub.analyzer import analyze  # noqa: E402
from mcp_pystub.verifier import verify  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)


def timed(cmd, cwd):
    t = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return round(time.perf_counter() - t, 2), r.returncode, r.stdout.strip()[-200:], r.stderr.strip()[-300:]


for n in sys.argv[2:]:
    base = Path(rf"C:\tmp\pystub-e2e\{n}")
    E, SP, PY = base / "proj" / "entry.py", base / "venv" / "Lib" / "site-packages", base / "venv" / "Scripts" / "python.exe"
    if n == "real_acc":
        base = Path(r"C:\tmp\pystub-e2e\check_round6\real_acc")
        E, SP, PY = base / "proj" / "entry.py", base / "venv" / "Lib" / "site-packages", base / "venv" / "Scripts" / "python.exe"
    tf = out / f"{n}_trace.json"
    plain = [timed([str(PY), str(E)], str(E.parent)) for _ in range(3)]
    tcode = (f"import sys; sys.path.append({W!r}); from mcp_pystub.trace import main; "
             f"sys.exit(main(['--out', {str(tf)!r}, {str(E)!r}]))")
    traced = [timed([str(PY), "-c", tcode], str(E.parent)) for _ in range(3)]
    t_plain = min(x[0] for x in plain)
    t_traced = min(x[0] for x in traced)
    a = analyze(str(E), python_path=str(SP), trace_files=[str(tf)])
    pk = [p["package_name"] for p in a["stubbable"]] + [p["package_name"] for p in a["conditional"]]
    subs = [f"{h['submodule']}=>{h['target_package']}" for h in a["submodule_stub_hints"] + a["conditional_submodule_hints"]]
    nof = [p["package_name"] for p in a["nofollow"]]
    if n == "real_acc":
        pk = ["geoacc"]
    t = time.time()
    v = verify(str(E), python_path=str(SP), packages=pk, submodules=subs, nofollow=nof, run_entry=True, record=True)
    dt = round(time.time() - t, 1)
    tc = [{"package_name": c["package_name"], "mb": c["estimated_size_mb"], "state": c["observed"]["state"]}
          for c in a.get("trace_candidates", [])]
    rec = {"scenario": n, "entry_plain_s": t_plain, "entry_traced_s": t_traced,
           "overhead_x": round(t_traced / t_plain, 2) if t_plain else None,
           "plain_rc_out": plain[0][1:3], "traced_rc_out": traced[0][1:3], "traced_err": traced[0][3][-200:],
           "verify_record_s": dt, "baseline_failed": v.get("baseline_failed"),
           "items": pk + subs + nof,
           "statuses": {k: {"status": s["status"], "observed": (s.get("observed") or {}).get("state"),
                            "functions": (s.get("observed") or {}).get("functions_executed"),
                            "top": (s.get("observed") or {}).get("top_functions", [])[:3],
                            "targets": (s.get("observed") or {}).get("targets")} for k, s in v["statuses"].items()},
           "status_summary": v["status_summary"],
           "rejected": [(x["item"], (x.get("reason") or "")[:60]) for x in v["rejected"]],
           "analyze_observed": {(p.get("package_name") or p.get("submodule")): (p.get("observed") or {}).get("state")
                                for p in a["stubbable"] + a["conditional"] + a["submodule_stub_hints"]
                                + a["conditional_submodule_hints"] + a["nofollow"]},
           "trace_conflicts": [(c["item"], c["kind"], c["functions_executed"], c["top_functions"][:3])
                               for c in a.get("trace_conflicts", [])],
           "trace_candidates": tc}
    (out / f"{n}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)
