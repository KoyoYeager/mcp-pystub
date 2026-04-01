"""スタブコード生成

analyzer の分析結果を元に、stubbable パッケージの最小スタブコードを生成する。
ファイル書き出しは行わない — {パス: コード} の辞書を返す。
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from src.import_graph import build_import_graph
from src.models import ImportGraph, SubmoduleStubHint
from src.module_resolver import ModuleResolver
from src.usage_analyzer import analyze_packages


def generate_stubs(
    entry_point: str,
    package_name: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """stubbable パッケージのスタブコードを生成する。

    Args:
        entry_point: プロジェクトのエントリーポイント
        package_name: スタブ化するパッケージ名
        python_path: site-packages パス

    Returns:
        {
            "package": str,
            "files": {relative_path: code_content},
            "referenced_symbols": {module: [symbols]},
            "original_size_files": int,
            "stub_size_bytes": int,
        }
    """
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return {"error": f"エントリーポイントが見つかりません: {entry_point}"}

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    # import グラフ構築
    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    # パッケージの実際のファイル位置を特定
    pkg_root = _find_package_root(package_name, resolver)
    if not pkg_root:
        return {"error": f"パッケージ '{package_name}' が見つかりません"}

    # グラフ内でこのパッケージを参照しているモジュールを収集
    referenced_symbols = _collect_referenced_symbols(graph, package_name)

    # パッケージ内のモジュール構造を取得
    pkg_modules = _get_package_modules(graph, package_name)

    # スタブファイルを生成
    files = _generate_stub_files(
        package_name, pkg_root, pkg_modules, referenced_symbols
    )

    # サイズ計算
    stub_size = sum(len(code.encode("utf-8")) for code in files.values())
    original_files = _count_original_files(pkg_root)

    # ビルド手順を生成
    build_instructions = _generate_build_instructions(package_name, pkg_root)

    return {
        "package": package_name,
        "files": files,
        "referenced_symbols": {
            mod: syms for mod, syms in referenced_symbols.items()
        },
        "original_file_count": original_files,
        "stub_file_count": len(files),
        "stub_total_bytes": stub_size,
        "build_instructions": build_instructions,
    }


def _detect_project_root(entry: Path) -> Path:
    """プロジェクトルートを推定する。"""
    current = entry.parent
    for _ in range(20):
        if (current / "pyproject.toml").exists():
            return current
        if (current / "setup.py").exists():
            return current
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return entry.parent


def _find_package_root(package_name: str, resolver: ModuleResolver) -> Path | None:
    """パッケージの site-packages 内のルートディレクトリを取得。"""
    for sp in resolver._site_packages:
        pkg_dir = sp / package_name
        if pkg_dir.is_dir():
            return pkg_dir
        # 単一ファイルモジュール
        pkg_file = sp / f"{package_name}.py"
        if pkg_file.is_file():
            return pkg_file
    return None


def _collect_referenced_symbols(
    graph: ImportGraph, package_name: str
) -> dict[str, list[str]]:
    """グラフ内でこのパッケージから参照されているシンボルを収集する。

    Returns:
        {importing_module: [referenced_symbols]} — パッケージ外のモジュールが参照するシンボル
    """
    referenced: dict[str, list[str]] = defaultdict(list)

    for edge in graph.edges:
        target_node = graph.nodes.get(edge.to_module)
        if not target_node or target_node.top_level_package != package_name:
            continue

        from_node = graph.nodes.get(edge.from_module)
        if not from_node or from_node.top_level_package == package_name:
            continue  # パッケージ内部の import はスキップ

        # from X import Y, Z の場合
        imp = edge.import_info
        if imp.names_imported:
            for name in imp.names_imported:
                if name != "*" and name not in referenced[edge.to_module]:
                    referenced[edge.to_module] = referenced[edge.to_module] + [name]
        else:
            # import X の場合 — モジュール自体が参照される
            if edge.to_module not in referenced:
                referenced[edge.to_module] = []

    return dict(referenced)


def _get_package_modules(
    graph: ImportGraph, package_name: str
) -> dict[str, str | None]:
    """グラフ内のパッケージモジュール一覧を取得。

    Returns:
        {module_name: file_path}
    """
    modules: dict[str, str | None] = {}
    for name, node in graph.nodes.items():
        if node.top_level_package == package_name and node.classification == "third_party":
            modules[name] = node.file_path
    return modules


def _generate_stub_files(
    package_name: str,
    pkg_root: Path,
    pkg_modules: dict[str, str | None],
    referenced_symbols: dict[str, list[str]],
) -> dict[str, str]:
    """スタブファイルを生成する。

    Returns:
        {relative_path: code_content}
    """
    files: dict[str, str] = {}

    if pkg_root.is_file():
        # 単一ファイルモジュール
        symbols = _collect_all_symbols(referenced_symbols)
        code = _generate_stub_code(symbols, package_name)
        files[f"{package_name}.py"] = code
        return files

    # パッケージ — サブモジュール構造を構築
    # まず、参照されているモジュールのスタブを生成
    modules_needing_stubs: set[str] = set()

    # 外部から参照されるモジュール
    for mod in referenced_symbols:
        modules_needing_stubs.add(mod)

    # パッケージ内で import されるモジュール（内部依存）
    for mod in pkg_modules:
        if mod.startswith(f"{package_name}.") or mod == package_name:
            modules_needing_stubs.add(mod)

    # 内部 import で参照されるシンボルを収集し、対象モジュールのスタブにも追加
    # canmatrix/__init__.py が from canmatrix.canmatrix import Frame する場合、
    # canmatrix/canmatrix.py のスタブにも Frame が必要
    for mod_name in list(modules_needing_stubs):
        file_path = pkg_modules.get(mod_name)
        if file_path and Path(file_path).is_file() and Path(file_path).suffix == ".py":
            _propagate_internal_symbols(
                file_path, package_name, referenced_symbols
            )

    # 各モジュールのスタブ生成
    for mod_name in sorted(modules_needing_stubs):
        rel_path = _module_to_path(mod_name, package_name, pkg_root)

        # このモジュールに必要なシンボル
        symbols = referenced_symbols.get(mod_name, [])

        # 実際のファイルがある場合、内部 import も解析してスタブに反映
        file_path = pkg_modules.get(mod_name)
        internal_imports = []
        if file_path and Path(file_path).is_file() and Path(file_path).suffix == ".py":
            internal_imports = _extract_internal_imports(
                file_path, package_name
            )

        code = _generate_module_stub(
            mod_name, package_name, symbols, internal_imports
        )
        files[rel_path] = code

    # __init__.py が無い中間ディレクトリに空の __init__.py を追加
    dirs_needing_init: set[str] = set()
    for path in list(files.keys()):
        parts = Path(path).parts
        for i in range(1, len(parts)):
            dir_path = "/".join(parts[:i])
            init_path = f"{dir_path}/__init__.py"
            if init_path not in files:
                dirs_needing_init.add(init_path)

    for init_path in sorted(dirs_needing_init):
        files[init_path] = '"""Auto-generated stub"""\n'

    # __version__ を元パッケージから取得してスタブに追加
    # matplotlib 等がバージョンチェックするため必要
    init_key = f"{package_name}/__init__.py"
    if init_key in files:
        version = _get_package_version(package_name, pkg_root)
        if version:
            files[init_key] = files[init_key] + f'\n__version__ = "{version}"\n'

    return files


def _module_to_path(
    module_name: str, package_name: str, pkg_root: Path
) -> str:
    """モジュール名をスタブ内の相対パスに変換。"""
    parts = module_name.split(".")

    # 元のファイルがパッケージ（__init__.py）かモジュール（.py）か確認
    if len(parts) == 1:
        return f"{package_name}/__init__.py"

    # サブモジュールのパス
    sub_parts = parts[1:]  # package_name 以降
    candidate = pkg_root / "/".join(sub_parts)

    if candidate.is_dir() and (candidate / "__init__.py").exists():
        return f"{package_name}/{'/'.join(sub_parts)}/__init__.py"
    else:
        return f"{package_name}/{'/'.join(sub_parts)}.py"


def _generate_stub_code(symbols: list[str], module_name: str) -> str:
    """シンボルリストからスタブコードを生成。"""
    lines = [f'"""Auto-generated stub for {module_name}"""', ""]

    if not symbols:
        return "\n".join(lines) + "\n"

    for sym in sorted(set(symbols)):
        if sym.startswith("_") and sym.endswith("_") and sym not in ("__all__", "__version__"):
            continue  # dunder は基本スキップ
        if sym[0].isupper():
            # 大文字始まり → クラスと推定
            lines = lines + [f"class {sym}: pass", ""]
        else:
            # 小文字始まり → 関数と推定
            lines = lines + [
                f"def {sym}(*args, **kwargs):",
                f'    raise RuntimeError("{module_name}.{sym} is not available in stub")',
                "",
            ]

    return "\n".join(lines) + "\n"


def _generate_module_stub(
    module_name: str,
    package_name: str,
    symbols: list[str],
    internal_imports: list[str],
) -> str:
    """個別モジュールのスタブコードを生成。"""
    lines = [f'"""Auto-generated stub for {module_name}"""', ""]

    # 内部 import を再現（パッケージ内のモジュール間依存）
    for imp in internal_imports:
        lines = lines + [imp]
    if internal_imports:
        lines = lines + [""]

    if not symbols:
        return "\n".join(lines) + "\n"

    for sym in sorted(set(symbols)):
        if sym == "*":
            continue
        if sym.startswith("_") and sym.endswith("_") and sym not in ("__all__", "__version__"):
            continue
        if sym[0].isupper():
            lines = lines + [f"class {sym}: pass", ""]
        else:
            lines = lines + [
                f"def {sym}(*args, **kwargs):",
                f'    raise RuntimeError("{module_name}.{sym} is not available in stub")',
                "",
            ]

    return "\n".join(lines) + "\n"


def _extract_internal_imports(file_path: str, package_name: str) -> list[str]:
    """ファイルからパッケージ内部の import 文を抽出する。

    スタブでも内部 import 構造を維持しないと AttributeError になるため。
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return []

    imports: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0

            # 相対 import（パッケージ内部）
            if level > 0:
                dots = "." * level
                names = ", ".join(
                    f"{a.name} as {a.asname}" if a.asname else a.name
                    for a in node.names
                )
                if module:
                    imports = imports + [f"try:\n    from {dots}{module} import {names}\nexcept ImportError:\n    pass"]
                else:
                    imports = imports + [f"try:\n    from {dots} import {names}\nexcept ImportError:\n    pass"]

            # 絶対 import でパッケージ内部のもの
            elif module.startswith(f"{package_name}."):
                names = ", ".join(
                    f"{a.name} as {a.asname}" if a.asname else a.name
                    for a in node.names
                )
                imports = imports + [f"try:\n    from {module} import {names}\nexcept ImportError:\n    pass"]

    return imports


