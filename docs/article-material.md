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
analyze  — 全体俯瞰（まずこれを呼ぶ）
graph  — グラフ構造の確認（デバッグ用）
check   — 1パッケージの深掘り
generate    — スタブ生成（判断後に呼ぶ）
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
  pytest (67 テスト)

外部依存: なし（解析エンジンは Python 標準ライブラリのみ）
```

---

## 8. ファイル構成

```
src/
├── server.py              # 4 MCP ツール定義
├── analyzer.py            # 解析オーケストレーター
├── models.py              # 10 dataclass
├── import_extractor.py    # AST import 抽出
├── import_graph.py        # BFS グラフ構築
├── module_resolver.py     # stdlib/third_party/local 分類
├── usage_analyzer.py      # gateway 関数分析 + required 伝播
├── size_estimator.py      # パッケージサイズ推定
└── stub_generator.py      # スタブコード生成
```

---

## 9. 今後の改善案

- **pip 名 → import 名マッピング**: `importlib.metadata` で自動解決
- **キャッシュ**: 同じ site-packages のグラフを再利用
- **Nuitka / cx_Freeze 対応**: PyInstaller 以外のビルドツール連携
- **スタブの自動適用 + 復元スクリプト**: install_stubs.py 相当の機能
- **CI 連携**: GitHub Actions でビルドサイズの自動監視
