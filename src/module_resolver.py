"""モジュール分類

import 名を stdlib / third_party / local / builtin / unresolvable に分類する。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from src.models import ModuleResolution

# Python 3.10+ で利用可能
_STDLIB_MODULES: frozenset[str] = getattr(sys, "stdlib_module_names", frozenset())


class ModuleResolver:
    """モジュール名をファイルパスに解決し、分類する。"""

    def __init__(self, project_root: str, python_path: str = "") -> None:
        self.project_root = Path(project_root).resolve()
        self._site_packages: list[Path] = []

        if python_path:
            self._site_packages = [Path(python_path).resolve()]
        else:
            self._site_packages = [
                Path(p).resolve()
                for p in sys.path
                if "site-packages" in p and Path(p).is_dir()
            ]

    def resolve(
        self,
        module_name: str,
        relative_level: int = 0,
        source_file: str = "",
    ) -> ModuleResolution:
        """モジュール名を解決して分類を返す。"""
        if relative_level > 0:
            return self._resolve_relative(module_name, relative_level, source_file)

        top_level = module_name.split(".")[0]

        # stdlib チェック
        if top_level in _STDLIB_MODULES:
            return ModuleResolution(
                module_name=module_name,
                classification="stdlib",
                top_level_package=top_level,
            )

        # ローカルモジュールチェック
        local = self._find_local(module_name)
        if local:
            return ModuleResolution(
                module_name=module_name,
                file_path=str(local),
                classification="local",
                top_level_package=top_level,
            )

        # third_party チェック（site-packages 内を探索）
        sp_path = self._find_in_site_packages(module_name)
        if sp_path:
            return ModuleResolution(
                module_name=module_name,
                file_path=str(sp_path),
                classification="third_party",
                top_level_package=top_level,
            )

        # importlib.util.find_spec フォールバック
        return self._resolve_with_find_spec(module_name, top_level)

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
            candidate = base_dir / "/".join(parts)
        else:
            candidate = base_dir

        # パッケージ (__init__.py) またはモジュール (.py) を探す
        file_path = self._resolve_path(candidate)
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

    def _find_local(self, module_name: str) -> str | None:
        """project_root 内でモジュールを探す。"""
        parts = module_name.split(".")
        candidate = self.project_root / "/".join(parts)
        return self._resolve_path(candidate)

    def _find_in_site_packages(self, module_name: str) -> str | None:
        """site-packages 内でモジュールを探す。"""
        parts = module_name.split(".")
        for sp in self._site_packages:
            candidate = sp / "/".join(parts)
            result = self._resolve_path(candidate)
            if result:
                return result
        return None

    def _classify_resolved_path(
        self, resolved: Path, fallback_name: str
    ) -> tuple[str, str, str]:
        """解決済みパスからの分類・絶対モジュール名・top_level_package を返す。

        Returns:
            (classification, absolute_module_name, top_level_package)
        """
        # project_root 内ならローカル
        try:
            rel = resolved.relative_to(self.project_root)
            parts = list(rel.parts)
            if parts and parts[-1] == "__init__.py":
                parts = parts[:-1]
            elif parts and parts[-1].endswith(".py"):
                parts[-1] = parts[-1][:-3]
            abs_name = ".".join(parts) if parts else fallback_name
            top_level = parts[0] if parts else (fallback_name.split(".")[0] if fallback_name else "")
            return "local", abs_name, top_level
        except ValueError:
            pass

        # site-packages 内なら third_party
        for sp in self._site_packages:
            try:
                rel = resolved.relative_to(sp)
                parts = list(rel.parts)
                if parts and parts[-1] == "__init__.py":
                    parts = parts[:-1]
                elif parts and parts[-1].endswith(".py"):
                    parts[-1] = parts[-1][:-3]
                abs_name = ".".join(parts) if parts else fallback_name
                top_level = parts[0] if parts else (fallback_name.split(".")[0] if fallback_name else "")
                return "third_party", abs_name, top_level
            except ValueError:
                continue

        # どちらにも属さない
        top_level = fallback_name.split(".")[0] if fallback_name else ""
        return "local", fallback_name, top_level

    def _resolve_path(self, candidate: Path) -> str | None:
        """ディレクトリ（パッケージ）または .py ファイルとして解決する。"""
        # パッケージ: dir/__init__.py
        init = candidate / "__init__.py"
        if init.is_file():
            return str(init)
        # モジュール: file.py
        py = candidate.with_suffix(".py")
        if py.is_file():
            return str(py)
        # namespace package: ディレクトリは存在するが __init__.py なし
        if candidate.is_dir():
            return str(candidate)
        return None

    def _resolve_with_find_spec(
        self, module_name: str, top_level: str
    ) -> ModuleResolution:
        """importlib.util.find_spec を使ったフォールバック解決。"""
        try:
            spec = importlib.util.find_spec(top_level)
        except (ModuleNotFoundError, ValueError):
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
        if spec.origin is None or spec.origin == "built-in":
            return ModuleResolution(
                module_name=module_name,
                classification="builtin",
                top_level_package=top_level,
            )

        origin = Path(spec.origin).resolve()

        # site-packages 内か判定
        for sp in self._site_packages:
            if str(origin).startswith(str(sp)):
                return ModuleResolution(
                    module_name=module_name,
                    file_path=str(origin),
                    classification="third_party",
                    top_level_package=top_level,
                )

        # project_root 内か判定
        if str(origin).startswith(str(self.project_root)):
            return ModuleResolution(
                module_name=module_name,
                file_path=str(origin),
                classification="local",
                top_level_package=top_level,
            )

        # stdlib の可能性（lib/ 以下など）
        return ModuleResolution(
            module_name=module_name,
            file_path=str(origin),
            classification="stdlib",
            top_level_package=top_level,
        )