def _propagate_internal_symbols(
    file_path: str, package_name: str,
    referenced_symbols: dict[str, list[str]],
) -> None:
    """内部 import で参照されるシンボルを対象モジュールの referenced_symbols に追加。

    canmatrix/__init__.py の `from canmatrix.canmatrix import Frame, Signal` を解析し、
    canmatrix.canmatrix の referenced_symbols に Frame, Signal を追加する。
    """
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except (OSError, SyntaxError):
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0

            # 相対 import or パッケージ内の絶対 import
            if level > 0 or module.startswith(f"{package_name}."):
                # 絶対モジュール名を算出
                if level > 0:
                    # 相対 import → 絶対名に変換
                    parts = Path(file_path).resolve().parts
                    for i, p in enumerate(parts):
                        if p == package_name:
                            base_parts = list(parts[i:])
                            break
                    else:
                        continue
                    # __init__.py の場合はディレクトリ名まで
                    if base_parts[-1] == "__init__.py":
                        base_parts = base_parts[:-1]
                    elif base_parts[-1].endswith(".py"):
                        base_parts[-1] = base_parts[-1][:-3]
                    # level 分上に戻る
                    base_parts = base_parts[:max(1, len(base_parts) - level + 1)]
                    if module:
                        target_module = ".".join(base_parts) + "." + module
                    else:
                        target_module = ".".join(base_parts)
                else:
                    target_module = module

                # シンボルを追加
                if node.names:
                    for alias in node.names:
                        name = alias.name
                        if name == "*":
                            continue
                        if target_module not in referenced_symbols:
                            referenced_symbols[target_module] = []
                        if name not in referenced_symbols[target_module]:
                            referenced_symbols[target_module] = (
                                referenced_symbols[target_module] + [name]
                            )


