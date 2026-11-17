"""round2 修正 l: ガード中のイベントは X が読み込まれる時だけ数える / 外部オブジェクトの属性は外部 / verify の python。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"


def patch(fname, pairs):
    p = R / fname
    s = p.read_text(encoding="utf-8")
    for a, b in pairs:
        if a not in s:
            raise SystemExit(f"NOT FOUND in {fname}: {a[:90]!r}")
        s = s.replace(a, b, 1)
    p.write_text(s, encoding="utf-8")


patch("usage_analyzer.py", [
    ("""    events_by_top: dict[str, list[tuple[Unit, Event]]] = defaultdict(list)
    for key in reach.reached:
        unit = program.units[key]
        for ev in unit.events:""",
     """    events_by_top: dict[str, list[tuple[Unit, Event]]] = defaultdict(list)
    loaded_tops = {m.split(".")[0] for m in reach.loaded_modules}
    for key in reach.reached:
        unit = program.units[key]
        for ev in unit.events:
            if ev.guard and ev.guard not in loaded_tops:
                continue  # `if 'X' not in sys.modules: return` の後: X が他で読み込まれない限り実行されない"""),
])
patch("semantics.py", [
    ("""        if base.kind == "external":
            return Ref("external", base.module, base.chain + rest, base.trace)""",
     """        if base.kind == "external":
            return Ref("external", base.module, base.chain + rest, base.trace)
        if base.kind in ("external_value", "builtin_value"):
            # stdlib / 組み込みの値の属性: 解析対象のメソッドではない
            return Ref(base.kind, base.module, base.chain + rest, base.trace)"""),
])
patch("verifier.py", [
    ("""    py = _target_python(python_path)
    if py is None:
        return {"error": f"python_path に対応する python の実行ファイルが見つかりません: {python_path}"}""",
     """    py = _target_python(python_path)
    py_note = ""
    if py is None:
        # venv ではない site-packages: 現在の python に python_path を足して確かめる
        py = sys.executable
        py_note = f"python_path に対応する python が見つからないため {py} に python_path を足して確かめました"
"""),
    ("""        "note": "verify は import（と run_entry=True ならエントリーの 1 回の実行）で確かめるだけで、\"""",
     """        "python_note": py_note,
        "note": "verify は import（と run_entry=True ならエントリーの 1 回の実行）で確かめるだけで、\""""),
])
print("patched")
