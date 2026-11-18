# 変更履歴 / Changelog

[README に戻る](README.ja.md) | 各版の変更点。 / Changes in each release.

## 0.3.1

- テスト: tests/ の全件（373 件）が通過
- README: 先頭を、結果（exe が 252.0 MB → 38.9 MB）と使い方の GIF に作り直した。英語版（README.md）と日本語版（README.ja.md）に分け、
  詳しい説明は docs/ に移した。表の asammdf の行は、Claude Code に頼んで記録した値（252.0 → 38.9 MB）にした
- 修正: venv がプロジェクトのフォルダの中にある配置（`myapp/main.py` と `myapp/.venv/Lib/site-packages`）で generate / generate_submodule / verify が
  「python_path がプロジェクトと重なっています」と断られていたのを直した（名前が site-packages / dist-packages で、上に `pyvenv.cfg` がある場所に限る）
- 修正: 復元の「方法 2」と verify_commands が PATH の素の `python` を使っていたのを、対象環境の python のフルパスに直した。
  見つからない時は方法 2 を出さず notes に理由を書き、verify_commands は環境変数 `PYSTUB_PYTHON` が無ければ止まる形にした

## 0.3.0

- テスト: tests/ の全件（361 件）が通過
- 全てのスタブ（stubbable・サブモジュールのヒントを含む）を、使われたら原因を出して終了コード 70 で止まる形にそろえた
  （nofollow はビルドから外すだけ）
- 出力キーの改名（開発中の名前から）: summary の `proven_savings_mb` → `static_candidate_size_mb`、
  conditional の `unproven_because` → `conditional_because`、conditions の `why_unproven` → `why_conditional`
- パッケージ名を `src` から `mcp_pystub` に変えた（`python -m mcp_pystub.trace` など）
- 判定を 3 層に分けた: 静的解析（候補を出すだけ。保証ではない）・動的解析（`verify`、実行の記録）・実行時（止まるスタブ、終了コード 70）
- 追加: `verify` ツール（import だけの確認が既定。`run_entry=True`・`record=True`・`trace_files`・`entry_args`・`cwd`・`timeout`）、
  実行の記録（`python -m mcp_pystub.trace`）、条件付きの候補（`conditional` / `conditional_submodule_hints`、`confidence`）
- 出力キーの追加: `summary`・`conditional`・`conditional_submodule_hints`・`conditional_omitted_small`・`verify_status`・`next_steps`・
  `trace_conflicts`・`trace_candidates`（analyze）、`status_summary`・`statuses`・`verified_sets`・`baseline_failed`・`unresolved`・
  `child_processes`・`untraced_child_processes`・`startup_loaded`・`blocked_entry_open`・`excluded_from_import`・`entry_conditions`（verify）。
  既存のキー（`stubbable` / `nofollow` / `required` / `submodule_stub_hints`）の名前は変えていない
- 適用・復元: 元ファイルは項目ごとの `_pystub_bak/<項目名>/` へ移す（重ねた適用でも元ファイルを失わない）

## 0.2.0

- C 拡張パッケージの間接排除（サブモジュールのスタブ）
