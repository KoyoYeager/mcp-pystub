"""第 7 回 第 2 段 受け入れ 1: 設計した事例（proj / proj_b）で記録の区分を確かめる。"""
import json, subprocess, sys
from pathlib import Path
W = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, W)
from mcp_pystub.trace import load, observe  # noqa: E402
D = Path(r"C:\tmp\pystub-e2e\designed")
O = Path(r"C:\tmp\pystub-e2e\after_trace\designed")
O.mkdir(parents=True, exist_ok=True)
PY = D / "venv" / "Scripts" / "python.exe"
SP = [str(D / "venv" / "Lib" / "site-packages")]
EXPECT = {"proj": {"heavyframe": ("not_imported", "import_only"), "typesonly": ("not_imported", "import_only"),
                   "constlib": ("not_imported", "import_only"), "optaccel": ("not_imported", "import_only"),
                   "qtbig": ("not_imported", "import_only"), "usedlib": ("executed",), "importtime": ("executed",),
                   "optused": ("executed",)},
          "proj_b": {"heavyframe": ("executed",), "optaccel": ("executed",)}}
res = {}
ok = True
for pj in ("proj", "proj_b"):
    tf = O / f"{pj}_trace.json"
    code = f"import sys; sys.path.append({W!r}); from mcp_pystub.trace import main; sys.exit(main(['--out', {str(tf)!r}, 'entry.py']))"
    r = subprocess.run([str(PY), "-c", code], cwd=str(D / pj), capture_output=True, text=True)
    t = load(str(tf))
    got = {p: observe(t, p, SP) for p in ["heavyframe", "typesonly", "constlib", "optaccel", "qtbig", "usedlib",
                                         "importtime", "optused", "libconv", "libconv.gui"]}
    res[pj] = {p: {"state": o["state"], "functions": o["functions_executed"], "top": o["top_functions"][:3]} for p, o in got.items()}
    for p, allowed in EXPECT[pj].items():
        good = got[p]["state"] in allowed
        ok &= good
        print(pj, p, got[p]["state"], "OK" if good else "NG", got[p]["top_functions"][:3])
res["all_ok"] = ok
(O / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print("ALL_OK", ok)
