"""15 シナリオの analyze を 1 本ずつ別プロセスで回し、判定の要約・時間・ピークメモリを JSON に出す。

python verdicts.py <out.json> [シナリオ名 ...]   （名前を省くと全部）
"""
import json, subprocess, sys
from pathlib import Path

ROOT = Path(r"C:\tmp\pystub-e2e")
HERE = Path(__file__).resolve().parent
CHILD = r'''
import json, sys, time, psutil
sys.path.insert(0, sys.argv[1])
from mcp_pystub.analyzer import analyze
t = time.perf_counter()
r = analyze(sys.argv[2], python_path=sys.argv[3])
dt = time.perf_counter() - t
m = psutil.Process().memory_info()
v = {a["package_name"]: a["verdict"] for k in ("stubbable", "nofollow", "required") for a in r[k]}
for c in r.get("conditional", []):
    v[c["package_name"]] = "conditional"
out = {"time_s": round(dt, 2), "peak_mb": round(m.peak_wset / 1048576),
       "verdicts": v,
       "stubbable": sorted(a["package_name"] for a in r["stubbable"]),
       "hints": sorted(h["submodule"] + "=>" + h["target_package"] for h in r["submodule_stub_hints"]),
       "conditional": [(c["package_name"], c.get("estimated_savings_mb"), c.get("confidence"), c.get("conditions_total"))
                       for c in r.get("conditional", [])],
       "cond_hints": [(h["submodule"], h["target_package"], h.get("estimated_savings_mb"), h.get("confidence"),
                       h.get("conditions_total")) for h in r.get("conditional_submodule_hints", [])],
       "omitted_small": r.get("conditional_omitted_small"),
       "warnings": len(r.get("warnings", []))}
print("RESULT " + json.dumps(out, ensure_ascii=False))
'''


def main():
    out = Path(sys.argv[1])
    names = sys.argv[2:] or sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name[:2].isdigit() and (p / "venv").exists())
    res = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    for n in names:
        entry = ROOT / n / "proj" / "entry.py"
        sp = ROOT / n / "venv" / "Lib" / "site-packages"
        if n == "main":
            entry = HERE.parent / "_check" / "real" / "s5" / "main.py"
            sp = ""
        import os
        p = subprocess.run([sys.executable, "-c", CHILD, os.environ.get("CODE_ROOT", str(HERE.parent)), str(entry), str(sp)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        line = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
        if not line:
            res[n] = {"error": p.stderr[-1500:]}
            print(n, "ERROR", p.stderr[-500:], flush=True)
        else:
            res[n] = json.loads(line[0][7:])
            r = res[n]
            print(f"{n}: {r['time_s']}s peak={r['peak_mb']}MB stub={r['stubbable']} hints={r['hints']} "
                  f"cond={r['conditional']} ch={r['cond_hints']}", flush=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