def _collect_all_symbols(
    referenced: dict[str, list[str]]
) -> list[str]:
    """全モジュールの参照シンボルを統合。"""
    all_syms: list[str] = []
    for syms in referenced.values():
        for s in syms:
            if s not in all_syms:
                all_syms = all_syms + [s]
    return all_syms


def _get_pip_name(package_name: str) -> str:
    """import 名から pip パッケージ名を推定する。"""
    try:
        import importlib.metadata
        # packages_distributions() で import名→pip名のマッピングを取得
        mapping = importlib.metadata.packages_distributions()
        if package_name in mapping:
            return mapping[package_name][0]
    except Exception:
        pass
    return package_name


def _get_package_version(package_name: str, pkg_root: Path) -> str | None:
    """元パッケージの __version__ を取得する。"""
    try:
        import importlib.metadata
        return importlib.metadata.version(package_name)
    except Exception:
        pass
    # __version__.py や _version.py を探す
    for vf in ["__version__.py", "_version.py"]:
        vpath = pkg_root / vf
        if vpath.is_file():
            try:
                source = vpath.read_text(encoding="utf-8", errors="replace")
                for line in source.split("\n"):
                    if "__version__" in line and "=" in line:
                        # __version__ = "1.2.3" のようなパターン
                        val = line.split("=", 1)[1].strip().strip("'\"")
                        return val
            except OSError:
                pass
    return None


