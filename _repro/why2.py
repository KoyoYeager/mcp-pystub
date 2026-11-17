"""analyze_after.json の required 理由（使用箇所）から到達経路を辿る: python why2.py pkg1 pkg2 ..."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer

R = Path(__file__).resolve().parent / "runs" / "S40_real"
res = json.loads((R / "analyze_after.json").read_text(encoding="utf-8"))
ep = R / "proj" / "main.py"
root = _detect_project_root(ep)
g = build_import_graph(str(ep), str(root), ModuleResolver(str(root)), max_depth=10)
sem = usage_analyzer.get_semantics(g)


def fmt(ev):
    if isinstance(ev, str):
        return ev
    if ev.ref is not None:
        return f"{ev.kind}/{ev.ctx} {ev.ref.kind} {'.'.join((ev.ref.module,) + ev.ref.chain)} L{ev.lineno}"
    return f"{ev.kind} {ev.name or ','.join(ev.modules)} L{ev.lineno}"


for p in res["required"]:
    if p["package_name"] not in sys.argv[1:]:
        continue
    m = re.search(r"（([^ ]+?) 行", p["reason"])
    print("=====", p["package_name"], p["reason"][:150])
    if not m:
        continue
    where = m.group(1)
    mod, _, qual = where.partition(":")
    key = (mod, qual or "<module>")
    seen = set()
    while key in sem.reach.why and key not in seen:
        seen.add(key)
        src, ev = sem.reach.why[key]
        print(f"  {key[0]}:{key[1]}  <=  {fmt(ev)}")
        if src is None:
            break
        key = src
