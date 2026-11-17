"""prefix の使用のうち「確実」と判定されたものと、その確実な到達経路: python e2e_certdiag.py 02_openpyxl openpyxl.drawing.image"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver
from mcp_pystub.analyzer import _detect_project_root
from mcp_pystub import usage_analyzer

ROOT = Path(r"C:\tmp\pystub-e2e")
name, cand = sys.argv[1], sys.argv[2]
ep = ROOT / name / "proj" / "entry.py"
sp = ROOT / name / "venv" / "Lib" / "site-packages"
root = _detect_project_root(ep)
g = build_import_graph(str(ep), str(root), ModuleResolver(str(root), str(sp)), max_depth=10)
sem = usage_analyzer.get_semantics(g)
u = usage_analyzer.prefix_usage(sem, cand)
cert = [x for x in u.uses if usage_analyzer.is_certain_use(sem, x[0], x[1])]
print("uses", len(u.uses), "certain", len(cert))
# 確実な辺だけの親
padj = {}
for src, dst, t, ev in sem.reach.edges:
    if t == "precise" and dst not in padj:
        padj[dst] = (src, ev)
for unit, ev, sym in cert[:5]:
    print("== certain use", unit.module, unit.qualname, "L", ev.lineno, sym, "ctx", ev.ctx)
    k = unit.key
    seen = set()
    while k is not None and k not in seen and len(seen) < 25:
        seen.add(k)
        src, e2 = padj.get(k, (None, None))
        desc = ""
        if e2 is not None and not isinstance(e2, str):
            desc = f"{e2.kind}/{e2.ctx} {e2.name or ''} L{e2.lineno} cond={e2.cond}"
            if e2.ref is not None:
                desc += " " + ".".join((e2.ref.module,) + e2.ref.chain)
        print("   ", k, "<=", desc)
        k = src
