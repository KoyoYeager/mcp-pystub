"""round3 修正 c: conditional（条件付き候補）の判定と条件の組み立て。"""
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


patch("models.py", [
    ('''    imported_symbols: list[str] = field(default_factory=list)  # 親が re-export するシンボル
    estimated_savings_mb: float = 0.0
    reason: str = ""''',
     '''    imported_symbols: list[str] = field(default_factory=list)  # 親が re-export するシンボル
    estimated_savings_mb: float = 0.0
    reason: str = ""
    conditional: bool = False  # 条件付き（安全を示せない。conditions の機能を使わない前提）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0'''),
    ('''    verdict: str = "required"  # "stubbable" | "nofollow" | "required"''',
     '''    verdict: str = "required"  # "stubbable" | "nofollow" | "conditional" | "required"'''),
    ('''    submodule_stubs: list[SubmoduleStubHint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass''',
     '''    submodule_stubs: list[SubmoduleStubHint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # conditional のとき: 「これを使うと壊れる」入口（仕様 9 節）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0
    unproven_because: list[str] = field(default_factory=list)
    conditional_submodule_stubs: list[SubmoduleStubHint] = field(default_factory=list)


@dataclass'''),
])

patch("usage_analyzer.py", [
    ('''from mcp_pystub.semantics import Event, Program, Reachability, Ref, Unit, compute_reachability, under''',
     '''from mcp_pystub.semantics import (
    Event, Program, Reachability, Ref, Unit, certain_units, compute_reachability, entry_paths, under,
)'''),
    ('''    graph: ImportGraph | None = None
    hooks: dict[str, str] = field(default_factory=dict)  # フックのあるモジュール名 → フックのファイル''',
     '''    graph: ImportGraph | None = None
    hooks: dict[str, str] = field(default_factory=dict)  # フックのあるモジュール名 → フックのファイル
    certain: set = field(default_factory=set)  # 確実に実行されるコード単位
    entries: dict = field(default_factory=dict)  # 確実でないユニット → 過大近似の入口'''),
    ('''    sem = Semantics(program, reach, entry_module, dict(events_by_top), unparsed_local, graph,
                    {m: hooks[m] for m in build_roots})''',
     '''    sem = Semantics(program, reach, entry_module, dict(events_by_top), unparsed_local, graph,
                    {m: hooks[m] for m in build_roots})
    sem.certain = certain_units(reach)
    sem.entries = entry_paths(reach, sem.certain)'''),
    ('''    if usage.uses:
        first_unit, first_ev, first_sym = usage.uses[0]''',
     '''    pending: dict | None = None
    certain_uses = [(u, ev, sym) for u, ev, sym in usage.uses if is_certain_use(sem, u, ev)]
    if usage.uses and not certain_uses:
        pending = build_conditions(sem, usage.uses, package_name)
        if pending is None:
            certain_uses = usage.uses  # 入口を特定できない: 区別できないので required
    if certain_uses:
        first_unit, first_ev, first_sym = certain_uses[0]'''),
    ('''        kind = "import 時に" if first_unit.qualname == "<module>" else "到達可能な関数で"''',
     '''        kind = "import 時に" if first_unit.qualname == "<module>" else "確実に実行される関数で"'''),
    ('''                f"文脈 {first_ev.ctx}、使用箇所 {len(usage.uses)} 件）。"''',
     '''                f"文脈 {first_ev.ctx}、確実な使用 {len(certain_uses)} 件 / 使用 {len(usage.uses)} 件）。"'''),
    ('''    # nofollow（仕様 3 節）: 実行される import が全て「単純な保護」（try 本体は import だけ、
    # except は pass か None/False 代入だけ）で、到達可能なコードからの参照が一切無い
    executed_imports = usage.imports''',
     '''    if pending is not None:
        return PackageAnalysis(
            verdict="conditional",
            reason=(
                "安全は示せません（到達の過大近似・入力次第の経路でだけ使われます）。"
                f"次の機能を使わない前提で外せます: {pending['summary']}（verify(..., run_entry=True) で確認を推奨）"
            ),
            gateway_functions=gateways,
            conditions=pending["conditions"],
            conditions_total=pending["total"],
            unproven_because=pending["because"],
            **common,
        )

    # nofollow（仕様 3 節）: 実行される import が全て「単純な保護」（try 本体は import だけ、
    # except は pass か None/False 代入だけ）で、到達可能なコードからの参照が一切無い
    executed_imports = usage.imports'''),
])

