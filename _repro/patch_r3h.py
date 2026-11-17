"""round3 修正 h: プロジェクトのコードを調べた証拠・confidence・要約 / スタブは標準エラーにも出す / 重なりの防御。"""
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
    ('''    conditional: bool = False  # 条件付き（安全を示せない。conditions の機能を使わない前提）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0''',
     '''    conditional: bool = False  # 条件付き（安全を示せない。conditions の機能を使わない前提）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0
    confidence: str = ""  # high: 障害が全て依存ライブラリ内の過大近似で、プロジェクトに入口の使用が 0 件 / low'''),
    ('''    conditions_total: int = 0
    unproven_because: list[str] = field(default_factory=list)''',
     '''    conditions_total: int = 0
    unproven_because: list[str] = field(default_factory=list)
    confidence: str = ""'''),
])

patch("usage_analyzer.py", [
    ('''    conds = sorted(groups.values(), key=lambda g: (-len(g["uses"]), g["entry"]))''',
     '''    conds = sorted(groups.values(), key=lambda g: (-len(g["uses"]), g["entry"]))
    tags_of: dict[int, str] = {}
    for (ekey, tag), g in groups.items():
        tags_of[id(g)] = tag
    all_project_zero = True
    for g in conds:
        name = g["entry"].rstrip("()").split(".")[-1] if not g["entry"].startswith("import ") else ""
        hits = project_name_uses(sem, name) if name else []
        g["project_uses_count"] = len(hits)
        g["project_uses"] = hits[:10]
        if hits:
            all_project_zero = False
            g["project_note"] = f"プロジェクトのコードに `{name}` の呼び出し・参照が {len(hits)} 件あります（照らしてください）"
        else:
            g["project_note"] = (f"プロジェクトのコードは `{name}` を直接使っていません"
                                 + ("（到達は依存ライブラリ内の名前の一致による推定だけ）" if tags_of[id(g)] == "name" else ""))
    confidence = "high" if (all_project_zero and "branch" not in because) else "low"'''),
    ('''    return {"conditions": shown, "total": total, "because": sorted(_TAG_TEXT.get(t, t) for t in because),
            "summary": summary}''',
     '''    return {"conditions": shown, "total": total, "because": sorted(_TAG_TEXT.get(t, t) for t in because),
            "summary": summary, "confidence": confidence}


def project_name_uses(sem: Semantics, name: str) -> list[str]:
    """プロジェクト自身のコード（local 分類のファイル）での、その名前の呼び出し・属性参照（ファイル:行）。"""
    cache = sem.__dict__.setdefault("_name_hits", {})
    if name in cache:
        return cache[name]
    import ast as _ast
    hits: list[str] = []
    for m in sorted(sem.program.local_modules):
        info = sem.program.modules.get(m)
        if info is None or info.tree is None:
            continue
        for n in _ast.walk(info.tree):
            if (isinstance(n, _ast.Attribute) and n.attr == name) or (
                isinstance(n, _ast.Name) and n.id == name and isinstance(n.ctx, _ast.Load)
            ):
                hits.append(f"{info.path}:{getattr(n, 'lineno', 0)}")
    cache[name] = hits
    return hits'''),
    ('''            conditions=pending["conditions"],
            conditions_total=pending["total"],
            unproven_because=pending["because"],''',
     '''            conditions=pending["conditions"],
            conditions_total=pending["total"],
            unproven_because=pending["because"],
            confidence=pending["confidence"],'''),
    ('''        ci = cond_info.get(cand)
        hints.append(SubmoduleStubHint(
            conditional=bool(cond_info),''',
     '''        ci = cond_info.get(cand)
        hints.append(SubmoduleStubHint(
            confidence=(("high" if all(x.get("confidence") == "high" for x in cond_info.values()) else "low")
                        if cond_info else ""),
            conditional=bool(cond_info),'''),
])

patch("analyzer.py", [
    ('''    chints.sort(key=lambda d: -d["estimated_savings_mb"])
    result_dict["conditional"] = conditional''',
     '''    rank = {"high": 0, "low": 1, "": 2}
    conditional.sort(key=lambda d: (rank.get(d.get("confidence", ""), 2), -d["estimated_savings_mb"]))
    chints.sort(key=lambda d: (rank.get(d.get("confidence", ""), 2), -d["estimated_savings_mb"]))
    result_dict["conditional"] = conditional'''),
    ('''    result_dict["conditional_omitted_small"] = omitted
    result_dict["verify_status"] = "未実施"''',
     '''    result_dict["conditional_omitted_small"] = omitted
    result_dict["verify_status"] = "未実施"
    result_dict = {"summary": _summary(result_dict, str(entry), python_path, required), **result_dict}'''),
])

