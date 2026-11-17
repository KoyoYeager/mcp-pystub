"""round2 修正 c: エントリーポイントの対象は required、安全網は dunder に答えない。"""
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
    ("""    presence_checked: set[str] = field(default_factory=set)""",
     """    presence_checked: set[str] = field(default_factory=set)
    entry_point_modules: set[str] = field(default_factory=set)  # entry_points() で読み込まれうるモジュール"""),
    ("""            for mod, attr in _read_entry_points(program.resolver):
                load_module(mod)""",
     """            for mod, attr in _read_entry_points(program.resolver):
                st.entry_point_modules.add(mod)
                load_module(mod)"""),
])

patch("usage_analyzer.py", [
    ("""    # 有無の検出（仕様 3 節）""",
     """    # エントリーポイント・プラグイン登録（仕様 4 節: 解析不能）: entry_points() で読み込まれて
    # 未知のコードから呼ばれる
    eps = [m for m in sem.reach.entry_point_modules if under(m, package_name)]
    if eps:
        return PackageAnalysis(
            verdict="required",
            reason=f"到達可能なコードが entry_points() でプラグインを読み込み、{eps[0]} が対象です"
            "（呼ばれ方を静的に追えないため安全側判定）。",
            gateway_functions=gateways,
            **common,
        )

    # 有無の検出（仕様 3 節）"""),
])

patch("stub_generator.py", [
    ('''        k = c.__dict__.get("_pystub_known")
        if k is None or n in k:''',
     '''        k = c.__dict__.get("_pystub_known")
        if n[:2] != "__" and (k is None or n in k):'''),
    ('''_NET_KNOWN = """_PYSTUB_KNOWN = {known}
def __getattr__(n):
    if n in _PYSTUB_KNOWN:''',
     '''_NET_KNOWN = """_PYSTUB_KNOWN = {known}
def __getattr__(n):
    if n in _PYSTUB_KNOWN and n[:2] != "__":'''),
])
print("patched")
