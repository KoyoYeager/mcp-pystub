"""mcp-pystub — Python exe スタブ最適化 MCP サーバー

import グラフを AST 解析し、exe ビルド時にスタブ置換可能なパッケージを自動検出する。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.analyzer import analyze, check_usage, inspect_graph
from src.stub_generator import generate_stubs

mcp = FastMCP("pystub")


@mcp.tool()
def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """プロジェクトのエントリーポイントから import グラフを解析し、
    スタブ置換可能なパッケージを自動検出します。

    各パッケージは以下のいずれかに判定されます:
    - stubbable: スタブ化可能（プロジェクトの実行パスで未使用）
    - nofollow: try/except 保護あり（--nofollow-import-to で除外推奨）
    - required: スタブ化不可（実際に使用されている）

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        python_path: site-packages パス（空の場合は現在の環境を自動検出）
        max_depth: import グラフの最大探索深度（デフォルト: 10）

    Returns:
        stubbable / nofollow / required に分類されたパッケージ一覧
    """
    from src.analyzer import analyze as _analyze
    return _analyze(entry_point, python_path, max_depth)


@mcp.tool()
def graph(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 5,
) -> dict:
    """エントリーポイントからの import グラフを構築して可視化します。

    全モジュールの依存関係をノードとエッジで返します。
    各ノードは stdlib / third_party / local / builtin / unresolvable に分類されます。

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        python_path: site-packages パス（空の場合は現在の環境を自動検出）
        max_depth: 最大探索深度（デフォルト: 5）

    Returns:
        ノード・エッジ・統計情報を含むグラフデータ
    """
    return inspect_graph(entry_point, python_path, max_depth)


@mcp.tool()
def check(
    entry_point: str,
    package_name: str,
    python_path: str = "",
    max_depth: int = 5,
) -> dict:
    """特定のパッケージがプロジェクト内でどのように使われているか詳細に分析します。

    import チェーン、gateway 関数、プロジェクトからの呼び出し状況を
    追跡して判定結果を返します。

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        package_name: 調査するパッケージ名（例: "pandas"）
        python_path: site-packages パス（空の場合は現在の環境を自動検出）
        max_depth: import グラフの最大探索深度（デフォルト: 5）

    Returns:
        パッケージの詳細な使用分析と判定結果
    """
    return check_usage(entry_point, package_name, python_path, max_depth)


@mcp.tool()
def generate(
    entry_point: str,
    package_name: str,
    python_path: str = "",
) -> dict:
    """stubbable パッケージの最小スタブコードを生成します。

    analyzer が特定した参照シンボルに基づき、import が通る最小限の
    ダミーモジュール（クラス定義 + 関数スタブ）を生成します。
    ファイルの書き出しは行わず、{パス: コード} の辞書を返します。

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        package_name: スタブ化するパッケージ名（例: "pandas"）
        python_path: site-packages パス（空の場合は現在の環境を自動検出）

    Returns:
        files: {相対パス: コード内容} の辞書
        referenced_symbols: 各モジュールで参照されるシンボル一覧
        stub_total_bytes: スタブの合計サイズ
    """
    return generate_stubs(entry_point, package_name, python_path)


def main() -> None:
    """MCP サーバーを起動する。"""
    mcp.run()


if __name__ == "__main__":
    main()
