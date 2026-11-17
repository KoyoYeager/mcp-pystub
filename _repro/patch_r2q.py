"""round2 修正 q（速度）: 書き込まれる属性名はモジュールを読み込んだ時に 1 回だけ数え、パッケージごとに足し込む。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"
p = R / "semantics.py"
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    if a not in s:
        raise SystemExit("NOT FOUND: " + a[:80])
    s = s.replace(a, b, 1)


start = s.index("    def _stored_names(self, top: str) -> tuple[frozenset[bytes], bool]:")
end = s.index("    def attr_stored_in_package(self, top: str, name: str) -> bool:")
s = s[:start] + '''    def _note_stores(self, name: str, data: bytes) -> None:
        top = name.split(".")[0]
        names = self._pkg_store_names.setdefault(top, set())
        names.update(self._STORE_RE.findall(data))
        names.update(self._DEL_RE.findall(data))
        names.update(self._SETATTR_RE.findall(data))
        if self._SETATTR_DYN_RE.search(data) or b"__dict__[" in data:
            self._pkg_dyn_store.add(top)

''' + s[end:]
rep('''    def attr_stored_in_package(self, top: str, name: str) -> bool:
        """`x.NAME = ...` / `x.NAME += ...` / `del x.NAME` / setattr(x, "NAME", ...) がパッケージ内にありうるか（安全側）。"""
        names, dyn = self._stored_names(top)
        return name.encode() in names or (dyn and name.isupper())''',
    '''    def attr_stored_in_package(self, top: str, name: str) -> bool:
        """`x.NAME = ...` / `x.NAME += ...` / `del x.NAME` / setattr(x, "NAME", ...) がパッケージ内にありうるか（安全側）。"""
        names = self._pkg_store_names.get(top, set())
        return name.encode() in names or (top in self._pkg_dyn_store and name.isupper())''')
rep('''        self._names_cache: dict[str, tuple] = {}''',
    '''        self._names_cache: dict[str, tuple] = {}
        self._pkg_store_names: dict[str, set[bytes]] = {}
        self._pkg_dyn_store: set[str] = set()''')
rep('''        tree, error = parse_file(str(p))
        info = ModuleInfo(name=name, path=str(p), is_package=is_pkg, tree=tree)''',
    '''        tree, error = parse_file(str(p))
        info = ModuleInfo(name=name, path=str(p), is_package=is_pkg, tree=tree)
        try:
            self._note_stores(name, p.read_bytes())
        except OSError:
            pass''')
p.write_text(s, encoding="utf-8")
print("patched")
