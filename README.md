<!-- mcp-name: io.github.KoyoYeager/pystub -->

# mcp-pystub

Python exe ビルド（PyInstaller / Nuitka / cx_Freeze）時にスタブ置換可能なパッケージを自動検出し、最小スタブコードを生成する MCP サーバー。

---

An MCP server that auto-detects stubbable packages for Python exe builds (PyInstaller / Nuitka / cx_Freeze) and generates minimal stub code to reduce executable size.

## 背景 / Background

Python アプリを exe 化すると、依存ライブラリがモジュールレベルで import する重量パッケージが丸ごと同梱され、exe サイズが膨張する。実際のコードパスで使わないパッケージは最小ダミー（スタブ）で置換すれば大幅なサイズ削減が可能。

When building Python apps into executables, heavy packages imported at module level by dependencies get bundled entirely, bloating the exe size. Replacing unused packages with minimal stubs can significantly reduce size.

### 実証データ / Verified Results

> **再測定中**: 下の数値は判定を 2 段（使用が見つからなかった候補 / 条件付き）に分ける前のものです。差し替え予定です。

**asammdf プロジェクト — PyInstaller exe ビルド（E2E 動作確認済み）:**

| | exe サイズ | 動作 | PySide6 |
|---|---|---|---|
| スタブなし | 431 MB | OK | ロード済み |
| asammdf.gui スタブ適用 | **259 MB** | OK | **排除** |
| **削減** | **-40%（172 MB）** | 影響なし | |

**asammdf 変換ツール（analyze 結果）:**
```
stubbable:          pandas (59.7 MB), canmatrix (4.0 MB) — 合計 63.7 MB
submodule hints:    asammdf.gui → PySide6 (523 MB) 排除可能 — 50 hints 検出
```

> 手動でスタブ化していた 3 パッケージ（pandas, canmatrix, asammdf.gui）を**全て自動検出**

## 機能 / Features

| ツール / Tool | 説明 / Description |
|---|---|
| `analyze` | import グラフを解析しスタブ候補を自動検出。C拡張パッケージの間接排除ヒントも出力 / Auto-detect stubbable packages + submodule stub hints for C-extension elimination |
| `graph` | import グラフをノード・エッジで可視化 / Visualize import graph as nodes and edges |
| `check` | 特定パッケージの使用状況を詳細分析 / Deep analysis of a specific package's usage |
| `generate` | パッケージスタブの最小コードを生成 + ビルド手順出力 / Generate minimal stub code + build instructions |
| `generate_submodule` | **C拡張パッケージを間接排除するサブモジュールスタブを生成** / Generate submodule stubs to indirectly eliminate C-extension packages |
| `verify` | 生成スタブに差し替えた状態を、対象環境の python で実際に import して確かめ、通った組を返す（site-packages は書き換えない） / Dynamically verify proposals by importing the graph with stubs in the target interpreter |

> `analyze` の結果は静的解析による**候補**です。判定の仕様は [docs/STUB_SAFETY_SPEC.md](docs/STUB_SAFETY_SPEC.md)。
> 適用前に `verify` で確かめ、ビルド後に実行結果を元と比べてください。

### 判定結果 / Verdicts

判定は 2 段に分かれます（仕様: [docs/STUB_SAFETY_SPEC.md](docs/STUB_SAFETY_SPEC.md) の 1〜6 節と 9 節）。

**静的解析で使用が見つからなかった候補** — エントリーから到達しうるコードに、スタブで動作が変わる使用が見つからなかったもの（保証ではありません。実際に外せるかは下の 3 層の役割を参照）:

| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | スタブ化可能（全ての実行で元と同じ） / Proven safe to stub |
| **nofollow** | 単純に保護された import だけで参照も無い。除外可能 / Proven safe to exclude |
| `submodule_stub_hints` | C 拡張を import 閉包から外す、使用が見つからなかったサブモジュールの組 / Submodule stub candidates |
| **required** | スタブ化不可（確実に実行される使用がある、または区別できない） / Cannot stub |