def _count_original_files(pkg_root: Path) -> int:
    """元パッケージのファイル数を数える。"""
    if pkg_root.is_file():
        return 1
    count = 0
    try:
        for f in pkg_root.rglob("*"):
            if f.is_file():
                count += 1
    except OSError:
        pass
    return count


def _generate_build_instructions(package_name: str, pkg_root: Path) -> dict:
    """スタブ適用・復元のためのビルド手順を生成する。

    復元失敗を防ぐため、バックアップ・バージョン固定・検証ステップを含む。

    Returns:
        {
            "install_commands": [str],    # スタブ適用コマンド
            "uninstall_commands": [str],  # 元に戻すコマンド
            "backup_commands": [str],     # バックアップコマンド
            "verify_commands": [str],     # 復元検証コマンド
            "hook_disable": [str],        # PyInstaller フック無効化パス
            "notes": [str],
        }
    """
    pip_name = _get_pip_name(package_name)
    version = _get_package_version(package_name, pkg_root)

    # site-packages パスを取得するコマンド
    sp_cmd = 'python -c "import site; print(site.getsitepackages()[0])"'

    # バックアップ（復元失敗防止の最重要ステップ）
    backup_cmds = [
        f"# === 必ず最初にバックアップ ===",
        f"cp -r $({sp_cmd})/{package_name} $({sp_cmd})/{package_name}.bak",
    ]

    install_cmds = [
        f"pip uninstall -y {pip_name}",
        f"# スタブを site-packages にコピー:",
        f"cp -r _stubs/{package_name}/ $({sp_cmd})/{package_name}/",
    ]

    # 復元: バックアップ優先、フォールバックで pip
    uninstall_cmds = [
        f"# === 方法1: バックアップから復元（推奨・高速・確実） ===",
        f"rm -rf $({sp_cmd})/{package_name}",
        f"mv $({sp_cmd})/{package_name}.bak $({sp_cmd})/{package_name}",
        f"",
        f"# === 方法2: pip で再インストール（バックアップ消失時のフォールバック） ===",
    ]
    if version:
        uninstall_cmds = uninstall_cmds + [
            f"pip install --force-reinstall {pip_name}=={version}",
        ]
    else:
        uninstall_cmds = uninstall_cmds + [
            f"pip install --force-reinstall {pip_name}",
        ]

    # 復元検証
    verify_cmds = [
        f'python -c "import {package_name}; print(\'{package_name} OK\')"',
    ]
    if version:
        verify_cmds = verify_cmds + [
            f'python -c "import {package_name}; assert {package_name}.__version__ == \'{version}\', '
            f'f\'version mismatch: {{{package_name}.__version__}}\'"',
        ]

    # PyInstaller フック候補を検索
    hook_patterns: list[str] = []
    if pkg_root.parent:
        pyinstaller_hooks = pkg_root.parent / "PyInstaller" / "hooks"
        if pyinstaller_hooks.is_dir():
            for hook in pyinstaller_hooks.glob(f"hook-{package_name}*.py"):
                hook_patterns = hook_patterns + [str(hook.name)]

        for d in pkg_root.parent.glob(f"__pyinstaller_hooks_*{package_name}*"):
            hook_patterns = hook_patterns + [str(d.name)]

    hook_cmds: list[str] = []
    if hook_patterns:
        hook_cmds = [f"# リネームして無効化: {h} → {h}.disabled" for h in hook_patterns]
    else:
        hook_cmds = [f"# hook-{package_name}*.py があれば .disabled にリネーム"]

    notes = [
        "必ず backup_commands を最初に実行すること（復元失敗防止）",
        "ビルド後は uninstall_commands → verify_commands の順で復元・検証",
        "PyInstaller フックがスタブと衝突する場合は hook_disable でフック無効化",
    ]

    return {
        "backup_commands": backup_cmds,
        "install_commands": install_cmds,
        "uninstall_commands": uninstall_cmds,
        "verify_commands": verify_cmds,
        "hook_disable": hook_cmds,
        "notes": notes,
    }


