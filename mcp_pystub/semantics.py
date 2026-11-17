"""意味解析エンジン

- シンボル解決: `pkg.attr.attr` のような参照を、再エクスポート・サブモジュール・
  スター import を辿って定義元（クラス / 関数 / 定数 / サブモジュール / 不明）まで解決する。
- 関数単位の到達解析: エントリーポイントのモジュール本体から、import・呼び出し・
  コールバック（関数を値として渡す）・インスタンス化されたクラスのメソッドを辿る
  （Rapid Type Analysis 相当）。動的で追えないものは到達扱い（安全側）。
- イベント: 各コード単位（モジュール本体 / 関数）で、どのシンボルをどの文脈
  （呼び出し・値として渡す・演算・isinstance/注釈などの安全な参照）で使ったかを記録する。

判定（usage_analyzer）とスタブ生成（stub_generator）の両方がこの結果を使う。
"""

from __future__ import annotations

import ast
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

from mcp_pystub.import_extractor import (
    handlers_catch_import_error,
    is_type_checking_test,
    parse_file,
)
from mcp_pystub.module_resolver import ModuleResolver, is_extension_file

# 引数として渡しても実体が呼ばれない（型・属性の参照だけの）組み込み・typing 関数
_SAFE_ARG_CALLEES: frozenset[str] = frozenset({
    "isinstance", "issubclass", "hasattr", "getattr", "type", "id", "repr",
    "cast", "TypeVar", "NewType", "Union", "Optional", "overload",
    "get_type_hints", "TypeAlias", "ClassVar", "Generic", "Protocol",
})

# 適用しても関数を登録・保持しない（関数を外へ逃がさない）デコレータ名（末尾名で判定）
_TRANSPARENT_DECORATORS: frozenset[str] = frozenset({
    "property", "staticmethod", "classmethod", "abstractmethod", "abstractproperty",
    "cached_property", "overload", "wraps", "lru_cache", "cache", "total_ordering",
    "dataclass", "final", "override", "runtime_checkable", "contextmanager",
    "asynccontextmanager", "singledispatch", "singledispatchmethod", "setter",
    "getter", "deleter", "no_type_check", "deprecated", "unique", "dataclass_transform",
    "type_check_only",
})

_PROPERTY_DECORATORS: frozenset[str] = frozenset({
    "property", "cached_property", "setter", "getter", "deleter", "abstractproperty",
})


def under(module: str, prefix: str) -> bool:
    """module が prefix（パッケージ名またはサブモジュール名）配下か。"""
    return module == prefix or module.startswith(prefix + ".")


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class Binding:
    kind: str  # module | from | def | class | const | alias | unknown
    target: str = ""  # module 名（module / from）
    name: str = ""  # from で取り込む名前
    node: ast.AST | None = None
    qualname: str = ""


@dataclass
class ClassInfo:
    module: str
    qualname: str
    node: ast.ClassDef
    methods: dict[str, str] = field(default_factory=dict)  # name -> unit qualname
    properties: set[str] = field(default_factory=set)
    attrs: dict[str, Binding] = field(default_factory=dict)  # クラス本体の代入
    bases: list[ast.expr] = field(default_factory=list)


@dataclass
class ModuleInfo:
    name: str
    path: str | None
    is_package: bool = False
    tree: ast.Module | None = None
    opaque: bool = False  # 拡張モジュール / 構文エラー / 名前空間 — 中身が読めない
    is_extension: bool = False
    parse_error: str | None = None
    bindings: dict[str, Binding] = field(default_factory=dict)
    star_sources: list[str] = field(default_factory=list)
    has_getattr: bool = False
    future_annotations: bool = False
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    functions: dict[str, ast.AST] = field(default_factory=dict)  # qualname -> def node
    all_names: list[str] | None = None  # __all__（リテラルの場合）
    units_built: bool = False


@dataclass(frozen=True)
class Ref:
    """解決済みの参照。trace は辿ったモジュールと、そのモジュールでの属性列。"""

    kind: str  # module function method class classattr const unknown missing opaque instance derived
    module: str
    chain: tuple[str, ...]
    trace: tuple[tuple[str, tuple[str, ...]], ...]
    target: str = ""  # function/method/class の qualname（module 内）
    ext: tuple[str, ...] = ()  # extend で後から付いた属性列
    ext_from: int = -1  # ext が付き始めた時点の trace の長さ

    def entry_for(self, prefix: str) -> tuple[str, tuple[str, ...]] | None:
        """prefix 配下で最初に入ったモジュールと、そこからの属性列。"""
        found: tuple[str, tuple[str, ...]] | None = None
        found_idx = -1
        for i, (mod, chain) in enumerate(self.trace):
            if found is None:
                if under(mod, prefix):
                    found = (mod, chain)
                    found_idx = i
            elif mod == found[0]:
                # 同じモジュール内で属性を辿った（extend）分は長い方を採る
                if len(chain) > len(found[1]):
                    found = (mod, chain)
                    found_idx = i
            else:
                break
        if found is not None and self.ext and 0 <= found_idx < self.ext_from:
            # 入口より後に（別モジュールで）付いた属性も入口から見た属性列に含める
            found = (found[0], found[1] + self.ext)
        return found

    def touches(self, prefix: str) -> bool:
        return any(under(mod, prefix) for mod, _ in self.trace)


@dataclass
class Event:
    kind: str  # ref | import | method | attr | dynamic
    ctx: str = ""  # call | arg | store | return | operand | safe | alias | decorator
    ref: Ref | None = None
    name: str = ""
    lineno: int = 0
    modules: tuple[str, ...] = ()  # import: 読み込むモジュール
    from_base: str = ""  # import: from X import ... の X
    names: tuple[str, ...] = ()  # import: from で取り込む名前（* を含む）
    protected: bool = False


@dataclass
class Unit:
    module: str
    qualname: str  # "<module>" = モジュール本体
    node: ast.AST
    cls: str = ""  # メソッドならクラスの qualname
    events: list[Event] = field(default_factory=list)
    escaped_units: list[str] = field(default_factory=list)  # 未知のデコレータで登録されうる関数

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.qualname)


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def literal_source(node: ast.AST | None) -> str | None:
    """リテラル（定数・定数のタプル/リスト/辞書等）ならソース表記を返す。"""
    if node is None:
        return None
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    try:
        src = ast.unparse(node)
    except Exception:  # noqa: BLE001
        return None
    if len(src) > 20000:
        return None
    return src


def _decorator_last_name(dec: ast.expr) -> str:
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return ""


