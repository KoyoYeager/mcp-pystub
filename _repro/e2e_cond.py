"""e2e venv の analyze で conditional を表示: python e2e_cond.py 12_asammdf"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze

ROOT = Path(r"C:\tmp\pystub-e2e")
OUT = Path(__file__).resolve().parent / "runs" / "e2e_analyze"
OUT.mkdir(parents=True, exist_ok=True)
small = "--small" in sys.argv
for name in [a for a in sys.argv[1:] if not a.startswith("--")]:
    entry = ROOT / name / "proj" / "entry.py"
    sp = ROOT / name / "venv" / "Lib" / "site-packages"
    t = time.time()
    r = analyze(str(entry), python_path=str(sp), include_small_conditional=small)
    (OUT / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"== {name}: {time.time()-t:.1f}s stubbable={[(p['package_name'], round(p['estimated_size_mb'],1)) for p in r['stubbable']]} "
          f"hints={[(h['submodule'], h['target_package']) for h in r['submodule_stub_hints']]} omitted_small={r['conditional_omitted_small']}")
    for c in r["conditional"]:
        print(f"  conditional {c['package_name']} {c['estimated_savings_mb']}MB total_conditions={c['conditions_total']} because={c['unproven_because']}")
        for g in c["conditions"][:4]:
            print(f"     - {g['entry']}  [{g['why_unproven'][:20]}] uses={g['uses'][:2]} path={' -> '.join(g['path'][-4:])}")
    for h in r["conditional_submodule_hints"]:
        print(f"  cond-hint {h['submodule']} => {h['target_package']} {h['estimated_savings_mb']}MB total={h['conditions_total']}")
        for g in h["conditions"][:4]:
            print(f"     - {g['entry']}  [{g['why_unproven'][:20]}] uses={g['uses'][:2]} path={' -> '.join(g['path'][-4:])}")