**条件付き**（`conditional` / `conditional_submodule_hints`） — 安全は示せないが、列挙した機能を使わないなら外せる候補。
stubbable には混ざりません。各候補に、推定の削減量・条件（壊れる入口と、プロジェクトが呼ぶ公開 API からの経路）・
証明できなかった理由（名前だけの振り分け / 引数に渡したクラス / 登録デコレータ / 入力次第の分岐）・確認方法
（`verify(..., run_entry=True)` の呼び出し）と `confidence`（high / low）が付きます。

条件付きのスタブは、条件の機能に到達した時点（関数の呼び出し、値の代わりへの添字・真偽・書式・比較・反復・属性、
普通でないクラスの str / 反復など）で、標準エラーに `[mcp-pystub] PystubConditionalStubError: mcp-pystub のスタブです。…`
と原因を書いて **終了コード 70 で即終了**します（`except` で握りつぶせません）。標準エラーが無い窓のない exe では、
原因を exe のあるフォルダ（書けなければ一時フォルダ）の `pystub_stop.log` に書きます。`MCP_PYSTUB_STRICT=0` を付けた時だけ
`PystubConditionalStubError` を投げる動作になります（呼び出し元の `except Exception` 等で握りつぶされうる箇所は条件の
`may_be_swallowed` に出ます）。スタブのクラスそのものの `==`・hash・真偽・repr は元のクラスと同じ動作で、元に無い属性は
`AttributeError` になります。

**制限**: 子プロセス（multiprocessing・concurrent.futures・subprocess）の中で止まった場合、親が終了コードや例外として
受け取って処理を続けることがあります。即終了なので、書き込み途中のファイルが残り、`atexit` / `finally` は実行されません。
site-packages の `.pth` と `sitecustomize` が起動時に実行するコードは解析の外です。

**confidence**（条件付き候補ごと。仕様 10.2.1）:

| 値 | 意味 |
|---|---|
| `high` | プロジェクトから使用まで、**推定を挟まずに届く経路が 1 本も無い**（入口の列挙が完了し、受け手の分からない同名の呼び出しも無く、入口へ入る辺が登録・間接呼び出しの仕組みでない）という事実の表示。推定の経路（`estimated_routes`）がある時は「推定の経路があります。verify(run_entry=True) で確かめてください」と出す |
| `low` | 条件を守れば外せるが、プロジェクトが入口に届く公開関数を呼んでいる（直接でも、ほかの関数を経てでも）・経路が間接的。解析したエントリーでは変わらなくても、別の入力で終了コード 70 で止まりうる。理由は `confidence_reason`（例: 「プロジェクトが `asammdf.MDF.append()` を呼んでいます。入力によってはここから … pandas に届きます」） |

「推定」は、名前だけで結び付けた呼び出し（受け手の型が分からない `o.plot()` を同名のメソッド全てに結ぶ）、`__del__` のような
暗黙の呼び出し、動的な振り分け（getattr・登録辞書など）のことです。確定した経路は `route_from_project`、推定の経路は
`estimated_routes` に分けて出し、high の候補に確定した経路が出ることはありません。

low でも、解析したエントリーで変わらないことは `verify(..., run_entry=True)` で確かめられます。verify が比べるのは
エントリーの標準出力（実行中に `sys.stdout` へ書いたもの）と終了コード（エントリーと子プロセス全体。atexit の後も含む）だけで、
標準エラー・ファイルへの出力・import 時に掴まれた stdout への出力は比べません。エントリーは `sys.argv = [エントリー] + entry_args`、
作業フォルダ `cwd`（既定はエントリーのあるフォルダ）で実行するので、引数や入力ファイルが要るエントリーでは `entry_args` /
`cwd` を指定してください。基準（スタブなし）の実行が失敗した時は何も通さず `baseline_failed` に理由を返します。