def _const_prefix(expr: ast.expr) -> str:
    """getattr の名前式の定数の接頭辞（f"nan{x}" / "do_" + x → "nan" / "do_"）。"""
    if isinstance(expr, ast.JoinedStr) and expr.values:
        first = expr.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left = expr.left
        if isinstance(left, ast.Constant) and isinstance(left.value, str):
            return left.value
        return _const_prefix(left)
    return ""


def _abs_from_base(module: str, is_package: bool, level: int, name: str | None) -> str:
    if level == 0:
        return name or ""
    parts = module.split(".") if module else []
    if not is_package:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)] if level - 1 <= len(parts) else []
    base = ".".join(parts)
    if name:
        base = f"{base}.{name}" if base else name
    return base


# ---------------------------------------------------------------------------
# Program: モジュールの読み込みとシンボル解決
# ---------------------------------------------------------------------------


class Program:
    def __init__(self, resolver: ModuleResolver) -> None:
        self.resolver = resolver
        self.modules: dict[str, ModuleInfo | None] = {}
        self.units: dict[tuple[str, str], Unit] = {}
        self.warnings: list[str] = []
        self._sub_cache: dict[tuple[str, str], str | None] = {}

    # --- 読み込み ---

    def register(self, name: str, path: str | None) -> None:
        if name in self.modules and self.modules[name] is not None:
            return
        self.modules[name] = self._load(name, path)

    def get(self, name: str) -> ModuleInfo | None:
        if name in self.modules:
            return self.modules[name]
        path = self.resolver.find_module_file(name)
        info = self._load(name, path) if path else None
        self.modules[name] = info
        return info

    def _load(self, name: str, path: str | None) -> ModuleInfo | None:
        if not path:
            return None
        p = Path(path)
        if p.is_dir():
            return ModuleInfo(name=name, path=str(p), is_package=True, opaque=True)
        is_pkg = p.name.split(".")[0] == "__init__"
        if is_extension_file(p):
            return ModuleInfo(name=name, path=str(p), is_package=is_pkg, opaque=True, is_extension=True)
        if not p.name.endswith((".py", ".pyw")):
            return ModuleInfo(name=name, path=str(p), is_package=is_pkg, opaque=True)
        tree, error = parse_file(str(p))
        info = ModuleInfo(name=name, path=str(p), is_package=is_pkg, tree=tree)
        if tree is None:
            info.opaque = True
            info.parse_error = error
            return info
        self._collect_module(info)
        return info

    def submodule(self, info: ModuleInfo, name: str) -> str | None:
        """info がパッケージで name がサブモジュールならその絶対名。"""
        if not info.is_package or not info.path:
            return None
        ck = (info.name, name)
        if ck in self._sub_cache:
            return self._sub_cache[ck]
        p = Path(info.path)
        pkg_dir = p if p.is_dir() else p.parent
        f = self.resolver._resolve_path(pkg_dir / name, allow_namespace=True)
        if not f:
            self._sub_cache[ck] = None
            return None
        sub = f"{info.name}.{name}"
        self._sub_cache[ck] = sub
        if sub not in self.modules or self.modules[sub] is None:
            self.modules[sub] = self._load(sub, f)
        return sub

    # --- モジュールレベルの束縛 ---

    def _collect_module(self, info: ModuleInfo) -> None:
        tree = info.tree
        assert tree is not None
        for stmt in tree.body:
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
                if any(a.name == "annotations" for a in stmt.names):
                    info.future_annotations = True
        self._collect_bindings(info, tree.body, info.bindings, "")
        all_b = info.bindings.get("__all__")
        if all_b and all_b.kind == "const" and all_b.node is not None:
            try:
                val = ast.literal_eval(all_b.node)
                if isinstance(val, (list, tuple)) and all(isinstance(x, str) for x in val):
                    info.all_names = list(val)
            except Exception:  # noqa: BLE001
                pass
        # 関数・クラスの一覧（qualname）
        self._collect_defs(info, tree.body, "", None)

    def _collect_bindings(
        self, info: ModuleInfo, body: list[ast.stmt], out: dict[str, Binding], qual_prefix: str,
        is_module: bool = True,
    ) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.asname:
                        out[alias.asname] = Binding("module", target=alias.name)
                    else:
                        top = alias.name.split(".")[0]
                        out[top] = Binding("module", target=top)
            elif isinstance(stmt, ast.ImportFrom):
                base = _abs_from_base(info.name, info.is_package, stmt.level or 0, stmt.module)
                for alias in stmt.names:
                    if alias.name == "*":
                        if is_module and base:
                            info.star_sources.append(base)
                        continue
                    out[alias.asname or alias.name] = Binding("from", target=base, name=alias.name)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[stmt.name] = Binding("def", node=stmt, qualname=qual_prefix + stmt.name)
                if is_module and stmt.name == "__getattr__":
                    info.has_getattr = True
            elif isinstance(stmt, ast.ClassDef):
                out[stmt.name] = Binding("class", node=stmt, qualname=qual_prefix + stmt.name)
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    self._bind_target(target, stmt.value, out)
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is not None:
                    self._bind_target(stmt.target, stmt.value, out)
            elif isinstance(stmt, ast.AugAssign):
                if isinstance(stmt.target, ast.Name):
                    out[stmt.target.id] = Binding("unknown")
            elif isinstance(stmt, ast.If):
                if is_type_checking_test(stmt.test):
                    self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module)
                    continue
                # 本体の束縛を優先（後に処理したものが勝つ）
                self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module)
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module)
            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                for h in stmt.handlers:
                    self._collect_bindings(info, h.body, out, qual_prefix, is_module)
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module)
                self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module)
                self._collect_bindings(info, stmt.finalbody, out, qual_prefix, is_module)
            elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module)
                orelse = getattr(stmt, "orelse", [])
                self._collect_bindings(info, orelse, out, qual_prefix, is_module)
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    for n in ast.walk(stmt.target):
                        if isinstance(n, ast.Name):
                            out[n.id] = Binding("unknown")
            elif isinstance(stmt, ast.Delete):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        out.pop(t.id, None)

    @staticmethod
    def _bind_target(target: ast.expr, value: ast.expr, out: dict[str, Binding]) -> None:
        if isinstance(target, ast.Name):
            lit = literal_source(value)
            if lit is not None:
                out[target.id] = Binding("const", node=value)
            elif isinstance(value, (ast.Name, ast.Attribute)):
                out[target.id] = Binding("alias", node=value)
            else:
                out[target.id] = Binding("unknown", node=value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                for n in ast.walk(elt):
                    if isinstance(n, ast.Name):
                        out[n.id] = Binding("unknown")

    def _collect_defs(
        self, info: ModuleInfo, body: list[ast.stmt], prefix: str, cls: ClassInfo | None
    ) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + stmt.name
                # 同名再定義（property.setter 等）は別キーにする
                key = qual
                n = 1
                while key in info.functions:
                    n += 1
                    key = f"{qual}#{n}"
                info.functions[key] = stmt
                if cls is not None:
                    # 複数定義（setter）も全て到達させるため最初のキーで代表
                    cls.methods.setdefault(stmt.name, key)
                    if key != qual:
                        cls.methods[f"{stmt.name}#{n}"] = key
                    if any(_decorator_last_name(d) in _PROPERTY_DECORATORS for d in stmt.decorator_list):
                        cls.properties.add(stmt.name)
                self._collect_defs(info, stmt.body, qual + ".<locals>.", None)
            elif isinstance(stmt, ast.ClassDef):
                qual = prefix + stmt.name
                ci = ClassInfo(module=info.name, qualname=qual, node=stmt, bases=list(stmt.bases))
                self._collect_bindings(info, stmt.body, ci.attrs, qual + ".", is_module=False)
                info.classes[qual] = ci
                self._collect_defs(info, stmt.body, qual + ".", ci)
            else:
                for field_name in ("body", "orelse", "finalbody", "handlers"):
                    sub = getattr(stmt, field_name, None)
                    if isinstance(sub, list):
                        nodes: list[ast.stmt] = []
                        for s in sub:
                            if isinstance(s, ast.ExceptHandler):
                                nodes.extend(s.body)
                            elif isinstance(s, ast.stmt):
                                nodes.append(s)
                        self._collect_defs(info, nodes, prefix, cls)
                if isinstance(stmt, ast.Match):
                    for case in stmt.cases:
                        self._collect_defs(info, case.body, prefix, cls)

    # --- 解決 ---

    def resolve(
        self, mod: str, chain: tuple[str, ...], trace: tuple = (), depth: int = 0
    ) -> Ref:
        trace = trace + ((mod, chain),)
        if depth > 40:
            return Ref("unknown", mod, chain, trace)
        if not chain:
            return Ref("module", mod, (), trace)
        info = self.get(mod)
        if info is None:
            return Ref("opaque", mod, chain, trace)
        head, rest = chain[0], chain[1:]
        if info.opaque:
            sub = self.submodule(info, head)
            if sub:
                return self.resolve(sub, rest, trace, depth + 1)
            return Ref("opaque", mod, chain, trace)
        b = info.bindings.get(head)
        if b is None:
            sub = self.submodule(info, head)
            if sub:
                return self.resolve(sub, rest, trace, depth + 1)
            for star in info.star_sources:
                if star == mod:
                    continue
                r = self.resolve(star, chain, trace, depth + 1)
                if r.kind != "missing":
                    return r
            if info.has_getattr:
                return Ref("unknown", mod, chain, trace)
            return Ref("missing", mod, chain, trace)
        if b.kind == "module":
            return self.resolve(b.target, rest, trace, depth + 1)
        if b.kind == "from":
            if b.target == mod:
                sub = self.submodule(info, b.name)
                if sub:
                    return self.resolve(sub, rest, trace, depth + 1)
                return Ref("unknown", mod, chain, trace)
            return self.resolve(b.target, (b.name,) + rest, trace, depth + 1)
        if b.kind == "def":
            if rest:
                return Ref("unknown", mod, chain, trace)
            return Ref("function", mod, chain, trace, target=b.qualname)
        if b.kind == "class":
            return self._resolve_class_attr(info, b.qualname, (head,), rest, trace)
        if b.kind == "const":
            return Ref("const" if not rest else "unknown", mod, chain, trace)
        if b.kind == "alias" and b.node is not None:
            base = self.ref_of_expr(info, b.node, None, depth + 1, trace)
            if base is None:
                return Ref("unknown", mod, chain, trace)
            return self.extend(base, rest, depth + 1)
        return Ref("unknown", mod, chain, trace)

    def _resolve_class_attr(
        self, info: ModuleInfo, cls_qual: str, chain: tuple[str, ...], rest: tuple[str, ...], trace: tuple
    ) -> Ref:
        if not rest:
            return Ref("class", info.name, chain, trace, target=cls_qual)
        ci = info.classes.get(cls_qual)
        attr = rest[0]
        if ci is not None:
            if attr in ci.methods:
                if len(rest) == 1:
                    return Ref("method", info.name, chain + rest, trace, target=ci.methods[attr])
                return Ref("unknown", info.name, chain + rest, trace)
            ab = ci.attrs.get(attr)
            if ab is not None:
                if ab.kind == "const" and len(rest) == 1:
                    return Ref("const", info.name, chain + rest, trace)
                if ab.kind == "class":
                    return self._resolve_class_attr(info, ab.qualname, chain + (attr,), rest[1:], trace)
        return Ref("classattr", info.name, chain + rest, trace, target=cls_qual)

    def extend(self, base: Ref, rest: tuple[str, ...], depth: int = 0) -> Ref:
        if not rest:
            return base
        r = self._extend(base, rest, depth)
        ext_from = base.ext_from if base.ext_from >= 0 else len(base.trace)
        return replace(r, ext=base.ext + rest, ext_from=ext_from)

    def _extend(self, base: Ref, rest: tuple[str, ...], depth: int = 0) -> Ref:
        if base.kind == "module":
            return self.resolve(base.module, rest, base.trace, depth + 1)
        if base.kind == "class":
            info = self.get(base.module)
            if info is not None and not info.opaque:
                return self._resolve_class_attr(info, base.target, base.chain, rest, base.trace)
            return Ref("classattr", base.module, base.chain + rest, base.trace, target=base.target)
        if base.kind == "instance" and len(rest) == 1:
            info = self.get(base.module)
            ci = info.classes.get(base.target) if info is not None else None
            if ci is not None and rest[0] in ci.methods:
                return Ref("method", base.module, base.chain + rest, base.trace, target=ci.methods[rest[0]])
        if base.kind in ("const", "constattr"):
            # リテラル定数のメソッド・属性（スタブも同じ値を持つ）
            return Ref("constattr", base.module, base.chain + rest, base.trace)
        kind = "opaque" if base.kind == "opaque" else "derived"
        return Ref(kind, base.module, base.chain + rest, base.trace, target=base.target)

    def ref_of_expr(
        self, info: ModuleInfo, expr: ast.AST, scope: dict[str, Ref | None] | None,
        depth: int = 0, trace: tuple = (),
    ) -> Ref | None:
        """式を参照に解決する。解決できなければ None。"""
        if depth > 40:
            return None
        if isinstance(expr, ast.Name):
            if scope is not None and expr.id in scope:
                return scope[expr.id]
            if expr.id not in info.bindings and not info.star_sources:
                return None
            r = self.resolve(info.name, (expr.id,), trace, depth + 1)
            if r.kind == "missing" and len(r.trace) <= 1 + len(trace):
                return None  # 組み込み名など
            return r
        if isinstance(expr, ast.Attribute):
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)
            if base is None:
                return None
            return self.extend(base, (expr.attr,), depth + 1)
        if isinstance(expr, ast.Call):
            base = self.ref_of_expr(info, expr.func, scope, depth + 1, trace)
            if base is None:
                return None
            ext_from = base.ext_from if base.ext_from >= 0 else len(base.trace)
            if base.kind == "class":
                return replace(base, kind="instance", ext=base.ext + ("()",), ext_from=ext_from)
            if base.kind == "constattr":
                return replace(base, ext=base.ext + ("()",), ext_from=ext_from)
            return replace(base, kind="derived", chain=base.chain + ("()",), ext=base.ext + ("()",), ext_from=ext_from)
        if isinstance(expr, ast.Subscript):
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)
            if base is None:
                return None
            if base.kind == "class":
                return base  # Generic[...] 等
            ext_from = base.ext_from if base.ext_from >= 0 else len(base.trace)
            return replace(base, kind="derived", chain=base.chain + ("[]",), ext=base.ext + ("[]",), ext_from=ext_from)
        return None

    # --- コード単位 ---

    def build_units(self, info: ModuleInfo) -> None:
        if info.units_built or info.opaque or info.tree is None:
            info.units_built = True
            return
        info.units_built = True
        walker = _UnitWalker(self, info)
        walker.run()


