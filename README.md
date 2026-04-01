<!-- mcp-name: io.github.KoyoYeager/pystub -->

# mcp-pystub

Python exe ビルド（PyInstaller / Nuitka / cx_Freeze）時にスタブ置換可能なパッケージを自動検出し、最小スタブコードを生成する MCP サーバー。

---

An MCP server that auto-detects stubbable packages for Python exe builds (PyInstaller / Nuitka / cx_Freeze) and generates minimal stub code to reduce executable size.

## 背景 / Background

Python アプリを exe 化すると、依存ライブラリがモジュールレベルで import する重量パッケージが丸ごと同梱され、exe サイズが膨張する。実際のコードパスで使わないパッケージは最小ダミー（スタブ）で置換すれば大幅なサイズ削減が可能。

When building Python apps into executables, heavy packages imported at module level by dependencies get bundled entirely, bloating the exe size. Replacing unused packages with minimal stubs can significantly reduce size.

### 実証データ / Verified Results

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

### 判定結果 / Verdicts

| 判定 / Verdict | 意味 / Meaning |
|---|---|
| **stubbable** | スタブ化可能。プロジェクトの実行パスで未使用 / Safe to stub. Not used in project's runtime path |
| **nofollow** | try/except 保護あり。`--nofollow-import-to` で除外推奨 / Protected import. Use `--nofollow-import-to` |
| **required** | スタブ化不可。実際に使用されている / Cannot stub. Actually used at runtime |

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
- **復元安全設計** *(v0.2 new)*: バックアップ + バージョン固定 pip + 検証コマンドの 3 重安全策
- **PyInstaller フック情報**: 無効化が必要なフックファイルを通知

## できないこと・制限事項 / Limitations

- **C 拡張パッケージ**（.pyd / .so）は直接スタブ化すると C 拡張が欠落するため required 判定。ただし `generate_submodule` で**間接排除が可能**（v0.2 で対応）
- **動的 import**（`importlib.import_module(変数)`）は静的解析で追跡不可（warnings で通知）
- **PyInstaller カスタムフック**: スタブと衝突する場合がありフックの手動無効化が必要
- **遅延初期化パターン**: `__init__` で直接呼ばず後のメソッドで使うパッケージは検出精度が下がる（安全側で required に判定）
- **pip 名と import 名の不一致**: `python-dateutil` → `dateutil` 等のマッピングは未対応
- **ランタイムの条件分岐**: `if sys.version < (3,11)` 内の import は静的解析で判定不可

### 安全性の設計方針 / Safety Policy

「stubbable と判定したが実は必要だった」は**絶対に起こさない設計**。判定に迷う場合は required（安全側）に倒す。逆方向の誤判定（本当は stubbable なのに required）は許容する。

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
    "submodule_stub_hints": [
      {"submodule": "asammdf.gui", "parent_package": "asammdf",
       "target_package": "PySide6", "imported_symbols": ["plot"]}
    ],
    "analysis_time_ms": 6478
  }
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
      "install_commands": ["pip uninstall -y pandas", "# cp stubs to site-packages"],
      "uninstall_commands": ["pip install pandas"],
      "hook_disable": ["# hook-pandas*.py → .disabled"]
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
      "backup_commands": ["cp -r .../asammdf/gui .../asammdf/gui.bak"],
      "install_commands": ["rm -rf .../asammdf/gui", "cp -r _stubs/asammdf/gui/ .../asammdf/gui/"],
      "uninstall_commands": ["mv .../asammdf/gui.bak .../asammdf/gui"],
      "verify_commands": ["python -c \"import asammdf; print('OK')\""]
    }
  }
```

## パフォーマンス / Performance

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
| ユニットテスト（9モジュール） | 91 | 全通過 |
| PyPI ライブラリ大規模テスト（requests, flask, pandas 等） | 84 | クラッシュ 0 |
| PyInstaller exe ビルド + 動作テスト | 22 | **全 PASS** |
| asammdf E2E（MDF作成→読取→リサンプリング） | 1 | **exe 40% 削減 + 正常動作** |
| 復元テスト（バックアップ → 復元 → 検証） | 4 | 全成功 |

## ライセンス / License

MIT