`analyze` の結果の先頭には `summary`（使用が見つからなかった候補の推定削減 MB・conditional(high) の MB と件数・推奨の一覧・次の手順）が
付きます。1 MB 未満の条件付き候補は既定で省き（`conditional_omitted_small` に件数）、`include_small_conditional=True`
で全件を出します。推定サイズ（`estimated_size_mb` / `estimated_savings_mb`）は**インストール済みのファイルの大きさ**で、
exe の削減量ではありません（実際の削減は PyInstaller の同梱範囲と圧縮で変わります）。

## できること / What It Can Do

- **ライブラリ非固定の汎用検出**: AST 構造のみで判定。ハードコードなし
- **関数レベルの使用追跡**: `mdf.get()` は使うが `mdf.to_dataframe()` は使わない → pandas は stubbable
- **Call と参照の区別**: `isinstance(x, pd.DataFrame)` は stub-safe、`pd.DataFrame(data)` は実使用
- **クラス継承の検出**: `class User(BaseModel)` → pydantic は required
- **module-level 呼び出し検出**: import 時に実行されるコードを追跡
- **try/except 保護の自動検出**: 保護された import は `nofollow` と判定
- **スタブコード自動生成**: 参照シンボルのみの最小スタブ + ビルド手順（バックアップ・復元・検証）
- **C 拡張の自動検出**: .pyd / .so を含むパッケージは直接スタブ化不可と判定
- **C 拡張の間接排除** *(v0.2 new)*: C拡張パッケージを import しているサブモジュールが未使用なら、そのサブモジュールをスタブ化して C拡張を排除可能。PySide6 (523 MB) → asammdf.gui スタブで **exe 40% 削減**を実証
- **復元安全設計**: 元ファイルは site-packages の外の `_pystub_bak/<項目名>/` へ移動（pip uninstall しない）。
  項目ごとの領域なので、パッケージ全体とサブモジュールのスタブを重ねても元ファイルを失いません（内側が適用中なら
  外側の適用は断ります）。二重適用・中断にも耐え、復元後は verify_commands が `__file__` と RECORD のハッシュで確かめます
- **PyInstaller フック情報**: 無効化が必要なフックファイルを通知

## できないこと・制限事項 / Limitations

- **C 拡張パッケージ**（.pyd / .so）は直接スタブ化すると C 拡張が欠落するため required 判定。ただし `generate_submodule` で**間接排除が可能**（v0.2 で対応）
- **動的 import**（`importlib.import_module(変数)` / `__import__(変数)`）は静的解析で追跡不可。プロジェクトのコードに
  あれば安全側（required）、依存ライブラリの中なら warnings にモジュールと行を出し、判定の前提とします（仕様 5 節）
- **PyInstaller カスタムフック**: スタブと衝突する場合がありフックの手動無効化が必要
- **遅延初期化パターン**: `__init__` で直接呼ばず後のメソッドで使うパッケージは検出精度が下がる（安全側で required に判定）
- **pip 名と import 名の不一致**: `python-dateutil` → `dateutil` 等のマッピングは未対応
- **ランタイムの条件分岐**: `if sys.version_info < (3, 11)` 等の実行環境で決まる分岐は、両方の枝を「確実に実行される」
  側に倒します（安全側。削減を取りこぼすことがあります）

### 3 層の役割と保証 / Three layers

| 層 | 役割 | 保証 |
|---|---|---|
| 静的解析（`analyze`） | 候補を出し、サイズ順に並べ、理由（使われ方・条件・confidence）を説明する | 保証しない（提案） |
| 動的解析（`verify(run_entry=True)` と実行の記録） | エントリーを実際に動かし、候補が実行されたか（記録）と、スタブに差し替えても動作が変わらないか（verify）を確かめる | **動かした入力では動作が変わらない** |
| 実行時（止まるスタブ） | 動かしていない経路でスタブに触れたら、原因を出して終了コード 70 で止まる（条件付きのスタブ） | 黙って結果が変わらない（下の制限を除く） |