# ---------------------------------------------------------------------------
# コード単位のイベント収集
# ---------------------------------------------------------------------------


class _UnitWalker:
    def __init__(self, program: Program, info: ModuleInfo) -> None:
        self.p = program
        self.info = info
        self.qual_of: dict[int, str] = {id(n): q for q, n in info.functions.items()}
        self.class_qual_of: dict[int, str] = {id(ci.node): q for q, ci in info.classes.items()}

    def run(self) -> None:
        tree = self.info.tree
        assert tree is not None
        unit = Unit(self.info.name, "<module>", tree)
        self.p.units[unit.key] = unit
        self._walk_body(tree.body, unit, None, "", protected=False)
        # 関数単位
        for qual, node in self.info.functions.items():
            cls_qual = ""
            base_qual = qual.split("#")[0]
            if "." in base_qual:
                owner = base_qual.rsplit(".", 1)[0]
                if owner in self.info.classes:
                    cls_qual = owner
            u = Unit(self.info.name, qual, node, cls=cls_qual)
            self.p.units[u.key] = u
            scope = self._function_scope(node)
            assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            self._walk_body(node.body, u, scope, qual + ".<locals>.", protected=False)

    # --- スコープ ---

    def _function_scope(self, node: ast.AST) -> dict[str, Ref | None]:
        scope: dict[str, Ref | None] = {}
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        args = node.args
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            scope[a.arg] = None
        if args.vararg:
            scope[args.vararg.arg] = None
        if args.kwarg:
            scope[args.kwarg.arg] = None
        if isinstance(node, ast.Lambda):
            return scope
        globals_: set[str] = set()
        # 本体の束縛（入れ子の def/class の中は除く）
        pending_alias: list[tuple[str, ast.expr]] = []
        for n in self._iter_own_nodes(node.body):
            if isinstance(n, ast.Global):
                globals_.update(n.names)
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    if alias.asname:
                        scope[alias.asname] = self.p.resolve(alias.name, ()) if alias.name else None
                    else:
                        top = alias.name.split(".")[0]
                        scope[top] = self.p.resolve(top, ())
            elif isinstance(n, ast.ImportFrom):
                base = _abs_from_base(self.info.name, self.info.is_package, n.level or 0, n.module)
                for alias in n.names:
                    if alias.name == "*" or not base:
                        continue
                    scope[alias.asname or alias.name] = self.p.resolve(base, (alias.name,))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                scope[n.name] = None  # 入れ子定義は後で解決
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        if isinstance(n.value, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)):
                            pending_alias.append((t.id, n.value))
                        scope.setdefault(t.id, None)
                    else:
                        for x in ast.walk(t):
                            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                                scope.setdefault(x.id, None)
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                t = n.target
                if isinstance(t, ast.Name):
                    if isinstance(n, (ast.AnnAssign, ast.NamedExpr)) and n.value is not None and isinstance(
                        n.value, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)
                    ):
                        pending_alias.append((t.id, n.value))
                    scope.setdefault(t.id, None)
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                for x in ast.walk(n.target):
                    if isinstance(x, ast.Name):
                        scope.setdefault(x.id, None)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    if item.optional_vars is not None:
                        for x in ast.walk(item.optional_vars):
                            if isinstance(x, ast.Name):
                                scope.setdefault(x.id, None)
            elif isinstance(n, ast.ExceptHandler):
                if n.name:
                    scope.setdefault(n.name, None)
        for g in globals_:
            scope.pop(g, None)
        # エイリアス（フロー非依存: 解決できた最後のものを採用）
        for name, value in pending_alias:
            if name in globals_:
                continue
            r = self.p.ref_of_expr(self.info, value, scope)
            if r is not None:
                scope[name] = r
        return scope

    @staticmethod
    def _iter_own_nodes(body: list[ast.stmt]):
        stack: list[ast.AST] = list(reversed(body))
        while stack:
            n = stack.pop()
            yield n
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            stack.extend(reversed(list(ast.iter_child_nodes(n))))

    # --- 文 ---

    def _walk_body(
        self, body: list[ast.stmt], unit: Unit, scope: dict[str, Ref | None] | None,
        local_prefix: str, protected: bool,
    ) -> None:
        for stmt in body:
            self._walk_stmt(stmt, unit, scope, local_prefix, protected)

    def _walk_stmt(
        self, stmt: ast.stmt, unit: Unit, scope: dict[str, Ref | None] | None,
        local_prefix: str, protected: bool,
    ) -> None:
        ev = self._expr
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._walk_def(stmt, unit, scope, local_prefix)
        elif isinstance(stmt, ast.ClassDef):
            for d in stmt.decorator_list:
                ev(d, "decorator", unit, scope)
            for b in stmt.bases:
                ev(b, "base", unit, scope)
            for k in stmt.keywords:
                ev(k.value, "base", unit, scope)
            cls_prefix = (local_prefix + stmt.name + ".") if local_prefix else self._class_prefix(stmt)
            # クラス本体は定義時に実行される
            for s in stmt.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._walk_def(s, unit, scope, cls_prefix)
                else:
                    self._walk_stmt(s, unit, scope, cls_prefix, protected)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                parts = alias.name.split(".")
                mods = tuple(".".join(parts[: i + 1]) for i in range(len(parts)))
                unit.events.append(Event("import", modules=mods, lineno=stmt.lineno, protected=protected))
        elif isinstance(stmt, ast.ImportFrom):
            base = _abs_from_base(self.info.name, self.info.is_package, stmt.level or 0, stmt.module)
            if base and base != "__future__":
                parts = base.split(".")
                mods = [".".join(parts[: i + 1]) for i in range(len(parts))]
                names = tuple(a.name for a in stmt.names)
                unit.events.append(Event(
                    "import", modules=tuple(mods), from_base=base, names=names,
                    lineno=stmt.lineno, protected=protected,
                ))
        elif isinstance(stmt, ast.Expr):
            ev(stmt.value, "safe" if not isinstance(stmt.value, ast.Call) else "operand", unit, scope)
        elif isinstance(stmt, ast.Assign):
            simple = all(isinstance(t, ast.Name) for t in stmt.targets)
            ev(stmt.value, "alias" if simple else "store", unit, scope)
            for t in stmt.targets:
                self._target(t, unit, scope)
        elif isinstance(stmt, ast.AnnAssign):
            if not self.info.future_annotations and not (scope is not None):
                ev(stmt.annotation, "safe", unit, scope)
            if stmt.value is not None:
                ev(stmt.value, "alias" if isinstance(stmt.target, ast.Name) else "store", unit, scope)
            self._target(stmt.target, unit, scope)
        elif isinstance(stmt, ast.AugAssign):
            ev(stmt.value, "operand", unit, scope)
            ev(stmt.target, "operand", unit, scope)
        elif isinstance(stmt, (ast.Return,)):
            if stmt.value is not None:
                ev(stmt.value, "return", unit, scope)
        elif isinstance(stmt, ast.If):
            ev(stmt.test, "operand", unit, scope)
            if is_type_checking_test(stmt.test):
                self._walk_body(stmt.orelse, unit, scope, local_prefix, protected)
                return
            self._walk_body(stmt.body, unit, scope, local_prefix, protected)
            self._walk_body(stmt.orelse, unit, scope, local_prefix, protected)
        elif isinstance(stmt, (ast.While,)):
            ev(stmt.test, "operand", unit, scope)
            self._walk_body(stmt.body, unit, scope, local_prefix, protected)
            self._walk_body(stmt.orelse, unit, scope, local_prefix, protected)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            ev(stmt.iter, "operand", unit, scope)
            self._walk_body(stmt.body, unit, scope, local_prefix, protected)
            self._walk_body(stmt.orelse, unit, scope, local_prefix, protected)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                ev(item.context_expr, "operand", unit, scope)
            self._walk_body(stmt.body, unit, scope, local_prefix, protected)
        elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            prot = handlers_catch_import_error(stmt.handlers)  # type: ignore[arg-type]
            self._walk_body(stmt.body, unit, scope, local_prefix, protected or prot)
            for h in stmt.handlers:
                if h.type is not None:
                    ev(h.type, "safe", unit, scope)
                self._walk_body(h.body, unit, scope, local_prefix, protected)
            self._walk_body(stmt.orelse, unit, scope, local_prefix, protected)
            self._walk_body(stmt.finalbody, unit, scope, local_prefix, protected)
        elif isinstance(stmt, ast.Raise):
            if stmt.exc is not None:
                ev(stmt.exc, "raise", unit, scope)
            if stmt.cause is not None:
                ev(stmt.cause, "raise", unit, scope)
        elif isinstance(stmt, ast.Assert):
            ev(stmt.test, "operand", unit, scope)
            if stmt.msg is not None:
                ev(stmt.msg, "operand", unit, scope)
        elif isinstance(stmt, ast.Delete):
            for t in stmt.targets:
                self._target(t, unit, scope)
        elif isinstance(stmt, ast.Match):
            ev(stmt.subject, "operand", unit, scope)
            for case in stmt.cases:
                for n in ast.walk(case.pattern):
                    if isinstance(n, ast.MatchClass):
                        ev(n.cls, "safe", unit, scope)
                    elif isinstance(n, ast.MatchValue):
                        ev(n.value, "safe", unit, scope)
                if case.guard is not None:
                    ev(case.guard, "operand", unit, scope)
                self._walk_body(case.body, unit, scope, local_prefix, protected)
        # Pass / Break / Continue / Global / Nonlocal: 何もしない

    def _class_prefix(self, stmt: ast.ClassDef) -> str:
        qual = self.class_qual_of.get(id(stmt))
        return (qual + ".") if qual else (stmt.name + ".")

    def _walk_def(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, unit: Unit,
        scope: dict[str, Ref | None] | None, prefix: str,
    ) -> None:
        """入れ子の def: デコレータ・既定引数・注釈は定義時（このユニット）に評価される。"""
        escaped = False
        for d in node.decorator_list:
            self._expr(d, "decorator", unit, scope)
            name = _decorator_last_name(d)
            if name in _TRANSPARENT_DECORATORS:
                continue
            target = d.func if isinstance(d, ast.Call) else d
            r = self.p.ref_of_expr(self.info, target, scope)
            if r is None or r.kind not in ("function", "class", "method"):
                escaped = True  # app.route 等: 登録されて後で呼ばれうる
        args = node.args
        for dflt in list(args.defaults) + [x for x in args.kw_defaults if x is not None]:
            self._expr(dflt, "store", unit, scope)
        if not self.info.future_annotations:
            for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + [
                x for x in (args.vararg, args.kwarg) if x is not None
            ]:
                if a.annotation is not None:
                    self._expr(a.annotation, "safe", unit, scope)
            if node.returns is not None:
                self._expr(node.returns, "safe", unit, scope)
        if escaped:
            qual = self._find_qual(node, prefix)
            if qual:
                unit.escaped_units.append(qual)
        if scope is not None:
            qual = self._find_qual(node, prefix)
            if qual and node.name in scope and scope[node.name] is None:
                scope[node.name] = Ref("function", self.info.name, (node.name,), ((self.info.name, (node.name,)),), target=qual)

    def _find_qual(self, node: ast.AST, prefix: str) -> str:
        return self.qual_of.get(id(node), "")

    def _target(self, t: ast.expr, unit: Unit, scope: dict[str, Ref | None] | None) -> None:
        # 代入先 `P.options.x = 1` / `obj[k] = v` の基底は参照（属性アクセス）
        if isinstance(t, ast.Attribute):
            self._expr(t.value, "safe", unit, scope)
        elif isinstance(t, ast.Subscript):
            self._expr(t.value, "operand", unit, scope)
            self._expr(t.slice, "operand", unit, scope)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._target(e, unit, scope)
        elif isinstance(t, ast.Starred):
            self._target(t.value, unit, scope)

    # --- 式 ---

    def _record(self, unit: Unit, ref: Ref | None, ctx: str, node: ast.AST) -> None:
        if ref is None:
            return
        unit.events.append(Event("ref", ctx=ctx, ref=ref, lineno=getattr(node, "lineno", 0)))

    def _expr(self, e: ast.AST | None, ctx: str, unit: Unit, scope: dict[str, Ref | None] | None) -> None:
        if e is None:
            return
        ev = self._expr
        if isinstance(e, (ast.Name, ast.Attribute)):
            ref = self.p.ref_of_expr(self.info, e, scope)
            if ref is not None:
                self._record(unit, ref, ctx, e)
                # 属性列の根が呼び出し・添字なら、それ自体も評価される
                root = e
                while isinstance(root, ast.Attribute):
                    root = root.value
                if not isinstance(root, ast.Name):
                    ev(root, "operand", unit, scope)
                return
            if isinstance(e, ast.Attribute):
                # 受け手が解決できない属性: property / メソッドを名前で到達させる
                # 呼び出し・値として外へ渡す（bound method のコールバック）→ 同名メソッド
                # 単なる読み出し → 同名の property だけ
                unit.events.append(Event(
                    "method" if ctx in ("call", "decorator", "arg", "store", "return") else "attr",
                    name=e.attr, lineno=e.lineno,
                ))
                ev(e.value, "safe" if ctx not in ("call", "decorator") else "operand", unit, scope)
            return
        if isinstance(e, ast.Call):
            func = e.func
            fname = func.id if isinstance(func, ast.Name) and (scope is None or func.id not in scope) and func.id not in self.info.bindings else ""
            ev(func, "call" if ctx != "decorator" else "decorator", unit, scope)
            if fname in ("isinstance", "issubclass") and len(e.args) >= 2:
                ev(e.args[0], "operand", unit, scope)
                ev(e.args[1], "safe", unit, scope)
                for a in e.args[2:]:
                    ev(a, "arg", unit, scope)
            elif fname in ("getattr", "hasattr", "setattr", "delattr") and e.args:
                ev(e.args[0], "safe", unit, scope)
                if len(e.args) >= 2:
                    if not (isinstance(e.args[1], ast.Constant) and isinstance(e.args[1].value, str)):
                        base_ref = self.p.ref_of_expr(self.info, e.args[0], scope)
                        on_self = isinstance(e.args[0], ast.Name) and e.args[0].id in ("self", "cls")
                        unit.events.append(Event(
                            "dynamic", ref=base_ref, name=_const_prefix(e.args[1]), lineno=e.lineno,
                            ctx="self" if on_self else "",
                        ))
                        ev(e.args[1], "operand", unit, scope)
                    else:
                        attr = e.args[1].value
                        base_ref = self.p.ref_of_expr(self.info, e.args[0], scope)
                        if base_ref is not None:
                            r2 = self.p.extend(base_ref, (attr,))
                            self._record(unit, r2, "call" if ctx == "call" else ctx if fname == "getattr" else "safe", e)
                        else:
                            unit.events.append(Event("attr", name=attr, lineno=e.lineno))
                for a in e.args[2:]:
                    ev(a, "arg", unit, scope)
            elif fname in _SAFE_ARG_CALLEES:
                for a in e.args:
                    ev(a, "safe", unit, scope)
            elif fname in ("__import__",) or self._is_import_module(func, scope):
                if e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str):
                    name = e.args[0].value
                    parts = name.split(".")
                    mods = tuple(".".join(parts[: i + 1]) for i in range(len(parts)))
                    unit.events.append(Event("import", modules=mods, lineno=e.lineno))
                for a in e.args:
                    ev(a, "operand", unit, scope)
            else:
                for a in e.args:
                    ev(a, "arg", unit, scope)
            for k in e.keywords:
                ev(k.value, "arg", unit, scope)
            return
        if isinstance(e, ast.Lambda):
            for dflt in list(e.args.defaults) + [x for x in e.args.kw_defaults if x is not None]:
                ev(dflt, "store", unit, scope)
            lscope = dict(scope) if scope is not None else {}
            lscope.update(self._function_scope(e))
            ev(e.body, "return", unit, lscope)
            return
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            cscope = dict(scope) if scope is not None else {}
            for gen in e.generators:
                ev(gen.iter, "operand", unit, cscope)
                for x in ast.walk(gen.target):
                    if isinstance(x, ast.Name):
                        cscope[x.id] = None
                for cond in gen.ifs:
                    ev(cond, "operand", unit, cscope)
            if isinstance(e, ast.DictComp):
                ev(e.key, "store", unit, cscope)
                ev(e.value, "store", unit, cscope)
            else:
                ev(e.elt, "store", unit, cscope)
            return
        if isinstance(e, (ast.Tuple, ast.List, ast.Set)):
            inner = ctx if ctx in ("safe", "alias", "store", "return", "arg", "raise") else "store"
            for x in e.elts:
                ev(x, inner, unit, scope)
            return
        if isinstance(e, ast.Dict):
            for k in e.keys:
                ev(k, "store", unit, scope)
            for v in e.values:
                ev(v, "store", unit, scope)
            return
        if isinstance(e, ast.Starred):
            ev(e.value, ctx, unit, scope)
            return
        if isinstance(e, ast.Compare):
            ev(e.left, "compare", unit, scope)
            for c in e.comparators:
                ev(c, "compare", unit, scope)
            return
        if isinstance(e, ast.BinOp):
            ev(e.left, "operand", unit, scope)
            ev(e.right, "operand", unit, scope)
            return
        if isinstance(e, ast.Subscript):
            ref = self.p.ref_of_expr(self.info, e.value, scope)
            if ref is not None and ref.kind == "class":
                self._record(unit, ref, "safe", e.value)  # Generic[...]
            else:
                ev(e.value, "operand", unit, scope)
            ev(e.slice, "safe" if ctx == "safe" else "operand", unit, scope)
            return
        if isinstance(e, ast.NamedExpr):
            ev(e.value, "alias", unit, scope)
            return
        if isinstance(e, ast.IfExp):
            ev(e.test, "operand", unit, scope)
            ev(e.body, ctx, unit, scope)
            ev(e.orelse, ctx, unit, scope)
            return
        if isinstance(e, (ast.Await, ast.Yield, ast.YieldFrom)):
            ev(e.value, "return", unit, scope)
            return
        # BoolOp / UnaryOp / JoinedStr / FormattedValue / Slice など
        for child in ast.iter_child_nodes(e):
            if isinstance(child, ast.expr):
                ev(child, "operand", unit, scope)

    def _is_import_module(self, func: ast.expr, scope: dict[str, Ref | None] | None) -> bool:
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            return True
        if isinstance(func, ast.Name) and func.id == "import_module":
            return True
        return False


