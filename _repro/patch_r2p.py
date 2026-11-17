"""round2 修正 p（速度）: 書き込まれる属性名の集合をパッケージごとに 1 回の findall で作る。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"
p = R / "semantics.py"
s = p.read_text(encoding="utf-8")
start = s.index("    def attr_stored_in_package(self, top: str, name: str) -> bool:")
end = s.index("    def package_attr_stores(self, top: str) -> set[str]:  # 互換（未使用）")
new = '''    _STORE_RE = re.compile(rb"\\.\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*(?:[-+*/%&|^@]|<<|>>|//|\\*\\*)?=(?!=)")
    _DEL_RE = re.compile(rb"del\\s+[^\\n]*?\\.([A-Za-z_][A-Za-z0-9_]*)")
    _SETATTR_RE = re.compile(rb"setattr\\([^,)]*,\\s*['\\"]([A-Za-z_][A-Za-z0-9_]*)['\\"]")
    _SETATTR_DYN_RE = re.compile(rb"setattr\\([^,)]*,\\s*[^'\\"\\s]")

    def _stored_names(self, top: str) -> tuple[frozenset[bytes], bool]:
        n = len(self.modules)
        cached = self._names_cache.get(top)
        if cached is not None and cached[0] == n:
            return cached[1], cached[2]
        blob = self._package_blob(top)
        names = set(self._STORE_RE.findall(blob)) | set(self._DEL_RE.findall(blob)) | set(self._SETATTR_RE.findall(blob))
        dyn = bool(self._SETATTR_DYN_RE.search(blob)) or b"__dict__[" in blob
        out = frozenset(names)
        self._names_cache[top] = (n, out, dyn)
        return out, dyn

    def attr_stored_in_package(self, top: str, name: str) -> bool:
        """`x.NAME = ...` / `x.NAME += ...` / `del x.NAME` / setattr(x, "NAME", ...) がパッケージ内にありうるか（安全側）。"""
        names, dyn = self._stored_names(top)
        return name.encode() in names or (dyn and name.isupper())

'''
s = s[:start] + new + s[end:]
s = s.replace("""        self._blob_cache: dict[str, tuple[int, bytes]] = {}""", """        self._blob_cache: dict[str, tuple[int, bytes]] = {}
        self._names_cache: dict[str, tuple] = {}""", 1)
p.write_text(s, encoding="utf-8")
print("patched")
