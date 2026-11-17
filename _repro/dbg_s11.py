import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.stub_generator import generate_stubs
R = Path(__file__).resolve().parent / "runs" / "S11_from_import_submodule"
g = generate_stubs(str(R / "proj/main.py"), "heavyx", python_path=str(R / "sp"))
print(g["referenced_symbols"])
for k, v in g["files"].items():
    print("---", k); print(v.split("__getattr__ = _g\n", 1)[1])
from mcp_pystub.stub_generator import collect_accesses, _prepare
from mcp_pystub.usage_analyzer import get_semantics
(entry, resolver, graph), _ = _prepare(str(R / "proj/main.py"), str(R / "sp"), 10)
sem = get_semantics(graph)
acc = collect_accesses(sem, "heavyx")
print({m: dict(a.names) for m, a in acc.items()})
for (m, q), u in sem.program.units.items():
    if m == "libA":
        for ev in u.events:
            print(q, ev.kind, ev.ctx, ev.ref.kind if ev.ref else None, ev.ref.trace if ev.ref else ev.modules)
