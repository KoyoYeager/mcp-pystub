# 変更履歴 / Changelog

[README に戻る](README.ja.md) | 各版の変更点。 / Changes in each release.

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