# ---------------------------------------------------------------------------
# 到達解析
# ---------------------------------------------------------------------------


@dataclass
class Reachability:
    reached: set[tuple[str, str]] = field(default_factory=set)
    loaded_modules: set[str] = field(default_factory=set)
    instantiated: set[tuple[str, str]] = field(default_factory=set)  # (module, class qual)
    called_names: set[str] = field(default_factory=set)
    attr_names: set[str] = field(default_factory=set)
    dynamic_packages: set[str] = field(default_factory=set)
    dynamic_modules: set[tuple[str, str]] = field(default_factory=set)  # (module, 名前の接頭辞)
    # 到達理由（説明用）: unit → (どこから, 何で)
    why: dict[tuple[str, str], tuple] = field(default_factory=dict)
    # モジュール → そのモジュールの到達コードが import するモジュール
    module_imports: dict[str, set[str]] = field(default_factory=dict)


# 名前で動的にメソッドを呼ぶ stdlib の基底（visit_* / do_* / test* 等）→ 全メソッド到達
_DYNAMIC_DISPATCH_BASES: frozenset[str] = frozenset({
    "NodeVisitor", "NodeTransformer", "Cmd", "BaseHTTPRequestHandler",
    "SimpleHTTPRequestHandler", "CGIHTTPRequestHandler", "TestCase", "IsolatedAsyncioTestCase",
    "SimpleXMLRPCServer", "SimpleXMLRPCRequestHandler", "SimpleXMLRPCDispatcher",
    "BaseRequestHandler", "StreamRequestHandler", "DatagramRequestHandler",
    "HTMLParser", "ContentHandler", "Dispatcher",
})