def generate_submodule_stubs(
    entry_point: str,
    parent_package: str,
    submodule: str,
    python_path: str = "",
    max_depth: int = 10,
) -> dict:
    """サブモジュール単位のスタブを生成する。

    C拡張パッケージ（PySide6等）を直接スタブ化できない場合に、
    そのパッケージを import しているサブモジュール（asammdf.gui等）を
    スタブ化することで間接的に排除する。

    Args:
        entry_point: プロジェクトのエントリーポイント
        parent_package: サブモジュールが属するパッケージ (e.g., "asammdf")
        submodule: スタブ化するサブモジュール (e.g., "asammdf.gui")
        python_path: site-packages パス
        max_depth: import グラフの最大探索深度

    Returns:
        {
            "parent_package": str,
            "submodule": str,
            "files": {relative_path: code_content},
            "eliminated_packages": [str],
            "build_instructions": {...},
        }
    """
    entry = Path(entry_point).resolve()
    if not entry.is_file():
        return {"error": f"エントリーポイントが見つかりません: {entry_point}"}

    project_root = _detect_project_root(entry)
    resolver = ModuleResolver(
        project_root=str(project_root),
        python_path=python_path,
    )

    # import グラフ構築
    graph = build_import_graph(
        entry_point=str(entry),
        project_root=str(project_root),
        resolver=resolver,
        max_depth=max_depth,
    )

    # パッケージの実際のファイル位置を特定
    pkg_root = _find_package_root(parent_package, resolver)
    if not pkg_root or pkg_root.is_file():
        return {"error": f"パッケージ '{parent_package}' が見つかりません"}

    # サブモジュールのパスを特定
    submod_parts = submodule.split(".")
    if len(submod_parts) < 2 or submod_parts[0] != parent_package:
        return {"error": f"サブモジュール '{submodule}' は '{parent_package}' に属していません"}

    sub_rel = "/".join(submod_parts[1:])
    submod_dir = pkg_root / sub_rel
    submod_file = pkg_root / f"{sub_rel}.py"

    if submod_dir.is_dir():
        submod_path = submod_dir
        is_package = True
    elif submod_file.is_file():
        submod_path = submod_file
        is_package = False
    else:
        return {"error": f"サブモジュール '{submodule}' のファイルが見つかりません"}

    # サブモジュールが import しているパッケージ（排除対象）を特定
    eliminated: list[str] = []
    for edge in graph.edges:
        if edge.from_module == submodule or edge.from_module.startswith(f"{submodule}."):
            target = graph.nodes.get(edge.to_module)
            if (
                target
                and target.classification == "third_party"
                and target.top_level_package != parent_package
            ):
                if target.top_level_package not in eliminated:
                    eliminated = eliminated + [target.top_level_package]

    # 親パッケージがサブモジュールから import するシンボルを収集
    symbols_to_export: list[str] = []
    for edge in graph.edges:
        if edge.to_module == submodule or edge.to_module.startswith(f"{submodule}."):
            from_node = graph.nodes.get(edge.from_module)
            if (
                from_node
                and from_node.top_level_package == parent_package
                and edge.from_module != submodule
                and not edge.from_module.startswith(f"{submodule}.")
            ):
                for name in edge.import_info.names_imported:
                    if name not in symbols_to_export and name != "*":
                        symbols_to_export = symbols_to_export + [name]

    # スタブファイル生成
    files: dict[str, str] = {}

    if is_package:
        # __init__.py のスタブ
        init_code = _generate_stub_code(symbols_to_export, submodule)
        rel_init = f"{parent_package}/{sub_rel}/__init__.py"
        files[rel_init] = init_code
    else:
        code = _generate_stub_code(symbols_to_export, submodule)
        files[f"{parent_package}/{sub_rel}.py"] = code

    # サイズ計算
    original_size = 0
    if submod_path.is_dir():
        for f in submod_path.rglob("*"):
            if f.is_file():
                try:
                    original_size += f.stat().st_size
                except OSError:
                    pass
    elif submod_path.is_file():
        try:
            original_size = submod_path.stat().st_size
        except OSError:
            pass

    stub_size = sum(len(code.encode("utf-8")) for code in files.values())

    # ビルド手順
    build_instructions = _generate_submodule_build_instructions(
        parent_package, submodule, submod_path, pkg_root, resolver
    )

    return {
        "parent_package": parent_package,
        "submodule": submodule,
        "files": files,
        "eliminated_packages": sorted(eliminated),
        "original_size_bytes": original_size,
        "stub_size_bytes": stub_size,
        "symbols_exported": symbols_to_export,
        "build_instructions": build_instructions,
    }


