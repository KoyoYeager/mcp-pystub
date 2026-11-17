"""C:\\tmp\\pystub-e2e の venv を読み取りだけで解析し、要約を出す: python e2e_analyze.py 12_asammdf [...]"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze

ROOT = Path(r"C:\tmp\pystub-e2e")
OUT = Path(__file__).resolve().parent / "runs" / "e2e_analyze"
OUT.mkdir(parents=True, exist_ok=True)
for name in sys.argv[1:]:
    entry = ROOT / name / "proj" / "entry.py"
    sp = ROOT / name / "venv" / "Lib" / "site-packages"
    t = time.time()
    r = analyze(str(entry), python_path=str(sp))
    dt = time.time() - t
    (OUT / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    st = [(p["package_name"], round(p["estimated_size_mb"], 1)) for p in r["stubbable"]]
    nf = [p["package_name"] for p in r["nofollow"]]
    hints = [(h["submodule"], h["target_package"]) for h in r.get("submodule_stub_hints", [])]
    print(f"{name}: {dt:.1f}s total={r['total_packages_analyzed']} stubbable={st} nofollow={nf} hints={hints}", flush=True)
