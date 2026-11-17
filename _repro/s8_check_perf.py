import sys, time
from harness import setup
from mcp_pystub.analyzer import _detect_project_root, _build_import_chains
from mcp_pystub.import_graph import build_import_graph
from mcp_pystub.module_resolver import ModuleResolver

for N in (10, 11, 12):
    sp = {"heavyy.py": "def compute():\n    return 1\n"}
    for i in range(N):
        imps = "".join(f"import dep.m{j}\n" for j in range(N) if j != i)
        sp[f"dep/m{i}.py"] = imps + ("import heavyy\n" if i == N - 1 else "")
    sp["dep/__init__.py"] = "import dep.m0\n"
    root, spd, proj = setup(f"S80_check_perf_{N}", sp, {"main.py": "import dep\n"})
    ep = proj / "main.py"
    t = time.time()
    res = ModuleResolver(str(_detect_project_root(ep)), str(spd))
    g = build_import_graph(str(ep), str(_detect_project_root(ep)), res, max_depth=50)
    t1 = time.time()
    chains = _build_import_chains(g, "heavyy")
    t2 = time.time()
    print(f"N={N} nodes={len(g.nodes)} edges={len(g.edges)} graph={t1-t:.2f}s chains={t2-t1:.2f}s found={len(chains)}", flush=True)
