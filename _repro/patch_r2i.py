"""round2 修正 i: entry_points() の呼び出し元が P 自身なら、P をスタブにすれば呼ばれないので数えない。"""
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


patch("semantics.py", [
    ("""    entry_point_modules: set[str] = field(default_factory=set)  # entry_points() で読み込まれうるモジュール""",
     """    entry_point_modules: set[str] = field(default_factory=set)  # entry_points() で読み込まれうるモジュール
    entry_point_calls: set[tuple[str, str]] = field(default_factory=set)  # (呼び出し元モジュール, 対象モジュール)"""),
    ("""                st.entry_point_modules.add(mod)
                load_module(mod)""",
     """                st.entry_point_modules.add(mod)
                st.entry_point_calls.add((unit.module, mod))
                load_module(mod)"""),
])
patch("usage_analyzer.py", [
    ("""    eps = [m for m in sem.reach.entry_point_modules if under(m, package_name)]""",
     """    eps = sorted({m for caller, m in sem.reach.entry_point_calls
                  if under(m, package_name) and not under(caller, package_name)})"""),
])
print("patched")
