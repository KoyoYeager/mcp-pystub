# 記事素材: Python exe スタブ最適化 MCP サーバー

## 記事の切り口候補

1. **「Python exe が 200MB → 40MB に。スタブ置換の自動化に挑んだ話」** — 技術深掘り系
2. **「PyInstaller の exe が重すぎる？ 使ってない依存を自動検出する MCP サーバーを作った」** — 課題解決系
3. **「MCP サーバーで Python の import グラフを解析してみた」** — MCP + AST 解析のテック記事

---

## 1. 問題提起（Why）

### Python exe 化の「肥大化問題」

- PyInstaller / Nuitka で exe 化すると、**使わないパッケージ**まで丸ごと同梱される
- 典型例: asammdf が pandas (~30MB), PySide6 (~100MB) を module-level import
  - プロジェクトは `mdf.get()` しか使わない → pandas の機能は不要
  - でも `--nofollow-import-to=pandas` にすると ImportError でクラッシュ
- 結果: 40MB で済むはずの exe が **200MB** に

### 既存の解決策と限界

| 手法 | 問題点 |
|------|--------|
| `--exclude-module` | 実行時 ImportError |
| `--nofollow-import-to` | try/except なし import でクラッシュ |
| Nuitka anti-bloat | Nuitka 専用、手動 YAML 設定、ライブラリ固定 |
| UPX 圧縮 | 根本解決ではない（実行時に展開される） |
| venv クリーン化 | 依存の依存は制御できない |

### スタブ置換という手法

- 本物のパッケージを**クラス定義だけの 1KB ダミー**に差し替える
- import が通りさえすれば、使わない機能はダミーで OK
- **200MB → 40MB** の実績あり（Mdf2CsvConverter での実例）

---

## 2. 競合調査結果

**調査日**: 2026-03-30

### 直接の競合はゼロ

| 既存ツール | やること | やらないこと |
|-----------|---------|-------------|
| Nuitka anti-bloat | YAML ルールで import 置換 | 自動検出なし、Nuitka 専用 |
| PyTrim (学術) | 未使用依存を requirements から削除 | スタブ生成なし、exe 最適化ではない |
| Vulture / Autoflake | 未使用 import 検出 | スタブ生成なし、ビルド連携なし |
| fake-module (PyPI) | テスト用モジュール差替 | exe 最適化目的ではない |

### MCP サーバーは完全に空白地帯

- Python packaging / exe 最適化領域に MCP サーバーは **1 つも存在しない**
- PulseMCP, FastMCP ディレクトリで確認済み

---

## 3. 技術的な工夫（How）

### ハードコードなしの自動判定

ライブラリ名を一切ハードコードせず、**import の構造だけ**で判定する。

```
判定ロジック:
1. AST で import 文を解析（module-level? try/except保護?）
2. BFS で import グラフ構築
3. gateway 関数を特定（依存ライブラリ内でパッケージを使う関数）
4. プロジェクトコードが gateway 関数を呼んでいるか追跡
5. required パッケージの内部呼び出しチェーンを伝播

→ stubbable / nofollow / required の3値判定
```

### try/except 保護の自動検出

```python
# これは nofollow（--nofollow-import-to で除外可能）
try:
    import optional_package
except ImportError:
    optional_package = None

# これは stubbable 候補（スタブが必要）
import heavy_package  # try/except なし
```

AST の `Try` ノードの handler を走査して `ImportError` / `ModuleNotFoundError` を自動検出。

### コンストラクタ名マッチング

```python
# httpx._transports.default.py
class HTTPTransport:
    def __init__(self):
        self.pool = httpcore.ConnectionPool()  # httpcore を使用

# httpx._client.py
transport = HTTPTransport()  # ← これが gateway 関数呼び出し
```

`__init__` 内の使用をクラス名 `HTTPTransport` でも登録することで、
コンストラクタ呼び出しと内部使用を正しくマッチング。

---

## 4. 実証データ

### PyInstaller ビルドテスト

```
Rich CLI アプリ（Console.print のみ使用）:
  ベースライン:  11.75 MB
    - pygments:     664 ファイル (~8MB)
    - markdown_it:  109 ファイル (~0.4MB)

  スタブ版:      10.23 MB
    - pygments:     7 ファイル (983 bytes)
    - markdown_it:  2 ファイル (135 bytes)

  削減:          1.52 MB (13%)
  動作テスト:    ✓ PASS（exe が正常に Hello, World! を出力）
```

