"""verify を e2e の venv で試す: python e2e_verify.py <scenario> <mode>  (mode: i / ii / iii)

i  : 今回の自動提案（証明済み: stubbable / submodule_stub_hints / nofollow）
ii : 証明済み + conditional の全候補
iii: 前回 c610a09 が出して実ビルドで壊れた提案を packages に明示
"""
import json, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze
from mcp_pystub.verifier import verify

ROOT = Path(r"C:\tmp\pystub-e2e")
OUT = Path(__file__).resolve().parent / "runs" / "e2e_verify"
OUT.mkdir(parents=True, exist_ok=True)
OLD_BROKEN = {
    "01_tqdm": ["numpy", "cycler", "pyparsing"],
    "02_openpyxl": ["et_xmlfile"],
    "08_matplotlib": ["mpl_toolkits"],
    "13_seaborn": ["mpl_toolkits"],
    "09_pydantic": ["annotated_types", "typing_inspection"],
}
name, mode = sys.argv[1], sys.argv[2]
entry = ROOT / name / "proj" / "entry.py"
sp = ROOT / name / "venv" / "Lib" / "site-packages"
t = time.time()
if mode == "iii":
    pk, subs, nof = OLD_BROKEN.get(name, []), [], []
else:
    a = analyze(str(entry), python_path=str(sp))
    pk = [p["package_name"] for p in a["stubbable"]]
    subs = [f"{h['submodule']}=>{h['target_package']}" for h in a["submodule_stub_hints"]]
    nof = [p["package_name"] for p in a["nofollow"]]
    if mode == "ii":
        pk += [p["package_name"] for p in a["conditional"]]
        subs += [f"{h['submodule']}=>{h['target_package']}" for h in a["conditional_submodule_hints"]]
t1 = time.time()
r = verify(str(entry), str(sp), packages=pk, submodules=subs, nofollow=nof, run_entry=True)
dt = time.time() - t1
r["_input"] = {"packages": pk, "submodules": subs, "nofollow": nof, "analyze_s": round(t1 - t, 1), "verify_s": round(dt, 1)}
(OUT / f"{name}_{mode}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
rej = [(x.get("item"), (x.get("module") or ""), (x.get("error") or "")[:160]) for x in r.get("rejected", [])]
print(f"{name} [{mode}] input={pk}+{subs}+nof{nof} passed={r.get('passed')} rejected={rej} "
      f"entry_matched={r.get('entry_matched')} imported={r.get('imported_modules')} base_fail={r.get('baseline_failures')} "
      f"hung={r.get('baseline_hung')} rounds={r.get('rounds')} verify_s={dt:.1f} err={r.get('error')}", flush=True)
