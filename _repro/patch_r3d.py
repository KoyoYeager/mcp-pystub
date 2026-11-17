"""round3 修正 d: サブモジュールのヒントも 2 段（証明済み / 条件付き）、analyze の出力に conditional。"""
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
    # C 拡張の分岐: 2 段のヒント
    ('''    if resolver is not None and has_c_extensions(package_name, resolver):
        hints = find_submodule_hints(package_name, graph, sem)
        reason = "C 拡張（.pyd/.so）を含むためスタブ化不可。"
        if hints:
            submod_names = ", ".join(h.submodule for h in hints)
            reason += f" ただし {submod_names} をスタブ化することで間接排除が可能です。"
        return PackageAnalysis(verdict="required", reason=reason, submodule_stubs=hints, **common)''',
     '''    if resolver is not None and has_c_extensions(package_name, resolver):
        hints = find_submodule_hints(package_name, graph, sem)
        chints: list[SubmoduleStubHint] = []
        reason = "C 拡張（.pyd/.so）を含むためスタブ化不可。"
        if hints:
            submod_names = ", ".join(h.submodule for h in hints)
            reason += f" ただし {submod_names} をスタブ化することで間接排除が可能です。"
        else:
            chints = find_submodule_hints(package_name, graph, sem, allow_conditional=True)
            if chints:
                reason += (" 条件付きの候補: " + ", ".join(h.submodule for h in chints)
                           + "（conditional_submodule_hints を参照）")
        return PackageAnalysis(verdict="required", reason=reason, submodule_stubs=hints,
                               conditional_submodule_stubs=chints, **common)'''),
    ('''def find_submodule_hints(
    c_ext_package: str, graph: ImportGraph, sem: Semantics
) -> list[SubmoduleStubHint]:''',
     '''def find_submodule_hints(
    c_ext_package: str, graph: ImportGraph, sem: Semantics, allow_conditional: bool = False
) -> list[SubmoduleStubHint]:'''),
    ('''        for i in range(2, len(parts) + 1):
            cand = ".".join(parts[:i])
            if under(cand, T):
                break
            if not prefix_usage(sem, cand).uses:
                picked = cand
                break''',
     '''        for i in range(2, len(parts) + 1):
            cand = ".".join(parts[:i])
            if under(cand, T):
                break
            uses = prefix_usage(sem, cand).uses
            if not uses or (allow_conditional and _conditional_ok(sem, uses, cand)):
                picked = cand
                break'''),
    ('''    for c in chosen:
        others = tuple(x for x in chosen if x != c)
        if prefix_usage(sem, c, exclude=others).uses:
            return []''',
     '''    cond_info: dict[str, dict] = {}
    for c in chosen:
        others = tuple(x for x in chosen if x != c)
        uses = prefix_usage(sem, c, exclude=others).uses
        if uses:
            if not allow_conditional or not _conditional_ok(sem, uses, c):
                return []
            cond_info[c] = build_conditions(sem, uses, c)  # type: ignore[assignment]
    if allow_conditional and not cond_info:
        return []  # 証明済みのヒントとして出せる（こちらには出さない）'''),
    ('''        group = ", ".join(chosen)
        hints.append(SubmoduleStubHint(''',
     '''        group = ", ".join(chosen)
        ci = cond_info.get(cand)
        hints.append(SubmoduleStubHint(
            conditional=bool(cond_info),
            conditions=(ci or {}).get("conditions", []),
            conditions_total=(ci or {}).get("total", 0),'''),
    ('''def is_certain_use(sem: Semantics, unit: Unit, ev: Event) -> bool:''',
     '''def _conditional_ok(sem: Semantics, uses: list, prefix: str) -> bool:
    """使用が全て「確実ではない」もので、入口を特定できるか。"""
    if any(is_certain_use(sem, u, ev) for u, ev, _ in uses):
        return False
    return build_conditions(sem, uses, prefix) is not None


def is_certain_use(sem: Semantics, unit: Unit, ev: Event) -> bool:'''),
])

patch("analyzer.py", [
    ('''def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:''',
     '''def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
    include_small_conditional: bool = False,
) -> dict:'''),
    ('''    result_dict["submodule_stub_hints"] = all_hints
    result_dict["verify_status"] = "未実施"''',
     '''    result_dict["submodule_stub_hints"] = all_hints

    # 条件付き候補（仕様 9 節）: stubbable には混ぜない。削減量の大きい順、1 MB 未満は既定で省く
    min_mb = 0.0 if include_small_conditional else 1.0
    py_arg = f", python_path={python_path!r}" if python_path else ""
    conditional = []
    omitted = 0
    for a in sorted((a for a in analyses if a.verdict == "conditional"), key=lambda a: -a.estimated_size_mb):
        if a.estimated_size_mb < min_mb:
            omitted += 1
            continue
        d = asdict(a)
        d["estimated_savings_mb"] = round(a.estimated_size_mb, 1)
        d["how_to_check"] = (f"verify({str(entry)!r}{py_arg}, packages=[{a.package_name!r}], submodules=[], "
                             f"nofollow=[], run_entry=True)")
        conditional.append(d)
    chints = []
    for a in required:
        for h in a.conditional_submodule_stubs:
            h.estimated_savings_mb = round(a.estimated_size_mb, 1)
            if h.estimated_savings_mb < min_mb:
                omitted += 1
                continue
            d = asdict(h)
            d["how_to_check"] = (f"verify({str(entry)!r}{py_arg}, packages=[], "
                                 f"submodules=[{(h.submodule + '=>' + h.target_package)!r}], nofollow=[], run_entry=True)")
            chints.append(d)
    chints.sort(key=lambda d: -d["estimated_savings_mb"])
    result_dict["conditional"] = conditional
    result_dict["conditional_submodule_hints"] = chints
    result_dict["conditional_omitted_small"] = omitted
    result_dict["verify_status"] = "未実施"'''),
])

patch("server.py", [
    ('''def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:''',
     '''def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
    include_small_conditional: bool = False,
) -> dict:'''),
    ('''    return _analyze(entry_point, python_path, max_depth)''',
     '''    return _analyze(entry_point, python_path, max_depth, include_small_conditional)'''),
    ('''    - required: スタブ化不可（実際に使用されている）''',
     '''    - required: スタブ化不可（実際に使用されている）

    stubbable / nofollow / submodule_stub_hints は「全ての実行で元と同じ」と示せたものだけです。
    別のキー conditional / conditional_submodule_hints は「安全は示せないが、列挙した機能を使わないなら
    外せる」候補で、条件（壊れる入口）と確認方法（verify の呼び出し）が付きます。
    include_small_conditional=True で 1 MB 未満の条件付き候補も出します。'''),
])
print("patched")