def _generate_submodule_build_instructions(
    parent_package: str,
    submodule: str,
    submod_path: Path,
    pkg_root: Path,
    resolver: ModuleResolver,
) -> dict:
    """サブモジュールスタブの適用・復元手順を生成する。

    復元失敗を絶対に起こさないため:
    1. バックアップは必須（ディレクトリごとコピー）
    2. 復元はバックアップから mv（pip 不要で高速・確実）
    3. フォールバック: pip install --force-reinstall（バージョン固定）
    4. 検証コマンドで復元成功を確認
    """
    pip_name = _get_pip_name(parent_package)
    version = _get_package_version(parent_package, pkg_root)

    sp_cmd = 'python -c "import site; print(site.getsitepackages()[0])"'

    # サブモジュールの相対パス
    sub_rel = submodule.replace(".", "/")
    is_dir = submod_path.is_dir()

    if is_dir:
        target = f"$({sp_cmd})/{sub_rel}"
        backup_target = f"$({sp_cmd})/{sub_rel}.bak"
    else:
        target = f"$({sp_cmd})/{sub_rel}.py"
        backup_target = f"$({sp_cmd})/{sub_rel}.py.bak"

    backup_cmds = [
        f"# === 必ず最初にバックアップ（復元失敗防止） ===",
        f"cp -r {target} {backup_target}",
    ]

    install_cmds = [
        f"# サブモジュールをスタブに置換:",
    ]
    if is_dir:
        install_cmds = install_cmds + [
            f"rm -rf {target}",
            f"cp -r _stubs/{sub_rel}/ {target}/",
        ]
    else:
        install_cmds = install_cmds + [
            f"cp _stubs/{sub_rel}.py {target}",
        ]

    uninstall_cmds = [
        f"# === 方法1: バックアップから復元（推奨・高速・確実） ===",
    ]
    if is_dir:
        uninstall_cmds = uninstall_cmds + [
            f"rm -rf {target}",
            f"mv {backup_target} {target}",
        ]
    else:
        uninstall_cmds = uninstall_cmds + [
            f"mv {backup_target} {target}",
        ]

    uninstall_cmds = uninstall_cmds + [
        f"",
        f"# === 方法2: pip で親パッケージごと再インストール（バックアップ消失時） ===",
    ]
    if version:
        uninstall_cmds = uninstall_cmds + [
            f"pip install --force-reinstall {pip_name}=={version}",
        ]
    else:
        uninstall_cmds = uninstall_cmds + [
            f"pip install --force-reinstall {pip_name}",
        ]

    verify_cmds = [
        f'python -c "import {parent_package}; print(\'{parent_package} OK\')"',
        f'python -c "import {submodule}; print(\'{submodule} OK\')"',
    ]

    return {
        "backup_commands": backup_cmds,
        "install_commands": install_cmds,
        "uninstall_commands": uninstall_cmds,
        "verify_commands": verify_cmds,
        "notes": [
            "必ず backup_commands を最初に実行すること（復元失敗防止の最重要ステップ）",
            "バックアップからの復元は pip 不要で高速・確実",
            "ビルド後は uninstall_commands → verify_commands の順で復元・検証",
            f"親パッケージ ({parent_package}) の他の機能には影響しない",
        ],
    }
