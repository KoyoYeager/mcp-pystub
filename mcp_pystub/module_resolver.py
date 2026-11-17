"""モジュール分類

import 名を stdlib / third_party / local / builtin / unresolvable に分類する。
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
import sysconfig
from pathlib import Path

from mcp_pystub.models import ModuleResolution

# Python 3.10+ で利用可能
_STDLIB_MODULES: frozenset[str] = getattr(sys, "stdlib_module_names", frozenset())

# 拡張モジュールの接尾辞（実行中の Python のもの + 汎用 .pyd / .so）
EXTENSION_SUFFIXES: tuple[str, ...] = tuple(
    dict.fromkeys(list(importlib.machinery.EXTENSION_SUFFIXES) + [".pyd", ".so"])
)


def is_extension_file(path: str | Path) -> bool:
    name = str(path).lower()
    return name.endswith(".pyd") or name.endswith(".so") or ".so." in name


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def _is_within(child: Path, parent: Path) -> bool:
    c = _norm(child)
    p = _norm(parent).rstrip("\\/")
    return c == p or c.startswith(p + os.sep) or c.startswith(p + "/")


def _stdlib_dirs() -> list[Path]:
    dirs: list[Path] = []
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib"):
        if paths.get(key):
            dirs.append(Path(paths[key]).resolve())
    # Windows: DLLs ディレクトリ（_ctypes.pyd 等）
    dll = Path(sys.base_prefix) / "DLLs"
    if dll.is_dir():
        dirs.append(dll.resolve())
    return dirs


class ModuleResolver:
    """モジュール名をファイルパスに解決し、分類する。"""

    def __init__(self, project_root: str, python_path: str = "") -> None:
        self.project_root = Path(project_root).resolve()
        self._site_packages: list[Path] = []
        self.warnings: list[str] = []
        # ローカルモジュールの探索ルート（sys.path[0] 相当を先頭に追加できる）
        self._local_roots: list[Path] = [self.project_root]
        self._python_path_given = bool(python_path)

        if python_path:
            sp = Path(python_path).resolve()
            if not sp.is_dir():
                self.warnings.append(
                    f"python_path が存在しないかディレクトリではありません: {python_path} "
                    "（third_party パッケージを解決できません）"
                )
            self._site_packages = [sp]
        else:
            seen: set[str] = set()
            for p in sys.path:
                if ("site-packages" in p or "dist-packages" in p) and Path(p).is_dir():
                    rp = Path(p).resolve()
                    if _norm(rp) not in seen:
                        seen.add(_norm(rp))
                        self._site_packages.append(rp)
        self._stdlib_dirs = _stdlib_dirs()
        self._warned_outside: set[str] = set()
        self._cache: dict[tuple[str, int, str], ModuleResolution] = {}

    # --- 設定 ---

    def add_local_root(self, root: str | Path) -> None:
        """ローカル探索ルートを先頭に追加（エントリーポイントのディレクトリ = sys.path[0]）。"""
        r = Path(root).resolve()
        if all(_norm(r) != _norm(x) for x in self._local_roots):
            self._local_roots.insert(0, r)
            self._cache.clear()

    @property
    def site_packages(self) -> list[Path]:
        return list(self._site_packages)

    # --- 解決 ---

    def resolve(
        self,
        module_name: str,
        relative_level: int = 0,
        source_file: str = "",
    ) -> ModuleResolution:
        """モジュール名を解決して分類を返す。"""
        if relative_level > 0:
            key = (module_name, relative_level, str(Path(source_file).parent) if source_file else "")
        else:
            key = (module_name, 0, "")
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        result = self._resolve_uncached(module_name, relative_level, source_file)
        self._cache[key] = result
        return result

    def _resolve_uncached(
        self, module_name: str, relative_level: int, source_file: str
    ) -> ModuleResolution:
        if relative_level > 0:
            return self._resolve_relative(module_name, relative_level, source_file)

        if not module_name:
            return ModuleResolution(module_name=module_name, classification="unresolvable")

        top_level = module_name.split(".")[0]

        # stdlib チェック
        if top_level in _STDLIB_MODULES:
            return ModuleResolution(
                module_name=module_name,
                classification="stdlib",
                top_level_package=top_level,
            )

        # 通常パッケージ / モジュール / 拡張モジュールを優先（Python の探索規則:
        # 名前空間パッケージはどの sys.path エントリにも通常のものが無い場合だけ）
        for allow_ns in (False, True):
            local = self._find_local(module_name, allow_namespace=allow_ns)
            if local:
                return self._make_resolution(Path(local), module_name)
            sp_path = self._find_in_site_packages(module_name, allow_namespace=allow_ns)
            if sp_path:
                return self._make_resolution(Path(sp_path), module_name)

        if "." in module_name:
            # サブモジュールが見つからない（実行時に生成される属性・削除済みのモジュール等）。
            # トップレベルの __init__.py を返すと名前が無限に伸びるので、ファイル無しで返す
            top_res = self.resolve(top_level)
            return ModuleResolution(
                module_name=module_name,
                classification=(
                    top_res.classification
                    if top_res.classification in ("third_party", "local", "stdlib", "builtin")
                    else "unresolvable"
                ),
                top_level_package=top_level,
            )

        # importlib.util.find_spec フォールバック
        return self._resolve_with_find_spec(module_name, top_level)

    def _make_resolution(self, path: Path, module_name: str) -> ModuleResolution:
        classification, abs_name, top_level = self._classify_resolved_path(
            path.resolve(), module_name
        )
        # 絶対 import はその名前のまま（ルートからの相対名と一致する）
        return ModuleResolution(
            module_name=module_name,
            file_path=str(path),
            classification=classification,
            top_level_package=module_name.split(".")[0] if module_name else top_level,
        )

    def _resolve_relative(
        self, module_name: str, level: int, source_file: str
    ) -> ModuleResolution:
        """相対 import を絶対パスに解決する。"""
        if not source_file:
            return ModuleResolution(
                module_name=module_name, classification="unresolvable"
            )

        source = Path(source_file).resolve()
        base_dir = source.parent
        for _ in range(level - 1):
            base_dir = base_dir.parent

        if module_name:
            parts = module_name.split(".")
            candidate = base_dir.joinpath(*parts)
        else:
            candidate = base_dir

        file_path = self._resolve_path(candidate, allow_namespace=False) or self._resolve_path(
            candidate, allow_namespace=True
        )
        if file_path:
            resolved = Path(file_path).resolve()
            classification, absolute_name, top_level = self._classify_resolved_path(
                resolved, module_name
            )

            return ModuleResolution(
                module_name=absolute_name,
                file_path=str(file_path),
                classification=classification,
                top_level_package=top_level,
            )

        return ModuleResolution(
            module_name=module_name, classification="unresolvable"
        )

    def _find_local(self, module_name: str, allow_namespace: bool = True) -> str | None:
        """ローカル探索ルート内でモジュールを探す。"""
        parts = module_name.split(".")
        for root in self._local_roots:
            result = self._resolve_path(root.joinpath(*parts), allow_namespace=allow_namespace)
            if result:
                # site-packages 内（プロジェクト内 venv）は local ではない
                if any(_is_within(Path(result), sp) for sp in self._site_packages):
                    continue
                return result
        return None

    def _find_in_site_packages(
        self, module_name: str, allow_namespace: bool = True
    ) -> str | None:
        """site-packages 内でモジュールを探す。"""
        parts = module_name.split(".")
        for sp in self._site_packages:
            result = self._resolve_path(sp.joinpath(*parts), allow_namespace=allow_namespace)
            if result:
                return result
        return None

    def find_module_file(self, abs_name: str) -> str | None:
        """絶対モジュール名からファイル（またはディレクトリ）を返す。stdlib は None。"""
        if not abs_name or abs_name.split(".")[0] in _STDLIB_MODULES:
            return None
        res = self.resolve(abs_name)
        if res.classification in ("local", "third_party") and res.file_path:
            return res.file_path
        return None

    def classify_path(self, path: str | Path, fallback_name: str = "") -> tuple[str, str, str]:
        return self._classify_resolved_path(Path(path).resolve(), fallback_name)

    @staticmethod
    def _module_name_from_rel(parts: list[str]) -> list[str]:
        if not parts:
            return parts
        last = parts[-1]
        if last == "__init__.py" or last == "__init__.pyw":
            return parts[:-1]
        if last.endswith(".py") or last.endswith(".pyw"):
            return parts[:-1] + [last.rsplit(".", 1)[0]]
        if is_extension_file(last):
            stem = last.split(".")[0]
            if stem == "__init__":
                return parts[:-1]
            return parts[:-1] + [stem]
        return parts

    def _classify_resolved_path(
        self, resolved: Path, fallback_name: str
    ) -> tuple[str, str, str]:
        """解決済みパスからの分類・絶対モジュール名・top_level_package を返す。

        site-packages（最も深いルート）を project_root より優先する
        （プロジェクト内に venv がある場合の誤分類を防ぐ）。

        Returns:
            (classification, absolute_module_name, top_level_package)
        """
        def from_root(root: Path) -> tuple[str, str]:
            rel = os.path.relpath(str(resolved), str(root))
            parts = self._module_name_from_rel(list(Path(rel).parts))
            abs_name = ".".join(parts) if parts else fallback_name
            top_level = parts[0] if parts else (fallback_name.split(".")[0] if fallback_name else "")
            return abs_name, top_level

        sp_matches = [sp for sp in self._site_packages if _is_within(resolved, sp)]
        if sp_matches:
            best = max(sp_matches, key=lambda p: len(str(p)))
            abs_name, top = from_root(best)
            return "third_party", abs_name, top

        local_matches = [r for r in self._local_roots if _is_within(resolved, r)]
        if local_matches:
            # sys.path[0]（先頭）に近い、より深いルートを優先
            best = max(local_matches, key=lambda p: len(str(p)))
            abs_name, top = from_root(best)
            return "local", abs_name, top

        # どちらにも属さない
        top_level = fallback_name.split(".")[0] if fallback_name else ""
        return "local", fallback_name, top_level

    def _resolve_path(self, candidate: Path, allow_namespace: bool = True) -> str | None:
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
        return None

    def _in_stdlib_dir(self, origin: Path) -> bool:
        for d in self._stdlib_dirs:
            if _is_within(origin, d) and not any(
                _is_within(origin, sp) for sp in self._site_packages
            ) and "site-packages" not in str(origin) and "dist-packages" not in str(origin):
                return True
        return False

    def _resolve_with_find_spec(
        self, module_name: str, top_level: str
    ) -> ModuleResolution:
        """importlib.util.find_spec を使ったフォールバック解決。"""
        try:
            spec = importlib.util.find_spec(top_level)
        except (ModuleNotFoundError, ValueError, ImportError):
            return ModuleResolution(
                module_name=module_name,
                classification="unresolvable",
                top_level_package=top_level,
            )
        except Exception:  # noqa: BLE001 — 破損した __init__ 等で何が出ても解決不能扱い
            return ModuleResolution(
                module_name=module_name,
                classification="unresolvable",
                top_level_package=top_level,
            )

        if spec is None:
            return ModuleResolution(
                module_name=module_name,
                classification="unresolvable",
                top_level_package=top_level,
            )

        # built-in モジュール（C 拡張、origin なし）
        if spec.origin is None or spec.origin in ("built-in", "frozen"):
            return ModuleResolution(
                module_name=module_name,
                classification="builtin",
                top_level_package=top_level,
            )

        origin = Path(spec.origin).resolve()

        for sp in self._site_packages:
            if _is_within(origin, sp):
                return ModuleResolution(
                    module_name=module_name,
                    file_path=str(origin),
                    classification="third_party",
                    top_level_package=top_level,
                )

        if self._in_stdlib_dir(origin):
            return ModuleResolution(
                module_name=module_name,
                file_path=str(origin),
                classification="stdlib",
                top_level_package=top_level,
            )

        # 解析対象の site-packages の外（サーバ自身の環境など）にある
        # → stdlib と誤分類せず、解決不能として警告する
        if top_level not in self._warned_outside:
            self._warned_outside.add(top_level)
            self.warnings.append(
                f"'{top_level}' は解析対象の site-packages の外（{origin.parent}）にあるため "
                "解決不能として扱います（python_path を確認してください）"
            )
        return ModuleResolution(
            module_name=module_name,
            classification="unresolvable",
            top_level_package=top_level,
        )