_BASE_NAMES_CACHE: dict[tuple[str, tuple[str, ...]], frozenset[str] | None] = {}


def _stdlib_class_names(module: str, chain: tuple[str, ...]) -> frozenset[str] | None:
    """stdlib / 組み込みのクラスのメソッド名（dir）。取れなければ None（= 全メソッド扱い）。"""
    key = (module, chain)
    if key in _BASE_NAMES_CACHE:
        return _BASE_NAMES_CACHE[key]
    result: frozenset[str] | None = None
    try:
        import builtins
        import importlib
        import sys as _sys

        top = module.split(".")[0]
        if module == "builtins" or top in getattr(_sys, "stdlib_module_names", ()):
            obj = builtins if module == "builtins" else importlib.import_module(module)
            for c in chain:
                obj = getattr(obj, c)
            if isinstance(obj, type):
                if obj.__name__ in _DYNAMIC_DISPATCH_BASES:
                    result = None
                else:
                    result = frozenset(dir(obj))
    except Exception:  # noqa: BLE001
        result = None
    _BASE_NAMES_CACHE[key] = result
    return result


def framework_hook_names(program: Program, ci: ClassInfo) -> frozenset[str] | None:
    """解析外の基底クラス（stdlib / 拡張 / 不明）が名前で呼びうるメソッド名。

    - 基底が全て解析対象のクラスか object なら空集合
    - stdlib / 組み込みの基底は、その基底が持つメソッド名（Thread.run 等の上書き）
    - 動的に名前で呼ぶ基底（NodeVisitor 等）・拡張モジュール・不明な基底は None（全メソッド）
    """
    info = program.get(ci.module)
    if info is None:
        return None
    names: set[str] = set()
    for b in ci.bases:
        r = program.ref_of_expr(info, b, None)
        if r is not None and r.kind == "class":
            continue
        if r is None:
            if isinstance(b, ast.Name):
                got = _stdlib_class_names("builtins", (b.id,))
            elif isinstance(b, ast.Attribute):
                return None
            else:
                return None
        elif r.kind == "opaque" and r.chain:
            got = _stdlib_class_names(r.module, r.chain)
        else:
            return None
        if got is None:
            return None
        names |= got
    return frozenset(names)


