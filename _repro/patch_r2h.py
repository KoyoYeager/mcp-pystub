"""round2 修正 h: analyze に verify 未実施の注記・submodule_stub_hints を常に返す・RecursionError を安全側に / server に verify。"""
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


patch("analyzer.py", [
    ('''    # Phase C+D: 使用分析 + 判定
    analyses = analyze_packages(graph)''',
     '''    # Phase C+D: 使用分析 + 判定
    try:
        analyses = analyze_packages(graph)
    except RecursionError:
        # 解析が深すぎて終わらない: 全て required（安全側）
        tops = sorted({n.top_level_package for n in graph.nodes.values()
                       if n.classification == "third_party" and n.top_level_package})
        analyses = [PackageAnalysis(package_name=t, verdict="required",
                                    reason="解析が深すぎて完了しませんでした（安全側判定）。") for t in tops]
        graph.warnings.append("解析が深すぎて完了しなかったため、全パッケージを required にしました")'''),
    ('''    if all_hints:
        result_dict["submodule_stub_hints"] = all_hints

    return result_dict''',
     '''    result_dict["submodule_stub_hints"] = all_hints
    result_dict["verify_status"] = "未実施"
    result_dict["next_steps"] = [
        "この結果は静的解析による候補です（実行の保証ではありません）。",
        "適用前に verify ツールで、スタブ（とサブモジュールのヒント・nofollow）に差し替えた状態の import を"
        "対象環境の python で確かめてください（verify は site-packages を書き換えません）。",
        "verify が通った組だけを generate / generate_submodule の手順で適用し、ビルド後に実行結果を元と比べてください。",
    ]

    return result_dict'''),
])

patch("server.py", [
    ('''def main() -> None:''',
     '''@mcp.tool()
def verify(
    entry_point: str,
    python_path: str = "",
    packages: list[str] | None = None,
    submodules: list[str] | None = None,
    nofollow: list[str] | None = None,
    run_entry: bool = False,
) -> dict:
    """スタブに差し替えた状態を、対象環境の python で実際に import して確かめます（任意実行）。

    静的解析（analyze）は候補を出すだけです。verify は生成したスタブを一時フォルダに置き、対象環境の
    python を子プロセスで起動して、import グラフ上の全モジュールを import します。site-packages は
    書き換えません。スタブ無しの基準と比べて新たに失敗したものの原因のスタブを外し、検証を通った組を返します。

    Args:
        entry_point: プロジェクトのエントリーポイント
        python_path: 対象環境の site-packages（空なら現在の環境）
        packages: 確かめるスタブ化パッケージ（省略時は analyze の stubbable）
        submodules: 確かめるサブモジュール（"asammdf.gui=>PySide6" の形で対象の C 拡張も指定。省略時は analyze のヒント）
        nofollow: 除外して確かめるパッケージ（省略時は analyze の nofollow）
        run_entry: True ならエントリー本体も実行して出力を元と比べる

    Returns:
        passed: 検証を通った組 / rejected: 外した項目と失敗の原文
    """
    from mcp_pystub.verifier import verify as _verify
    return _verify(entry_point, python_path, packages, submodules, nofollow, run_entry)


def main() -> None:'''),
])
print("patched")
