# mcp-pystub

Python exe ビルド（PyInstaller / Nuitka / cx_Freeze）時にスタブ置換可能なパッケージを自動検出し、最小スタブコードを生成する MCP サーバー。

---

An MCP server that auto-detects stubbable packages for Python exe builds (PyInstaller / Nuitka / cx_Freeze) and generates minimal stub code to reduce executable size.

## 背景 / Background

Python アプリを exe 化すると、依存ライブラリがモジュールレベルで import する重量パッケージが丸ごと同梱され、exe サイズが膨張する。実際のコードパスで使わないパッケージは最小ダミー（スタブ）で置換すれば **50-80% のサイズ削減** が可能。

When building Python apps into executables, heavy packages imported at module level by dependencies get bundled entirely, bloating the exe size. Replacing unused packages with minimal stubs can reduce size by **50-80%**.

### 実証データ / Benchmark

```
Rich CLI アプリの例:
  ベースライン: 11.75 MB (pygments 664 files + markdown_it 109 files)
  スタブ版:     10.23 MB (pygments 7 files + markdown_it 2 files)
  削減:          1.52 MB (13%)
  動作テスト:    ✓ PASS
```

## 機能 / Features

| ツール / Tool | 説明 / Description |
|---|---|
| `analyze` | import グラフを解析しスタブ候補を自動検出 / Auto-detect stubbable packages from import graph |
| `graph` | import グラフをノード・エッジで可視化 / Visualize import graph as nodes and edges |
| `check` | 特定パッケージの使用状況を詳細分析 / Deep analysis of a specific package's usage |
| `generate` | スタブの最小コードを生成（ファイル書出なし） / Generate minimal stub code (no file writes) |

### 判定結果 / Verdicts

| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | スタブ化可能。プロジェクトの実行パスで未使用 / Safe to stub. Not used in project's runtime path |
| **nofollow** | try/except 保護あり。`--nofollow-import-to` で除外推奨 / Protected import. Use `--nofollow-import-to` |
| **required** | スタブ化不可。実際に使用されている / Cannot stub. Actually used at runtime |

## 解析アルゴリズム / Analysis Algorithm

ライブラリ名のハードコードは一切なし。全て AST 解析による構造的自動判定。

No hardcoded library names. All detection is structural, based on AST analysis.

1. **Import 抽出**: `ast.parse()` で各ファイルの import 文を解析（module-level / function-level / try-except 保護を区別）
2. **Import グラフ構築**: エントリーポイントから BFS で依存関係を再帰解決（stdlib / third_party / local を自動分類）
3. **使用分析**: gateway 関数（依存ライブラリ内でパッケージを実際に使う関数）を特定し、プロジェクトコードからの呼び出しを追跡
4. **Required 伝播**: required パッケージの内部呼び出しチェーンを追跡し、間接的に必要なパッケージも required に格上げ

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
  entry_point: "C:/project/main.py"
  python_path: "C:/project/.venv/Lib/site-packages"

出力 / Output:
  {
    "stubbable": [
      {"package_name": "pygments", "reason": "依存ライブラリ経由でのみ import...", ...}
    ],
    "required": [
      {"package_name": "click", "reason": "プロジェクトコードが直接 import し使用", ...}
    ],
    "nofollow": [],
    "analysis_time_ms": 649
  }
```

#### generate

```
入力 / Input:
  entry_point: "C:/project/main.py"
  package_name: "pygments"

出力 / Output:
  {
    "files": {
      "pygments/__init__.py": "\"\"\"Auto-generated stub\"\"\"\n",
      "pygments/token.py": "class Token: pass\nclass Comment: pass\n...",
      ...
    },
    "original_file_count": 664,
    "stub_file_count": 7,
    "stub_total_bytes": 983
  }
```

## パフォーマンス / Performance

| プロジェクト規模 | 解析時間 | ノード数 |
|-----------------|---------|---------|
| 軽量 (click) | 335ms | 73 |
| 中規模 (flask) | 1,743ms | 230 |
| 重量級 (pandas) | 4,820ms | 491 |
| 超重量級 (sympy) | 6,919ms | 681 |

92 ライブラリの大規模テストでクラッシュ 0 件。

## テスト / Testing

```bash
python -m pytest tests/ -v
```

67 テスト（ユニットテスト + 統合テスト + MCP ツールテスト）

## 制限事項 / Limitations

- **C 拡張パッケージ**（.pyd / .so）は AST 解析できないため検出不可
- **動的 import**（`importlib.import_module(variable)`）は静的解析不可（warnings で通知）
- **PyInstaller フック**: スタブ化時にカスタムフックの無効化が別途必要
- **保守的判定**: 名前衝突等で一部の stubbable パッケージが required と判定される場合がある（安全側）

## ライセンス / License

MIT
