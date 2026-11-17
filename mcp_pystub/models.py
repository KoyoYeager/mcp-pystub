"""データモデル定義

import 解析パイプラインで使用する全データ構造を定義する。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportInfo:
    """単一の import 文から抽出した情報"""

    module_name: str
    names_imported: list[str] = field(default_factory=list)
    alias: str | None = None
    is_module_level: bool = True
    is_protected: bool = False
    context: str = "module"
    line_number: int = 0
    import_type: str = "import"  # "import" | "from_import" | "star_import"
    relative_level: int = 0
    source_file: str = ""
    is_type_checking: bool = False  # if TYPE_CHECKING: 内（実行時には import されない）


@dataclass
class ModuleResolution:
    """モジュール名の解決結果"""

    module_name: str
    file_path: str | None = None
    classification: str = "unresolvable"  # stdlib | third_party | local | builtin | unresolvable
    top_level_package: str = ""


@dataclass
class ImportGraphNode:
    """import グラフの頂点"""

    module_name: str
    file_path: str | None = None
    classification: str = "unresolvable"
    depth: int = 0
    top_level_package: str = ""


@dataclass
class ImportGraphEdge:
    """import グラフの辺"""

    from_module: str
    to_module: str
    import_info: ImportInfo = field(default_factory=lambda: ImportInfo(module_name=""))


@dataclass
class ImportGraph:
    """import グラフ全体"""

    nodes: dict[str, ImportGraphNode] = field(default_factory=dict)
    edges: list[ImportGraphEdge] = field(default_factory=list)
    entry_point: str = ""
    project_root: str = ""
    warnings: list[str] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)  # 構文解析できなかったファイル
    truncated: list[str] = field(default_factory=list)  # max_depth で打ち切ったモジュール
    entry_module: str = ""


@dataclass
class GatewayFunction:
    """依存ライブラリ内で特定パッケージを実際に使う関数"""

    module: str = ""
    function_name: str = ""
    symbols_from_package: list[str] = field(default_factory=list)
    called_by_project: bool = False


@dataclass
class SubmoduleStubHint:
    """C拡張パッケージ等を間接排除するためのサブモジュールスタブ提案。

    例: PySide6(C拡張) ← asammdf.gui が import
        プロジェクトは asammdf.gui の機能を使用していない
        → asammdf/gui/ をスタブ化すれば PySide6 を排除可能
    """

    target_package: str = ""      # 排除したいパッケージ (e.g., "PySide6")
    parent_package: str = ""      # サブモジュールが属するパッケージ (e.g., "asammdf")
    submodule: str = ""           # スタブ化するサブモジュール (e.g., "asammdf.gui")
    submodule_path: str = ""      # ファイルパス
    imported_symbols: list[str] = field(default_factory=list)  # 親が re-export するシンボル
    estimated_savings_mb: float = 0.0
    reason: str = ""
    conditional: bool = False  # 条件付き（安全を示せない。conditions の機能を使わない前提）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0
    conditions_complete: bool = True  # False: 入口が多すぎて列挙を打ち切った（conditions_total は下限）
    confidence: str = ""  # high: 障害が全て依存ライブラリ内の過大近似で、プロジェクトに入口の使用が 0 件 / low


@dataclass
class PackageAnalysis:
    """単一パッケージの分析結果"""

    package_name: str = ""
    verdict: str = "required"  # "stubbable" | "nofollow" | "conditional" | "required"
    reason: str = ""
    is_directly_imported: bool = False
    is_transitively_imported: bool = False
    imported_by: list[str] = field(default_factory=list)
    import_depth: int = 0
    gateway_functions: list[GatewayFunction] = field(default_factory=list)
    estimated_size_mb: float = 0.0
    submodule_stubs: list[SubmoduleStubHint] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # conditional のとき: 「これを使うと壊れる」入口（仕様 9 節）
    conditions: list[dict] = field(default_factory=list)
    conditions_total: int = 0
    conditions_complete: bool = True  # False: 入口が多すぎて列挙を打ち切った（conditions_total は下限）
    unproven_because: list[str] = field(default_factory=list)
    confidence: str = ""
    conditional_submodule_stubs: list[SubmoduleStubHint] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """プロジェクト全体の分析結果"""

    entry_point: str = ""
    project_root: str = ""
    total_packages_analyzed: int = 0
    stubbable: list[PackageAnalysis] = field(default_factory=list)
    nofollow: list[PackageAnalysis] = field(default_factory=list)
    required: list[PackageAnalysis] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    analysis_time_ms: int = 0