**確認の手順**: (1) `verify`（既定は import だけ。エントリー本体は動かさない。ただし各モジュールの直下のコード（import 時の
コード）は動き、エントリーの `if __name__ == "__main__":` の中は動かない）→ (2) スタブを適用してビルドし、普段のやり方で exe を
試す（スタブに触れたら原因を出して終了コード 70 で止まる）→（任意）エントリーを動かしても安全な時だけ `run_entry=True` や
実行の記録でより強く確かめる。

> **警告**: `run_entry=True` と実行の記録は、対象のプログラムを実際に実行します。ファイルの書き込み・削除、通信、機器の操作などの
> 副作用が起きえます。AI エージェントは、利用者が明示的に求めた時だけ指定してください。GUI や常駐型のプログラムは終了しないので
> timeout で打ち切られます。

### 安全性の設計方針 / Safety Policy

使用が見つからなかった候補（stubbable / nofollow / submodule_stub_hints）・条件付きとも、**静的解析による提案**です
（[仕様](docs/STUB_SAFETY_SPEC.md) 5 節の前提のもと）。**適用前に `verify(..., run_entry=True)` で、解析したエントリーが
変わらないことを確かめてください**。保証の最後の拠り所は verify と、ビルド後の実行結果の比較です。判定に迷う場合は
required（安全側）に倒し、逆方向の誤判定（本当は stubbable なのに required）は許容します。

条件付きは、条件を破ると原則として終了コード 70 で止まります。ただし、値の代わりに対する `isinstance` / `type()` /
`callable` / `dir()` / `vars()`、スタブのクラスの `issubclass` / `__mro__` / `dir()` / `vars()` / `dataclasses.is_dataclass`、外部の基底から
継承した属性では、止まる前に結果が元と変わりえます（Python の仕組み上スタブ側で止められない）。元に無い属性のエラー文も
元と違います。

## 解析アルゴリズム / Analysis Algorithm

1. **Import 抽出**: `ast.parse()` で各ファイルの import 文を解析（module-level / function-level / try-except 保護を区別）
2. **Import グラフ構築**: エントリーポイントから BFS で依存関係を再帰解決（stdlib / third_party / local を自動分類）
3. **使用分析**: gateway 関数（依存ライブラリ内でパッケージを**呼び出す**関数）を特定し、プロジェクトコードがそれを呼んでいるか追跡。名前参照のみ（isinstance, 型アノテーション）は stub-safe として除外
4. **Module-level 検出**: import 時にパッケージの関数が呼ばれる場合は required に格上げ
5. **サブモジュール間接排除** *(v0.2)*: C拡張パッケージを import しているサブモジュールを特定し、プロジェクトが直接 import していない + re-export シンボルを呼んでいない場合にスタブ化ヒントを出力

## インストール / Installation

```bash
pip install mcp-pystub
```

### 依存パッケージ / Dependencies

- `mcp>=1.0.0` - Model Context Protocol SDK
- 解析エンジンは Python 標準ライブラリのみ使用（ast, importlib, pathlib）

## 使い方 / Usage

### MCP サーバーとして起動 / Run as MCP server

```bash
mcp-pystub
```

### Claude Desktop / Claude Code 設定

```json
{
  "mcpServers": {
    "pystub": {
      "command": "mcp-pystub"
    }
  }
}
```

### ツール使用例 / Tool Examples

#### analyze

```
入力 / Input:
  entry_point: "C:/project/converter.py"
  python_path: "C:/project/.venv/Lib/site-packages"

出力 / Output:
  {
    "stubbable": [
      {"package_name": "pandas", "estimated_size_mb": 59.7, "reason": "依存ライブラリ経由でのみ import..."}
    ],
    "required": [
      {"package_name": "numpy", "reason": "プロジェクトコードが直接 import し使用"},
      {"package_name": "PySide6", "estimated_size_mb": 523.2,
       "reason": "C 拡張...ただし asammdf.gui をスタブ化することで間接排除が可能",
       "submodule_stubs": [{"submodule": "asammdf.gui", "target_package": "PySide6"}]}
    ],
    "nofollow": [
      {"package_name": "mpmath"}
    ],
    "submodule_stub_hints": [],
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

> 出力例の数値は再測定中です。

#### verify

```
入力 / Input:
  entry_point, python_path, packages=["pandas"], submodules=["asammdf.gui=>PySide6"], nofollow=[],
  run_entry=True, timeout=120

