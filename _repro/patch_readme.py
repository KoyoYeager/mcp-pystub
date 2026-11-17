"""README を 2 段構え（証明済み / 条件付き）に合わせる。実測値の表には「再測定中」と注記するだけ。"""
from pathlib import Path

p = Path(__file__).resolve().parent.parent / "README.md"
s = p.read_text(encoding="utf-8")


def rep(a, b):
    global s
    if s.count(a) != 1:
        raise SystemExit("NOT FOUND: " + a[:60])
    s = s.replace(a, b, 1)


rep("""### 実証データ / Verified Results
""", """### 実証データ / Verified Results

> **再測定中**: 下の数値は判定を 2 段（証明済み / 条件付き）に分ける前のものです。差し替え予定です。
""")
rep("""| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | スタブ化可能。プロジェクトの実行パスで未使用 / Safe to stub. Not used in project's runtime path |
| **nofollow** | try/except 保護あり。`--nofollow-import-to` で除外推奨 / Protected import. Use `--nofollow-import-to` |
| **required** | スタブ化不可。実際に使用されている / Cannot stub. Actually used at runtime |
""", """判定は 2 段に分かれます（仕様: [docs/STUB_SAFETY_SPEC.md](docs/STUB_SAFETY_SPEC.md) の 1〜6 節と 9 節）。

**証明済み** — 「エントリーから到達しうる全ての実行で元と同じ」と静的に示せたものだけ:

| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | スタブ化可能（全ての実行で元と同じ） / Proven safe to stub |
| **nofollow** | 単純に保護された import だけで参照も無い。除外可能 / Proven safe to exclude |
| `submodule_stub_hints` | C 拡張を import 閉包から外す、証明済みのサブモジュールの組 / Proven submodule stubs |
| **required** | スタブ化不可（確実に実行される使用がある、または区別できない） / Cannot stub |

**条件付き**（`conditional` / `conditional_submodule_hints`） — 安全は示せないが、列挙した機能を使わないなら外せる候補。
stubbable には混ざりません。各候補に、推定の削減量・条件（壊れる入口と、プロジェクトが呼ぶ公開 API からの経路）・
証明できなかった理由（名前だけの振り分け / 引数に渡したクラス / 登録デコレータ / 入力次第の分岐）・確認方法
（`verify(..., run_entry=True)` の呼び出し）が付きます。条件付きのスタブは、条件の機能に到達すると
`PystubConditionalStubError: mcp-pystub のスタブです。…` で落ちます（黙って結果が変わりません）。
""")
rep("""    "submodule_stub_hints": [
      {"submodule": "asammdf.gui", "parent_package": "asammdf",
       "target_package": "PySide6", "imported_symbols": ["plot"]}
    ],
    "analysis_time_ms": 6478
  }
```""", """    "submodule_stub_hints": [],
    "conditional": [
      {"package_name": "pandas", "verdict": "conditional", "estimated_savings_mb": 44.6,
       "conditions": [{"entry": "asammdf.Signal.plot()", "why_unproven": "名前だけの振り分け ...",
                       "path": ["asammdf.Signal.plot()", "..."]}],
       "how_to_check": "verify('C:/project/converter.py', python_path=..., packages=['pandas'], ..., run_entry=True)"}
    ],
    "conditional_submodule_hints": [
      {"submodule": "asammdf.gui", "target_package": "PySide6", "estimated_savings_mb": 646.7,
       "conditional": true,
       "conditions": [{"entry": "asammdf.Signal.plot()", "public_api": "...", "why_unproven": "名前だけの振り分け ..."}],
       "how_to_check": "verify(..., submodules=['asammdf.gui=>PySide6'], run_entry=True)"}
    ],
    "verify_status": "未実施",
    "analysis_time_ms": 6478
  }
```

> 出力例の数値は再測定中です。""")
p.write_text(s, encoding="utf-8")
print("patched")
