"""round3 修正 f: 条件の説明に「プロジェクトが呼ぶ公開 API」からの経路を添える。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"


def patch(fname, pairs):
    p = R / fname
    s = p.read_text(encoding="utf-8")
    for a, b in pairs:
        if s.count(a) != 1:
            raise SystemExit(f"NOT FOUND/AMBIGUOUS ({s.count(a)}) in {fname}: {a[:90]!r}")
        s = s.replace(a, b, 1)
    p.write_text(s, encoding="utf-8")


patch("usage_analyzer.py", [
    ('''    certain: set = field(default_factory=set)  # 確実に実行されるコード単位''',
     '''    certain: set = field(default_factory=set)  # 確実に実行されるコード単位
    precise_parent: dict = field(default_factory=dict)  # 確実なユニット → 確実な辺での親'''),
    ('''    sem.certain = certain_units(reach)
    sem.entries = entry_paths(reach, sem.certain)''',
     '''    sem.certain = certain_units(reach)
    sem.entries = entry_paths(reach, sem.certain)
    for src, dst, t, _ev in reach.edges:
        if t == "precise" and dst in sem.certain and dst not in sem.precise_parent and src != dst:
            sem.precise_parent[dst] = src'''),
    ('''def build_conditions(sem: Semantics, uses: list, prefix: str) -> dict | None:''',
     '''def _public_route(sem: Semantics, caller: tuple[str, str] | None, limit: int = 8) -> tuple[str, list[str]]:
    """確実な呼び出し元から起点まで遡り、プロジェクトのコードが直接呼ぶ依存ライブラリの関数（公開 API）と経路。"""
    chain: list[tuple[str, str]] = []
    k = caller
    seen = set()
    while k is not None and k not in seen and len(chain) < 60:
        seen.add(k)
        chain.append(k)
        k = sem.precise_parent.get(k)
    chain.reverse()  # 起点 → 呼び出し元
    api = ""
    for i, u in enumerate(chain):
        if u[0] in sem.program.local_modules:
            continue
        if i > 0 and chain[i - 1][0] in sem.program.local_modules and u[1] != "<module>":
            api = _api_name(sem, u)
            break
    route = [_api_name(sem, u) for u in chain if u[0] not in sem.program.local_modules][-limit:]
    return api, route


def build_conditions(sem: Semantics, uses: list, prefix: str) -> dict | None:'''),
    ('''            "path": _path_to(sem, unit.key, ekey),
            "may_be_swallowed": False,
        })''',
     '''            "path": _path_to(sem, unit.key, ekey),
            "may_be_swallowed": False,
        })
        if "public_api" not in g:
            api, route = _public_route(sem, caller)
            g["public_api"] = api  # プロジェクトのコードが呼ぶ、条件の入口に至る依存ライブラリの関数
            g["route_from_project"] = route'''),
])
print("patched")
