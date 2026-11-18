# ツールと設定 / Tools and setup

[README に戻る](../README.ja.md) | インストール、MCP の設定例、各ツールの引数と出力例。 / Installation, MCP configuration examples, and tool inputs and outputs.

## インストール / Installation

```bash
pip install mcp-pystub
```

### 依存パッケージ / Dependencies

- `mcp>=1.0.0,<2` - Model Context Protocol SDK（2.x は FastMCP の API が変わったため未対応）
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

uvx で起動する場合（pip install 不要。uv が必要）:

```json
{
  "mcpServers": {
    "pystub": {
      "command": "uvx",
      "args": ["mcp-pystub"]
    }
  }
}
```

### ツール使用例 / Tool Examples

#### analyze

asammdf のシナリオの実際の出力の抜粋（入力のパスは例に置き換えています）:

```
入力 / Input:
  entry_point: "C:/work/myapp/main.py"
  python_path: "C:/work/myapp/venv/Lib/site-packages"

出力 / Output（抜粋）:
  {
    "summary": {"static_candidate_size_mb": 0, "conditional_high_savings_mb": 646.7, "conditional_high_count": 1,
                "recommendations": [...], "next_steps": [...], "note": "...", "closing_note": "...", "size_note": "..."},
    "stubbable": [],
    "required": [
      {"package_name": "PySide6", "estimated_size_mb": 646.7, "reason": "...", "submodule_stubs": []},
      ...
    ],
    "nofollow": [],
    "submodule_stub_hints": [],
    "conditional": [
      {"package_name": "pandas", "estimated_savings_mb": 37.0, "confidence": "low", "confidence_reason": "...",
       "conditions": [...],
       "how_to_check": "verify(..., packages=['pandas'], submodules=[], nofollow=[], run_entry=True, record=True)"}
    ],
    "conditional_submodule_hints": [
      {"submodule": "asammdf.gui", "target_package": "PySide6", "estimated_savings_mb": 646.7, "confidence": "high",
       "conditions": [{"entry": "asammdf.gui 以下を import・使用しない", "why_conditional": "スタブにするサブモジュール自身の中の入口"}, ...],
       "how_to_check": "verify(..., packages=[], submodules=['asammdf.gui=>PySide6'], nofollow=[], run_entry=True, record=True)"},
      {"submodule": "asammdf.gui", "target_package": "lxml", "estimated_savings_mb": 8.5, "confidence": "low", ...}
    ],
    "verify_status": "未実施",
    "analysis_time_ms": 18082
  }
```

サブモジュールのヒントは、確かめられる候補なら `submodule_stub_hints`、条件付きなら `conditional_submodule_hints` に出ます
（required の `submodule_stubs` は、この例のように空のことがあります）。同じパッケージが stubbable と conditional の両方に
載ることはありません。

#### verify

```
入力 / Input:
  entry_point, python_path, packages=["pandas"], submodules=["asammdf.gui=>PySide6"], nofollow=[],
  run_entry=True, timeout=120

出力 / Output（抜粋）:
  {"passed": {"packages": [...], "submodules": [...], "nofollow": [...]},
   "rejected": [{"item": "...", "reason": "...", "module": "...", "error": "..."}],
   "unresolved": [...],          # 原因を特定できなかった項目（passed には入れない）
   "entry_matched": true,        # run_entry=True のとき、エントリーの標準出力（バイト列の全体）と終了コードが元と一致したか
   "status_summary": {"確認済み（実行）": [...], "import 確認済み": [...], "未確認": [...]},
   "verified_sets": [...],       # どの組で確かめたか（そのまま適用する）
   "child_processes": 0, "untraced_child_processes": 0,
   "entry_conditions": {...},    # エントリーを起動したコマンド・作業フォルダ・普段との違い
   "note": "..."}
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
