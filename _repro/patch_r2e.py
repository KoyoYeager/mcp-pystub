"""round2 修正 e: entry_points の群（group）を区別、ヒントの閉包はビルド起点を静的グラフで判断。"""
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
    ('''def _read_entry_points(resolver: ModuleResolver) -> list[tuple[str, str]]:
    """site-packages の dist-info/entry_points.txt から (module, attr) を集める。"""
    out: list[tuple[str, str]] = []
    for sp in resolver.site_packages:
        try:
            for d in sp.glob("*.dist-info"):
                f = d / "entry_points.txt"
                if not f.is_file():
                    continue
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    if "=" not in line or line.strip().startswith(("[", "#")):
                        continue''',
     '''# コマンド（実行ファイル）用の群: プログラムから entry_points() で読み込むものではない
_SCRIPT_GROUPS = frozenset({"console_scripts", "gui_scripts", "pyinstaller40", "distutils.commands",
                            "distutils.setup_keywords", "setuptools.finalize_distribution_options"})


def _read_entry_points(resolver: ModuleResolver, group: str = "") -> list[tuple[str, str]]:
    """site-packages の dist-info/entry_points.txt から (module, attr) を集める（群の指定があればその群だけ）。"""
    out: list[tuple[str, str]] = []
    for sp in resolver.site_packages:
        try:
            for d in sp.glob("*.dist-info"):
                f = d / "entry_points.txt"
                if not f.is_file():
                    continue
                cur = ""
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    ls = line.strip()
                    if ls.startswith("[") and ls.endswith("]"):
                        cur = ls[1:-1].strip()
                        continue
                    if group and cur != group:
                        continue
                    if not group and cur in _SCRIPT_GROUPS:
                        continue
                    if "=" not in line or ls.startswith("#"):
                        continue'''),
    ('''            for mod, attr in _read_entry_points(program.resolver):''',
     '''            for mod, attr in _read_entry_points(program.resolver, ev.name):'''),
    ('''        elif fname in (".entry_points", "entry_points", ".iter_entry_points", "iter_entry_points"):
            self._ev(Event("entry_points", lineno=e.lineno))''',
     '''        elif fname in (".entry_points", "entry_points", ".iter_entry_points", "iter_entry_points"):
            grp = ""
            for k in e.keywords:
                if k.arg == "group" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                    grp = k.value.value
            if fname.endswith("iter_entry_points") and e.args and isinstance(e.args[0], ast.Constant) \\
                    and isinstance(e.args[0].value, str):
                grp = e.args[0].value
            self._ev(Event("entry_points", name=grp, lineno=e.lineno))'''),
])

patch("usage_analyzer.py", [
    ('''    after = graph_closure(graph, entry, excludes=tuple(chosen))
    if any(under(m, T) for m in after):
        return []
    if any(under(m, T) for m in import_closure(sem, excludes=tuple(chosen))):
        return []''',
     '''    # 静的な import グラフ（PyInstaller が同梱する範囲 ⊇ 実行時に読み込まれる範囲）から T が消えること。
    # T のフック（ビルド時の起点）は、T がグラフから消えれば実行されない
    after = graph_closure(graph, entry, excludes=tuple(chosen))
    if any(under(m, T) for m in after):
        return []'''),
])
print("patched")
