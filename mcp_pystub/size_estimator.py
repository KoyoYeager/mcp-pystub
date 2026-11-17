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
    """パッケージの推定サイズ（MB）を返す。

    site-packages 内のパッケージディレクトリを走査し、
    全ファイルの合計サイズを計算する。

    Returns:
        推定サイズ（MB）。見つからない場合は 0.0。
    """
    for sp_dir in site_packages_dirs:
        sp = Path(sp_dir)
        pkg_dir = sp / package_name
        if pkg_dir.is_dir():
            return _dir_size_mb(pkg_dir)

        # 単一ファイルモジュール
        pkg_file = sp / f"{package_name}.py"
        if pkg_file.is_file():
            return pkg_file.stat().st_size / (1024 * 1024)

    return 0.0


def _dir_size_mb(path: Path) -> float:
    """ディレクトリの合計サイズを MB で返す。"""
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total / (1024 * 1024)