with open(R / "analyzer.py", "a", encoding="utf-8") as f:
    f.write('''

def _cond_text(conds: list[dict], total: int) -> tuple[str, int]:
    ents = [c.get("entry", "") for c in conds[:3]]
    uses = sum(int(c.get("project_uses_count", 0)) for c in conds)
    more = f" ほか {total - 3} 件" if total > 3 else ""
    return "、".join(ents) + more, uses


def _summary(rd: dict, entry: str, python_path: str, required: list) -> dict:
    """結果の先頭に置く要約: 証明済み・条件付き（high）の削減見込みと、次の手順。"""
    proven_mb = sum(p.get("estimated_size_mb", 0.0) for p in rd["stubbable"]) + \\
        sum(p.get("estimated_size_mb", 0.0) for p in rd["nofollow"])
    size_of = {a.package_name: a.estimated_size_mb for a in required}
    proven_targets = {h["target_package"] for h in rd.get("submodule_stub_hints", [])}
    proven_mb += sum(size_of.get(t, 0.0) for t in proven_targets)
    recs: list[str] = []
    for p in rd["stubbable"]:
        recs.append(f"{p['package_name']} をスタブ化できます（証明済み、約 {p['estimated_size_mb']:.1f} MB）")
    for h in rd.get("submodule_stub_hints", []):
        recs.append(f"{h['submodule']} をスタブ化すると {h['target_package']} を排除できます（証明済み）")
    high_mb = 0.0
    high_n = 0
    seen_targets: set[str] = set()
    cond_recs: list[str] = []
    for h in rd.get("conditional_submodule_hints", []):
        text, uses = _cond_text(h.get("conditions", []), h.get("conditions_total", 0))
        if h.get("confidence") == "high":
            if h["target_package"] not in seen_targets:
                high_mb += h.get("estimated_savings_mb", 0.0)
                seen_targets.add(h["target_package"])
            high_n += 1
        cond_recs.append((h.get("confidence"), -h.get("estimated_savings_mb", 0.0),
                          f"[{h.get('confidence')}] {h['submodule']} をスタブ化すると {h['target_package']} を排除できます"
                          f"（約 {h.get('estimated_savings_mb', 0.0):.1f} MB。条件: {text or '—'} を使わない。プロジェクト内の使用 {uses} 件）"))
    for p in rd.get("conditional", []):
        text, uses = _cond_text(p.get("conditions", []), p.get("conditions_total", 0))
        if p.get("confidence") == "high":
            high_mb += p.get("estimated_savings_mb", 0.0)
            high_n += 1
        cond_recs.append((p.get("confidence"), -p.get("estimated_savings_mb", 0.0),
                          f"[{p.get('confidence')}] {p['package_name']} をスタブ化できます"
                          f"（約 {p.get('estimated_savings_mb', 0.0):.1f} MB。条件: {text or '—'} を使わない。プロジェクト内の使用 {uses} 件）"))
    cond_recs.sort(key=lambda x: (0 if x[0] == "high" else 1, x[1]))
    py = f", python_path={python_path!r}" if python_path else ""
    steps = [
        "1. 推奨の上から選ぶ（[high] = 障害は依存ライブラリ内の過大近似だけで、プロジェクトのコードに条件の入口の使用が無い）",
        f"2. サブモジュールなら generate_submodule({entry!r}, <親>, <サブモジュール>{py})、パッケージなら generate(...) でスタブを作る",
        f"3. verify({entry!r}{py}, packages=[...], submodules=['<サブモジュール>=><排除する C 拡張>'], run_entry=True) で確かめる",
        "4. build_instructions の手順で適用 → ビルド → 実行結果を元と比べる → 手順どおり復元",
    ]
    return {
        "proven_savings_mb": round(proven_mb, 1),
        "conditional_high_savings_mb": round(high_mb, 1),
        "conditional_high_count": high_n,
        "recommendations": [r for _, _, r in cond_recs if r.startswith("[high]")] + recs + [
            r for _, _, r in cond_recs if not r.startswith("[high]")],
        "next_steps": steps,
        "note": "[high] / [low] は条件付き候補（安全の証明ではありません）。stubbable / submodule_stub_hints だけが証明済みです。",
    }
''')

patch("stub_generator.py", [
    ('''def _raise(n):
    raise PystubConditionalStubError("mcp-pystub のスタブです。" + _PYSTUB_NOTE + "（呼ばれたもの: " + n + "）")''',
     '''def _raise(n):
    m = "mcp-pystub のスタブです。" + _PYSTUB_NOTE + "（呼ばれたもの: " + n + "）"
    import os, sys
    try:
        sys.stderr.write("[mcp-pystub] " + m + "\\n")  # 呼び出し元の except で握りつぶされても分かるように
        sys.stderr.flush()
    except Exception:
        pass
    if os.environ.get("MCP_PYSTUB_STRICT") == "1":
        os._exit(70)
    raise PystubConditionalStubError(m)'''),
    # 重なりの防御: 祖先がスタブとして既に置き換えられているなら何もしない
    ('''    backup_cmds = ["# === 元ファイルをバックアップ領域へ移動（必ず最初に実行。既にあれば何もしない） ==="]
    for rel in rel_moves:''',
     '''    # 重なりの防御: 親パッケージが既に（別のスタブとして）バックアップ領域へ移されていれば、
    # この項目はその中に含まれるので適用・復元とも何もしない（仕様 7 節）
    anc_checks = []
    parts = Path(name.replace(".", "/")).parts
    for i in range(1, len(parts)):
        anc_rel = Path(*parts[:i])
        anc_checks.append(f"[ -e {_q(backup_root / (import_root / anc_rel).relative_to(site_root))} ]")
    covered = " || ".join(anc_checks)
    skip = (f"if {covered}; then echo 'skip: {name} は親パッケージのスタブに含まれます'; exit 0; fi") if anc_checks else ""
    backup_cmds = ["# === 元ファイルをバックアップ領域へ移動（必ず最初に実行。既にあれば何もしない） ==="]
    if skip:
        backup_cmds.append(skip)
    for rel in rel_moves:'''),
    ('''    install_cmds = [
        "# スタブを配置（生成された files を _stubs/ に書き出してから。バックアップが無ければ中止）:",''',
     '''    install_cmds = [
        "# スタブを配置（生成された files を _stubs/ に書き出してから。バックアップが無ければ中止）:",
    ] + ([skip] if skip else []) + ['''),
    ('''    uninstall_cmds = ["# === 方法1: バックアップから復元（推奨・高速・確実） ==="]''',
     '''    uninstall_cmds = ["# === 方法1: バックアップから復元（推奨・高速・確実） ==="] + ([skip] if skip else [])'''),
])
print("patched")
