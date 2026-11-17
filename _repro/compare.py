"""修正前（runs_s5b.log）と修正後（analyze_after.json）の stubbable を比べる。"""
import ast, json
from pathlib import Path
D = Path(__file__).resolve().parent
old_line = [l for l in open(D / "runs_s5b.log", encoding="utf-8") if l.strip().startswith("stubbable:")][0]
old = set(ast.literal_eval(old_line.split(":", 1)[1].strip()))
new = json.load(open(D / "runs/S40_real/analyze_after.json", encoding="utf-8"))
ns = {p["package_name"] for p in new["stubbable"]}
reasons = {p["package_name"]: (k, p["reason"]) for k in ("stubbable", "nofollow", "required") for p in new[k]}
print("old", len(old), "new", len(ns))
print("added:", sorted(ns - old))
moved = sorted(old - ns)
print("moved out:", len(moved))
for m in moved:
    k, r = reasons.get(m, ("-", "(解析対象外)"))
    print(f"  {m} [{k}] {r[:180]}")