def compute_reachability(program: Program, entry_module: str) -> Reachability:
    """エントリーポイントのモジュール本体から到達可能なコード単位を求める。"""
    st = Reachability()
    work: deque[tuple[str, str]] = deque()

    current: list[tuple[tuple[str, str] | None, str]] = [(None, "entry")]

    def reach(key: tuple[str, str], reason: str = "") -> None:
        if key in st.reached:
            return
        if key not in program.units:
            return
        st.reached.add(key)
        st.why[key] = (current[0][0], reason or current[0][1])
        work.append(key)

    def load_module(name: str) -> None:
        if name in st.loaded_modules:
            return
        info = program.get(name)
        if info is None:
            return
        st.loaded_modules.add(name)
        # 親パッケージも実行される
        if "." in name:
            load_module(name.rsplit(".", 1)[0])
        program.build_units(info)
        reach((name, "<module>"))

    def class_info(module: str, qual: str) -> ClassInfo | None:
        info = program.get(module)
        return info.classes.get(qual) if info is not None else None

    # メソッド名 → 生成済みクラスのメソッドのユニット
    methods_by_name: dict[str, list[tuple[str, str]]] = {}
    # クラス → getattr(self, ...) の名前の接頭辞
    dyn_by_class: dict[tuple[str, str], set[str]] = {}
    mro_cache: dict[tuple[str, str], frozenset[tuple[str, str]]] = {}

    def mro_of(module: str, qual: str) -> frozenset[tuple[str, str]]:
        key = (module, qual)
        if key in mro_cache:
            return mro_cache[key]
        mro_cache[key] = frozenset({key})  # 循環防止
        out = {key}
        ci = class_info(module, qual)
        info = program.get(module)
        if ci is not None and info is not None:
            for b in ci.bases:
                r = program.ref_of_expr(info, b, None)
                if r is not None and r.kind == "class":
                    out |= mro_of(r.module, r.target)
        mro_cache[key] = frozenset(out)
        return mro_cache[key]

    def instantiate(module: str, qual: str) -> None:
        key = (module, qual)
        if key in st.instantiated:
            return
        st.instantiated.add(key)
        ci = class_info(module, qual)
        if ci is None:
            return
        for mname, unit_qual in ci.methods.items():
            methods_by_name.setdefault(mname.split("#")[0], []).append((module, unit_qual))
        info = program.get(module)
        hooks = framework_hook_names(program, ci) if info is not None else frozenset()
        dyn_prefixes = [pfx for (m, pfx) in st.dynamic_modules if m == module]
        for anc in mro_of(module, qual):
            dyn_prefixes.extend(dyn_by_class.get(anc, ()))
        all_methods = hooks is None
        for mname, unit_qual in ci.methods.items():
            base = mname.split("#")[0]
            if (
                all_methods
                or (base.startswith("__") and base.endswith("__"))
                or base in ci.properties
                or base in st.called_names
                or (base in st.attr_names and base in ci.properties)
                or (hooks is not None and base in hooks)
                or any(base.startswith(pfx) for pfx in dyn_prefixes)
            ):
                reach((module, unit_qual))
        # 基底クラスのメソッドも継承される
        if info is not None:
            for b in ci.bases:
                r = program.ref_of_expr(info, b, None)
                if r is not None and r.kind == "class":
                    load_module(r.module)
                    instantiate(r.module, r.target)

    def on_method_name(name: str, attr_only: bool) -> None:
        target = st.attr_names if attr_only else st.called_names
        if name in target:
            return
        target.add(name)
        for key in methods_by_name.get(name, ()):
            if attr_only:
                ci = class_info(key[0], key[1].rsplit(".", 1)[0].split("#")[0])
                if ci is None or name not in ci.properties:
                    continue
            reach(key)

    load_module(entry_module)

    while work:
        key = work.popleft()
        unit = program.units[key]
        current[0] = (key, "")
        imports = st.module_imports.setdefault(unit.module, set())
        for qual in unit.escaped_units:
            reach((unit.module, qual))
        for ev in unit.events:
            current[0] = (key, ev)  # type: ignore[assignment]
            if ev.kind == "import":
                for m in ev.modules:
                    imports.add(m)
                    load_module(m)
                if ev.from_base:
                    info = program.get(ev.from_base)
                    if info is not None:
                        for n in ev.names:
                            if n == "*":
                                continue
                            # サブモジュールが存在すれば読み込まれる（安全側: 同名の束縛があっても）
                            sub = program.submodule(info, n)
                            if sub:
                                imports.add(sub)
                                load_module(sub)
                continue
            if ev.kind == "method":
                on_method_name(ev.name, attr_only=False)
                continue
            if ev.kind == "attr":
                on_method_name(ev.name, attr_only=True)
                continue
            if ev.kind == "dynamic":
                prefix = ev.name
                if ev.ref is not None and ev.ref.kind == "module":
                    # getattr(module, name): そのモジュールの（接頭辞が合う）関数・クラス
                    info = program.get(ev.ref.module)
                    if info is not None:
                        program.build_units(info)
                        for q in info.functions:
                            if "." not in q and q.startswith(prefix):
                                reach((ev.ref.module, q))
                        for q in info.classes:
                            if "." not in q and q.startswith(prefix):
                                instantiate(ev.ref.module, q)
                elif ev.ctx == "self" and unit.cls:
                    # getattr(self, name): 囲むクラスとその（生成済みの）子クラスのメソッド
                    ckey = (unit.module, unit.cls)
                    pf = dyn_by_class.setdefault(ckey, set())
                    if prefix not in pf:
                        pf.add(prefix)
                        for inst in list(st.instantiated):
                            if ckey in mro_of(*inst):
                                ci = class_info(*inst)
                                if ci:
                                    for mname, uq in ci.methods.items():
                                        if mname.startswith(prefix):
                                            reach((inst[0], uq))
                else:
                    # getattr(obj, name): 同じモジュールの生成済みクラスと、囲むクラスのメソッド
                    dkey = (unit.module, prefix)
                    if dkey not in st.dynamic_modules:
                        st.dynamic_modules.add(dkey)
                        for module, qual in list(st.instantiated):
                            ci = class_info(module, qual)
                            if ci and (module == unit.module or qual == unit.cls):
                                for mname, uq in ci.methods.items():
                                    if mname.startswith(prefix):
                                        reach((module, uq))
                        if unit.cls:
                            ci = class_info(unit.module, unit.cls)
                            if ci:
                                for mname, uq in ci.methods.items():
                                    if mname.startswith(prefix):
                                        reach((unit.module, uq))
                continue
            ref = ev.ref
            if ref is None:
                continue
            # 参照先モジュールは（import 済みのはずなので）読み込む
            if ref.kind in ("function", "method", "class", "instance") and ref.module not in st.loaded_modules:
                info = program.get(ref.module)
                if info is not None:
                    program.build_units(info)
            if ref.kind in ("function", "method"):
                if ev.ctx in ("call", "decorator", "arg", "store", "return", "operand", "raise", "base"):
                    reach((ref.module, ref.target))
            elif ref.kind == "class":
                # 継承されるだけ（base）では生成されない — 子クラスの生成時に基底へ伝播する
                if ev.ctx in ("call", "decorator", "arg", "store", "return", "raise"):
                    instantiate(ref.module, ref.target)
            elif ref.kind == "instance":
                instantiate(ref.module, ref.target)
            elif ref.kind in ("derived", "classattr", "unknown", "opaque") and ev.ctx in ("call", "decorator"):
                # obj.method() — 末尾の名前でメソッドを到達させる
                if ref.chain:
                    last = ref.chain[-1]
                    if last not in ("()", "[]"):
                        on_method_name(last, attr_only=False)
    return st
