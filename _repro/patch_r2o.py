"""round2 修正 o（速度）: パッケージ内の `x.NAME = ...` の検出をファイルのバイト列の正規表現で（安全側: 誤検出は定数扱いをやめるだけ）。"""
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
    ('''    def package_attr_stores(self, top: str) -> set[str]:
        """パッケージ top の（読み込んだ）モジュールで `x.NAME = ...` と書き込まれる属性名。"""
        n = len(self.modules)
        cached = self._stores_cache.get(top)
        if cached is not None and cached[0] == n:
            return cached[1]
        out: set[str] = set()
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top:
                continue
            self._collect_mutations(other)
            out |= other.attr_stores
        self._stores_cache[top] = (n, out)
        return out''',
     '''    def _package_blob(self, top: str) -> bytes:
        """パッケージ top の（読み込んだ）モジュールのソースを連結したもの（属性への書き込みの検出用）。"""
        n = len(self.modules)
        cached = self._blob_cache.get(top)
        if cached is not None and cached[0] == n:
            return cached[1]
        parts: list[bytes] = []
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top or not other.path:
                continue
            try:
                parts.append(Path(other.path).read_bytes())
            except OSError:
                continue
        blob = b"\\n".join(parts)
        self._blob_cache[top] = (n, blob)
        return blob

    def attr_stored_in_package(self, top: str, name: str) -> bool:
        """`x.NAME = ...` / `x.NAME += ...` / `del x.NAME` / setattr(...) がパッケージ内にありうるか（安全側）。"""
        blob = self._package_blob(top)
        if b"setattr(" in blob or b"__dict__[" in blob:
            # 名前が文字列として現れるなら書き込みうる
            if name.encode() in blob and re.search(rb"setattr\\([^)]*['\\"]" + re.escape(name.encode()) + rb"['\\"]", blob):
                return True
        pat = rb"\\.\\s*" + re.escape(name.encode()) + rb"\\s*(?:[-+*/%&|^@]|<<|>>|//|\\*\\*)?=(?!=)"
        if re.search(pat, blob):
            return True
        return bool(re.search(rb"del\\s+[^\\n]*\\." + re.escape(name.encode()) + rb"\\b", blob))

    def package_attr_stores(self, top: str) -> set[str]:  # 互換（未使用）
        return set()'''),
    ('''        if ok:
            stores = self.package_attr_stores(info.name.split(".")[0])
            if name in stores or "*" in stores:
                ok = False''',
     '''        if ok and self.attr_stored_in_package(info.name.split(".")[0], name):
            ok = False'''),
    ('''    def _class_attr_stored(self, info: ModuleInfo, attr: str) -> bool:
        stores = self.package_attr_stores(info.name.split(".")[0])
        return attr in stores or "*" in stores''',
     '''    def _class_attr_stored(self, info: ModuleInfo, attr: str) -> bool:
        return self.attr_stored_in_package(info.name.split(".")[0], attr)'''),
    ('''        self._stores_cache: dict[str, tuple[int, set[str]]] = {}''',
     '''        self._stores_cache: dict[str, tuple[int, set[str]]] = {}
        self._blob_cache: dict[str, tuple[int, bytes]] = {}'''),
    ('''import ast
import builtins
import sys''',
     '''import ast
import builtins
import re
import sys'''),
])
print("patched")
