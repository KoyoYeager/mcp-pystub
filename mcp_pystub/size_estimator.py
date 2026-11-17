"""パッケージサイズ推定

site-packages 内のファイル数・合計サイズからパッケージの重量を推定する。
ハードコードなし — ファイルシステムの実測値のみ使用。
"""

from __future__ import annotations

from pathlib import Path


def estimate_package_size(
    package_name: str,
    site_packages_dirs: list[str],
) -> float:
    """パッケージのインストール済みサイズ（MB）。exe の削減量ではない（同梱範囲・圧縮で変わる）。

    数えるもの: パッケージのディレクトリ（__pycache__ を除く）、同名の単一ファイルモジュール（.py / 拡張モジュール）、
    delvewheel 等が置く `<パッケージ>.libs` のディレクトリ。見つからない場合は 0.0。
    """
    total = 0
    found = False
    for sp_dir in site_packages_dirs:
        sp = Path(sp_dir)
        pkg_dir = sp / package_name
        if pkg_dir.is_dir():
            total += _dir_size(pkg_dir)
            found = True
        try:
            for f in sp.iterdir():
                n = f.name
                if f.is_file() and (n == f"{package_name}.py" or (n.startswith(package_name + ".")
                                                                   and n.endswith((".pyd", ".so", ".dll")))):
                    total += f.stat().st_size
                    found = True
                elif f.is_dir() and n == f"{package_name}.libs":
                    total += _dir_size(f)
        except OSError:
            pass
        if found:
            break
    return total / (1024 * 1024)


def _dir_size(path: Path) -> int:
    """ディレクトリの合計バイト（__pycache__ は数えない: ビルドには入らず、実行のたびに変わる）。"""
    total = 0
    try:
        for f in path.rglob("*"):
            if "__pycache__" in f.parts:
                continue
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _dir_size_mb(path: Path) -> float:  # 互換
    return _dir_size(path) / (1024 * 1024)