### 92 ライブラリ大規模テスト

```
テスト対象:   92 PyPI ライブラリ（requests, flask, pandas, matplotlib 等）
クラッシュ:   0 件
全依存検出:   69/92 (75%)
自身を誤判定: 0 件

未検出の原因:
  名前不一致:      15 件 (python-dateutil → dateutil 等)
  条件付き import: 10 件 (try/except, バージョン分岐)
  C 拡張:          5 件 (.pyd ファイル、AST 解析不可)
  真のバグ:        0 件
```

### パフォーマンス

```
軽量 (click):     335ms / 73 ノード
中規模 (flask):   1,743ms / 230 ノード
重量級 (pandas):  4,820ms / 491 ノード

中央値:           ~700ms
92 ライブラリ合計: 66 秒（平均 718ms/pkg）
```

---

## 5. 学んだこと・限界

### PyInstaller フックとの戦い

- PyInstaller は独自の**カスタムフック**でパッケージ内部を走査する
- スタブに置き換えるとフックがクラッシュ（`sqlalchemy.dialects.__all__` 等）
- **対策**: スタブ化時にフックもリネームして無効化が必要

```python
# hook-sqlalchemy.py が内部属性にアクセス → スタブだと AttributeError
# 解決: フックファイルを .disabled にリネームしてビルド
```

### 保守的判定のトレードオフ

- 「stubbable と判定したのに実は必要」は**絶対に起こさない**設計
- 代償: 一部の本当に stubbable なパッケージが required と判定される
- 原因: 関数名の衝突（`create()`, `render()` 等の汎用名）
- **偽陽性ゼロ**を優先し、偽陰性は許容

### 静的解析の限界

```
検出不可:
  ├── C 拡張 (.pyd/.so) — ソースコードがない
  ├── 動的 import — importlib.import_module(変数)
  ├── 条件付き import — if sys.version < (3,11)
  └── exec/eval による import — 追跡不可能
```

---

## 6. MCP サーバーとしての設計判断

### なぜ MCP サーバーにしたのか

| 特徴 | MCP の利点 |
|------|----------|
| AI エージェントが使える | Claude Code / Desktop から直接呼び出し |
| 構造化された出力 | JSON で判定理由・チェーン・サイズを返す |
| ステートレス | 毎回新規解析、キャッシュ不要 |
| 依存最小 | mcp SDK のみ。解析は stdlib で完結 |

### ツール分割の設計

```
analyze            — 全体俯瞰（まずこれを呼ぶ）
graph              — グラフ構造の確認（デバッグ用）
check              — 1パッケージの深掘り
generate           — パッケージスタブ生成（判断後に呼ぶ）
generate_submodule — サブモジュールスタブ生成（C拡張の間接排除）
```

### スタブ生成をツールに含めた理由

- analyzer が「どのシンボルが参照されるか」を既に知っている
- その情報からスタブを生成するのが自然
- **ファイル書き出しはしない**（AI が Write ツールで保存 or ユーザーに提示）

---

## 7. 技術スタック

```
解析エンジン:
  ast          — Python AST 解析
  importlib    — モジュール解決・メタデータ
  pathlib      — ファイルシステム操作
  dataclasses  — データモデル

MCP フレームワーク:
  mcp >= 1.0.0 (FastMCP)

テスト:
  pytest (91 テスト)

外部依存: なし（解析エンジンは Python 標準ライブラリのみ）
```

---

## 8. ファイル構成

```
src/
├── server.py              # 5 MCP ツール定義 (analyze, graph, check, generate, generate_submodule)
├── analyzer.py            # 解析オーケストレーター
├── models.py              # 11 dataclass (SubmoduleStubHint 追加)
├── import_extractor.py    # AST import 抽出
├── import_graph.py        # BFS グラフ構築
├── module_resolver.py     # stdlib/third_party/local 分類
├── usage_analyzer.py      # gateway 関数分析 + required 伝播 + サブモジュール検出
├── size_estimator.py      # パッケージサイズ推定
└── stub_generator.py      # スタブコード生成 + サブモジュールスタブ生成
```

