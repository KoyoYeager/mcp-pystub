"""15 シナリオの analyze 所要時間を、修正前（results.json）・前回（after_c610a09）と並べる。"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze

ROOT = Path(r"C:\tmp\pystub-e2e")
OUT = Path(__file__).resolve().parent / "runs" / "e2e_analyze"
OUT.mkdir(parents=True, exist_ok=True)
old = json.load(open(ROOT / "results.json", encoding="utf-8"))
old_ms = {}
for k, v in old.get("scenarios", {}).items():
    if isinstance(v, dict):
        old_ms[k] = v.get("analysis_time_ms")
names = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and p.name[:2].isdigit() and (p / "venv").exists())
rows = []
for name in names:
    prev = None
    rj = ROOT / "after_c610a09" / name / "result.json"
    if rj.exists():
        prev = json.load(open(rj, encoding="utf-8")).get("analysis_time_ms")
    entry = ROOT / name / "proj" / "entry.py"
    sp = ROOT / name / "venv" / "Lib" / "site-packages"
    t = time.time()
    r = analyze(str(entry), python_path=str(sp))
    dt = time.time() - t
    (OUT / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    st = [(p["package_name"], round(p["estimated_size_mb"], 1)) for p in r["stubbable"]]
    hints = [(h["submodule"], h["target_package"]) for h in r.get("submodule_stub_hints", [])]
    o = old_ms.get(name)
    rows.append((name, o, prev, r["analysis_time_ms"]))
    print(f"{name}: old={o} prev={prev} now={r['analysis_time_ms']} ratio={(r['analysis_time_ms']/o if o else 0):.2f} "
          f"stubbable={st} nofollow={[p['package_name'] for p in r['nofollow']]} hints={hints}", flush=True)
