"""mcp-pystub — Python exe スタブ最適化 MCP サーバー

import グラフを AST 解析し、exe ビルド時にスタブ置換可能なパッケージを自動検出する。
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_pystub.analyzer import analyze, check_usage, inspect_graph
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs

mcp = FastMCP("pystub")


@mcp.tool()
def analyze(
    entry_point: str,
    python_path: str = "",
    max_depth: int = 10,
    include_small_conditional: bool = False,
    trace_files: list[str] | None = None,
) -> dict:
    """プロジェクトのエントリーポイントから import グラフを解析し、
    スタブ置換可能なパッケージを自動検出します。

    各パッケージは以下のいずれかに判定されます:
    - stubbable: スタブ化可能（プロジェクトの実行パスで未使用）
    - nofollow: try/except 保護あり（--nofollow-import-to で除外推奨）
    - required: スタブ化不可（実際に使用されている）

    stubbable / nofollow / submodule_stub_hints は、静的解析で使用が見つからなかった候補です（保証ではありません）。
    別のキー conditional / conditional_submodule_hints は「安全は示せないが、列挙した機能を使わないなら
    外せる」候補で、条件（壊れる入口）と確認方法（verify の呼び出し）が付きます。
    include_small_conditional=True で 1 MB 未満の条件付き候補も出します。

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        python_path: site-packages パス（空の場合は現在の環境を自動検出）
        max_depth: import グラフの最大探索深度（デフォルト: 10）
        trace_files: 実行の記録ファイル（利用者が python -m mcp_pystub.trace で作ったもの）。analyze は読むだけで
            プログラムを実行しない。記録で実行された候補は「記録と矛盾」(trace_conflicts) として推奨から外し、
            required だが記録で未実行のものは「記録からの候補」(trace_candidates) に出す

    Returns:
        stubbable / nofollow / required に分類されたパッケージ一覧
    """
    from mcp_pystub.analyzer import analyze as _analyze
    return _analyze(entry_point, python_path, max_depth, include_small_conditional, trace_files)


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


@mcp.tool()
def generate_submodule(
    entry_point: str,
    parent_package: str,
    submodule: str,
    python_path: str = "",
) -> dict:
    """C拡張パッケージを間接排除するためのサブモジュールスタブを生成します。

    PySide6 のような C拡張パッケージは直接スタブ化できませんが、
    そのパッケージを import しているサブモジュール（例: asammdf.gui）を
    スタブ化することで間接的に排除できます。

    analyze ツールの結果に含まれる submodule_stub_hints の情報を元に
    このツールを使用してください。

    復元失敗を防ぐため、バックアップ・バージョン固定・検証ステップを
    含むビルド手順を生成します。

    Args:
        entry_point: プロジェクトのエントリーポイントファイルパス
        parent_package: サブモジュールが属するパッケージ名（例: "asammdf"）
        submodule: スタブ化するサブモジュール（例: "asammdf.gui"）
        python_path: site-packages パス（空の場合は現在の環境を自動検出）

    Returns:
        files: {相対パス: コード内容} の辞書
        eliminated_packages: 排除されるパッケージ一覧
        build_instructions: バックアップ・適用・復元・検証の手順
    """
    return generate_submodule_stubs(
        entry_point, parent_package, submodule, python_path
    )


@mcp.tool()
def verify(
    entry_point: str,
    python_path: str = "",
    packages: list[str] | None = None,
    submodules: list[str] | None = None,
    nofollow: list[str] | None = None,
    run_entry: bool = False,
    timeout: float = 900.0,
    entry_args: list[str] | None = None,
    cwd: str = "",
    record: bool = False,
    trace_files: list[str] | None = None,
) -> dict:
    """スタブに差し替えた状態を、対象環境の python で実際に import して確かめます（任意実行）。

    警告: run_entry=True と実行の記録（record=True）は対象のプログラムを実際に実行します。ファイルの書き込み・削除、通信、機器の操作などの副作用が
    起きえます。AI エージェントは、利用者が明示的に求めた時だけ run_entry=True / record=True を指定してください。GUI や常駐型の
    プログラムは終了しないので timeout で打ち切られます。既定（run_entry=False）は import だけで、別の子プロセスでグラフ上のモジュールを import します（各モジュールの直下のコードは動きますが、エントリーのスクリプトは動かしません。エントリーを import するローカルのモジュールは対象から外し、import の途中でエントリーのファイルを開く操作は止めます。Python の `open` を通る読み込みは、別名（短いパス名・拡張パス形式・UNC・ハードリンク）でも、ファイルの同一性で見分けて止めます。OS のコピー API（`shutil.copy2` の CopyFile2 など）、ctypes、外部コマンド（`cmd /c type` 等）での読み込みと、それでコピーしたファイル経由の import（zipimport 等）は止められません）。run_entry=True のエントリーは、標準出力をファイルにつないだ普段の起動と同じ条件（主な違いは PYTHONPATH の先頭の起動用フォルダと環境変数 MCP_PYSTUB_BOOT の 2 つ。ほかの違いは README の表を参照: 標準入力は空、標準出力はファイル、import sitecustomize が成功する、元のパッケージのファイルがディスクに残る、など。子プロセスは監査イベントで分かる起動を親子の対応で確かめ、ctypes・COM / WMI 経由の起動は検出できません。MPLBACKEND などは付けないので、窓を開くエントリーは呼ぶ側の環境変数で指定してください）で動かします。timeout は子プロセス 1 回あたりで、打ち切った時と終わった後は孫を含むプロセスの木を止めます。結果の verified_sets（確かめた組）をそのまま適用してください。一部だけを適用するなら、その組で確かめ直してください。確認済み（実行）は、下の表の違いの範囲で、動かした入力について確かめた結果です。配布前に、ビルドした exe を普段のやり方で試してください。

    静的解析（analyze）は候補を出すだけです。verify は生成したスタブを一時フォルダに置き、対象環境の
    python を子プロセスで起動して、import グラフ上の全モジュールを import します。site-packages は
    書き換えません。スタブ無しの基準と比べて新たに失敗したものの原因のスタブを外し、検証を通った組を返します。

    Args:
        entry_point: プロジェクトのエントリーポイント
        python_path: 対象環境の site-packages（空なら現在の環境）
        packages: 確かめるスタブ化パッケージ（省略時は analyze の stubbable）
        submodules: 確かめるサブモジュール（"asammdf.gui=>PySide6" の形で対象の C 拡張も指定。省略時は analyze のヒント）
        nofollow: 除外して確かめるパッケージ（省略時は analyze の nofollow）
        run_entry: True ならエントリー本体も実行して出力を元と比べる（False では import だけを確かめる）
        timeout: 子プロセス 1 回あたりの制限時間（秒）
        entry_args: エントリーに渡す引数（sys.argv[1:]。既定は空）
        cwd: エントリーを実行する作業フォルダ（既定はエントリーのあるフォルダ）
        record: True かつ run_entry=True のとき、基準（スタブなし）のエントリー実行で実行の記録も取る（追加の実行はしない）
        trace_files: 利用者が python -m mcp_pystub.trace で作った記録ファイル（合算して各項目の観測の区分に使う）

    Returns:
        passed: 検証を通った組 / rejected: 外した項目と失敗の原文 / unresolved: 原因を特定できなかった項目
        （passed には入れない）/ entry_matched・entry_note: エントリーの出力の一致（確かめていない時はその旨）
    """
    from mcp_pystub.verifier import verify as _verify
    return _verify(entry_point, python_path, packages, submodules, nofollow, run_entry, timeout=timeout,
                   entry_args=entry_args, cwd=cwd, record=record, trace_files=trace_files)


def main() -> None:
    """MCP サーバーを起動する。"""
    mcp.run()


if __name__ == "__main__":
    main()
