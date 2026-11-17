"""round2 修正 g: モジュール名の大文字小文字を区別（Windows でも import は区別する）。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"
p = R / "module_resolver.py"
s = p.read_text(encoding="utf-8")
a = '''    def _resolve_path(self, candidate: Path, allow_namespace: bool = True) -> str | None:
        """ディレクトリ（パッケージ）/ .py / 拡張モジュールとして解決する。"""
        # パッケージ: dir/__init__.py
        init = candidate / "__init__.py"
        if init.is_file():
            return str(init)
        # モジュール: file.py
        py = candidate.parent / (candidate.name + ".py")
        if py.is_file():
            return str(py)
        # 拡張モジュール: file.cp313-win_amd64.pyd / file.so 等
        parent = candidate.parent
        if parent.is_dir():
            for suffix in EXTENSION_SUFFIXES:
                ext = parent / (candidate.name + suffix)
                if ext.is_file():
                    return str(ext)
        # namespace package: ディレクトリは存在するが __init__.py なし
        if allow_namespace and candidate.is_dir():
            return str(candidate)
        return None'''
assert a in s
s = s.replace(a, '''    def _listdir(self, d: Path) -> frozenset[str] | None:
        key = str(d)
        cache = self.__dict__.setdefault("_dir_cache", {})
        if key not in cache:
            try:
                cache[key] = frozenset(os.listdir(d))
            except OSError:
                cache[key] = None
        return cache[key]

    def _exact(self, p: Path) -> bool:
        """大文字小文字まで一致する名前が親ディレクトリにあるか（import は区別する）。"""
        names = self._listdir(p.parent)
        return names is not None and p.name in names

    def _resolve_path(self, candidate: Path, allow_namespace: bool = True) -> str | None:
        """ディレクトリ（パッケージ）/ .py / 拡張モジュールとして解決する。"""
        names = self._listdir(candidate.parent)
        if names is None:
            return None
        # パッケージ: dir/__init__.py
        if candidate.name in names:
            init = candidate / "__init__.py"
            if init.is_file():
                return str(init)
        # モジュール: file.py
        if candidate.name + ".py" in names:
            py = candidate.parent / (candidate.name + ".py")
            if py.is_file():
                return str(py)
        # 拡張モジュール: file.cp313-win_amd64.pyd / file.so 等
        for suffix in EXTENSION_SUFFIXES:
            if candidate.name + suffix in names:
                ext = candidate.parent / (candidate.name + suffix)
                if ext.is_file():
                    return str(ext)
        # namespace package: ディレクトリは存在するが __init__.py なし
        if allow_namespace and candidate.name in names and candidate.is_dir():
            return str(candidate)
        return None''')
p.write_text(s, encoding="utf-8")
print("patched")