出力 / Output（抜粋）:
  {"passed": {"packages": [...], "submodules": [...], "nofollow": [...]},
   "rejected": [{"item": "...", "reason": "...", "module": "...", "error": "..."}],
   "unresolved": [...],          # 原因を特定できなかった項目（passed には入れない）
   "entry_matched": true,        # run_entry=True のとき、エントリーの出力が元と一致したか
   "note": "..."}                # run_entry=False では import だけを確かめ、実行は確かめていないこと
```

#### generate

```
入力 / Input:
  entry_point: "C:/project/converter.py"
  package_name: "pandas"

出力 / Output:
  {
    "files": {
      "pandas/__init__.py": "...",
      "pandas/core/api.py": "class DataFrame: pass\nclass Series: pass\n..."
    },
    "original_file_count": 2980,
    "stub_file_count": 266,
    "stub_total_bytes": 209530,
    "build_instructions": {
      "backup_commands": ["# 元ファイルを .../Lib/_pystub_bak/pandas/ へ移動", "..."],
      "install_commands": ["# _stubs/ のスタブを配置", "..."],
      "uninstall_commands": ["# 方法1: バックアップから復元", "...", "# 方法2: pip install --force-reinstall --no-deps pandas==<版>"],
      "verify_commands": ["python -c \"import pandas; ...__file__...\"", "# RECORD のハッシュ照合"],
      "hook_disable": ["# リネームして無効化: hook-pandas*.py → .disabled"]
    }
  }
```

#### generate_submodule *(v0.2 new)*

```
入力 / Input:
  entry_point: "C:/project/converter.py"
  parent_package: "asammdf"
  submodule: "asammdf.gui"

出力 / Output:
  {
    "parent_package": "asammdf",
    "submodule": "asammdf.gui",
    "files": {
      "asammdf/gui/__init__.py": "\"\"\"Auto-generated stub...\"\"\"\ndef plot(*args, **kwargs): ..."
    },
    "eliminated_packages": ["PySide6", "scipy", "lxml"],
    "original_size_bytes": 5907331,
    "stub_size_bytes": 144,
    "build_instructions": {
      "backup_commands": ["# .../asammdf/gui を .../Lib/_pystub_bak/asammdf.gui/ へ移動（親のスタブが適用中なら何もしない）"],
      "install_commands": ["# _stubs/asammdf/gui を配置"],
      "uninstall_commands": ["# バックアップから戻す（この項目の領域があるときだけ）"],
      "verify_commands": ["python -c \"import asammdf; ...__file__...\"", "# RECORD のハッシュ照合"]
    }
  }
```

## パフォーマンス / Performance

> **再測定中**: 下の表は判定を 2 段に分ける前（v0.2.0）の値です。

| プロジェクト規模 | 解析時間 | ノード数 |
|-----------------|---------|---------|
| 軽量 (click) | 335ms | 73 |
| 中規模 (flask) | 1,743ms | 230 |
| 重量級 (pandas) | 4,820ms | 491 |
| 超重量級 (sympy) | 6,919ms | 681 |

## テスト / Testing

```bash
python -m pytest tests/ -v
```

### テスト実績

| テスト種別 | 件数 | 結果 |
|-----------|------|------|
| ユニットテスト（tests/、攻撃シナリオの回帰を含む） | 305 | 全通過（第 6 回の時点） |
| PyPI ライブラリ大規模テスト（requests, flask, pandas 等） | 84 | クラッシュ 0 |
| PyInstaller exe ビルド + 動作テスト | 22 | **全 PASS** |
| asammdf E2E（MDF作成→読取→リサンプリング） | 1 | **exe 40% 削減 + 正常動作** |
| 復元テスト（バックアップ → 復元 → 検証） | 4 | 全成功 |

## ライセンス / License

MIT