# 条件の組み立て（ファイル末尾に追加）
with open(R / "usage_analyzer.py", "a", encoding="utf-8") as f:
    f.write('''

# ---------------------------------------------------------------------------
# 条件付き候補（仕様 9 節）
# ---------------------------------------------------------------------------

_TAG_TEXT = {
    "name": "名前だけの振り分け（受け手の型が分からない呼び出しを、同名のメソッド全てに届くと仮定）",
    "argclass": "引数に渡したクラス（渡した先で生成されると仮定）",
    "decorator": "登録デコレータ（飾られた関数が後で呼ばれると仮定）",
    "branch": "入力・呼び出し次第の分岐（関数の中の if / except / 条件式の枝）",
}

MAX_CONDITIONS = 10


def is_certain_use(sem: Semantics, unit: Unit, ev: Event) -> bool:
    """確実に実行される使用か（import 時・ビルド時・エントリーが必ず通る経路で、分岐の中ではない）。"""
    return unit.key in sem.certain and not ev.cond


def _api_name(sem: Semantics, key: tuple[str, str]) -> str:
    """ユニットを、利用者が照らせる名前に（パッケージの最上位から再エクスポートされていればその名前）。"""
    module, qual = key
    q = qual.split("#")[0].replace(".<locals>.", ".")
    if q == "<module>":
        return f"import {module}"
    top = module.split(".")[0]
    head = q.split(".")[0]
    info = sem.program.modules.get(top)
    if info is not None and not info.opaque and head in info.bindings and module != top:
        try:
            r = sem.program.resolve(top, (head,))
            if r.module == module:
                return f"{top}.{q}()"
        except Exception:  # noqa: BLE001
            pass
    return f"{module}.{q}()"


def _path_to(sem: Semantics, key: tuple[str, str], stop: tuple[str, str], limit: int = 6) -> list[str]:
    out = []
    seen = set()
    k = key
    while k is not None and k not in seen and len(out) < limit:
        seen.add(k)
        out.append(_api_name(sem, k))
        if k == stop:
            break
        w = sem.reach.why.get(k)
        k = w[0] if w else None
    return list(reversed(out))


def build_conditions(sem: Semantics, uses: list, prefix: str) -> dict | None:
    """使用（全て確実ではない）から「これを使うと壊れる」入口を組み立てる。入口が分からなければ None。"""
    groups: dict = {}
    because: set[str] = set()
    for unit, ev, sym in uses:
        if unit.key in sem.certain and ev.cond:
            ekey, tag, eev, caller = unit.key, "branch", ev, unit.key
        else:
            info = sem.entries.get(unit.key)
            if info is None:
                return None
            ekey, tag, eev, caller = info
            if tag == "precise":
                # 確実な辺の先が確実でない（起点から外れた）: 区別できない
                return None
        because.add(tag)
        g = groups.setdefault((ekey, tag), {
            "entry": _api_name(sem, ekey),
            "entry_location": f"{ekey[0]}:{ekey[1]}" + (f" 行 {eev.lineno}" if tag == "branch" and getattr(eev, "lineno", 0) else ""),
            "why_unproven": _TAG_TEXT.get(tag, tag),
            "dispatch_name": (getattr(eev, "name", "") if tag == "name" else ""),
            "called_from": _api_name(sem, caller) if caller is not None else "",
            "uses": [],
            "path": _path_to(sem, unit.key, ekey),
            "may_be_swallowed": False,
        })
        if len(g["uses"]) < 5 and sym not in g["uses"]:
            g["uses"].append(sym)
        if ev.broad or getattr(eev, "broad", False):
            g["may_be_swallowed"] = True
    conds = sorted(groups.values(), key=lambda g: (-len(g["uses"]), g["entry"]))
    total = len(conds)
    shown = conds[:MAX_CONDITIONS]
    for g in shown:
        if g["may_be_swallowed"]:
            g["note"] = "呼び出し元が except Exception（または except:）で囲まれており、スタブの例外が握りつぶされることがあります"
    summary = ", ".join(g["entry"] for g in shown[:3]) + (f" ほか {total - 3} 件" if total > 3 else "")
    return {"conditions": shown, "total": total, "because": sorted(_TAG_TEXT.get(t, t) for t in because),
            "summary": summary}
''')
print("patched")