---

## 9. v0.2: サブモジュールスタブ — C拡張パッケージの間接排除

### 問題: C拡張パッケージは行き止まりだった

v0.1 では C拡張（.pyd/.so）を含むパッケージは即 `required` 判定。
PySide6 (523 MB), matplotlib (27 MB) 等は「スタブ化不可」で終わっていた。

しかし Mdf2CsvConverter の手動スタブでは、PySide6 自体ではなく
**asammdf/gui/ ディレクトリをスタブに置き換える** ことで PySide6 を排除していた。

### 解決: サブモジュールスタブの自動検出

C拡張パッケージを import しているサブモジュールを特定し、
プロジェクトがそのサブモジュールの機能を使用していなければ
「このサブモジュールをスタブ化すれば C拡張を排除できる」とヒントを出す。

**新ツール**: `generate_submodule` — サブモジュール単位のスタブ生成

### Mdf2CsvConverter での実証 (asammdf v8.7.2)

**手動スタブとの答え合わせ: 3/3 一致**

| 手動スタブ (_stubs/) | MCPサーバ検出 | 一致 |
|---|---|---|
| pandas/ | stubbable (自動検出) | OK |
| canmatrix/ | stubbable (4.0 MB) | OK |
| asammdf_gui/ (→PySide6排除) | 50 submodule hints | OK |

### Before/After: C拡張パッケージ

| パッケージ | サイズ | v0.1 (Before) | v0.2 (After) |
|---|---|---|---|
| PySide6 | 523.2 MB | required (行き止まり) | **50 hints** (asammdf.gui系 47件) |
| torch | 443.2 MB | required (行き止まり) | 1 hint |
| scipy | 109.9 MB | required (行き止まり) | 13 hints |
| matplotlib | 26.9 MB | required (行き止まり) | 23 hints |
| 他16件 | 55.0 MB | required (行き止まり) | 58 hints |
| **合計** | **1,158 MB** | **0 hints** | **145 hints** |

### E2E テスト: PyInstaller exe 化 + 動作確認

**テスト対象**: asammdf で MDF 作成→チャンネル読取→リサンプリング

| | サイズ | MDF操作 | PySide6 |
|---|---|---|---|
| スタブなし | **431 MB** | OK | ロード済み |
| asammdf.gui スタブ | **259 MB** | OK | **未ロード** |
| **削減** | **172 MB (40%)** | 影響なし | 排除成功 |

```
exe 実行結果 (app_real_stub.exe):
  MDF created: 1 groups
  Channel 'Sine10Hz': 1000 samples, min=-1.000, max=1.000
  Resampled 'Cosine5Hz': 100 samples
  asammdf.gui.plot: loaded (type=function)   ← スタブ
  PySide6 loaded: False                      ← 排除成功
  ALL TESTS PASSED
```

### 復元安全性

3重の安全策を実装:

1. **バックアップ必須**: `cp -r gui/ gui.bak/` (スタブ適用前)
2. **バックアップ復元 (方法1)**: `mv gui.bak/ gui/` (pip不要・高速)
3. **pip フォールバック (方法2)**: `pip install --force-reinstall asammdf==8.7.2`
4. **検証コマンド**: `python -c "import asammdf; print('OK')"`

E2E テストでは全4回の復元に成功（失敗ゼロ）。

### テスト結果サマリー

| テスト | 件数 | 結果 |
|---|---|---|
| ユニットテスト (pytest) | 91 | 全 PASS |
| import チェーン検証 | 3 (pandas, canmatrix, asammdf.gui) | 全 PASS |
| PyInstaller exe ビルド | 2 (simple, real MDF) | 全 PASS |
| exe 動作確認 | 2 | 全 PASS |
| 復元確認 | 4 | 全 PASS |

---

## 10. 今後の改善案

- **pip 名 → import 名マッピング**: `importlib.metadata` で自動解決
- **キャッシュ**: 同じ site-packages のグラフを再利用
- **Nuitka / cx_Freeze 対応**: PyInstaller 以外のビルドツール連携
- **スタブの自動適用 + 復元スクリプト**: install_stubs.py 相当の機能
- **CI 連携**: GitHub Actions でビルドサイズの自動監視
