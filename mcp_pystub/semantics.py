"""意味解析エンジン（docs/STUB_SAFETY_SPEC.md の 2〜5 節を実装する）

- シンボル解決: `pkg.attr.attr` のような参照を、再エクスポート・サブモジュール・
  スター import を辿って定義元まで解決する。同じ名前に束縛が複数ある・条件付きで
  束縛される場合は、全候補（alts）を返す（どれが実行されるか静的に決めない）。
- 関数単位の到達解析（4 節）: エントリーポイントと PyInstaller のフックが実行する
  モジュール本体（5 節）を起点に、import・呼び出し・コールバック・インスタンス化された
  クラスのメソッド・クラス族（cls() / __subclasses__ / __init_subclass__）・動的な呼び出しを辿る。
  到達した関数だけコード単位を作る（遅延構築）。
- イベント: 各コード単位で、どのシンボルをどの文脈で使ったかを記録する。文脈の
  分類（2 節の A/B/C）は usage_analyzer が「許可リスト方式」で行う。
"""

from __future__ import annotations

import ast
import builtins
import re
import sys
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

from mcp_pystub.import_extractor import (
    handlers_catch_import_error,
    is_type_checking_test,
    parse_file,
)
from mcp_pystub.module_resolver import ModuleResolver, is_extension_file

_STDLIB: frozenset[str] = frozenset(getattr(sys, "stdlib_module_names", ())) | {"builtins"}

# 適用しても関数を登録・保持しない stdlib のデコレータ名（末尾名で判定）
_TRANSPARENT_DECORATORS: frozenset[str] = frozenset({
    "property", "staticmethod", "classmethod", "abstractmethod", "abstractproperty",
    "cached_property", "overload", "wraps", "lru_cache", "cache", "total_ordering",
    "dataclass", "final", "override", "runtime_checkable", "contextmanager",
    "asynccontextmanager", "setter", "getter", "deleter", "no_type_check",
    "unique", "dataclass_transform", "type_check_only",
})

_PROPERTY_DECORATORS: frozenset[str] = frozenset({
    "property", "cached_property", "setter", "getter", "deleter", "abstractproperty",
})

# 型として isinstance 等に使われる組み込み（定数式の中に現れてよい名前）
_BUILTIN_TYPE_NAMES: frozenset[str] = frozenset(
    n for n in dir(builtins) if isinstance(getattr(builtins, n), type)
)

# C（実行）とみなさない文脈。それ以外は全て C（usage_analyzer.classify_event）
SAFE_CONTEXTS: frozenset[str] = frozenset({
    "bind", "annot", "read", "isinst", "except", "is", "eq", "hasattr", "presence",
})

# 到達解析で「関数・メソッドが外へ渡る／呼ばれる」とみなす文脈
_REACH_CTX: frozenset[str] = frozenset({
    "call", "decorator", "arg", "store", "return", "operand", "iter", "with",
    "subscript", "raise", "unknown", "del",
})


def under(module: str, prefix: str) -> bool:
    """module が prefix（パッケージ名またはサブモジュール名）配下か。"""
    return module == prefix or module.startswith(prefix + ".")


def is_external_module(name: str) -> bool:
    return name.split(".")[0] in _STDLIB


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class Binding:
    kind: str  # module | from | def | class | literal | alias | call | unknown
    target: str = ""  # module 名（module / from）
    name: str = ""  # from で取り込む名前
    node: ast.AST | None = None
    qualname: str = ""
    conditional: bool = False


@dataclass
class ClassInfo:
    module: str
    qualname: str
    node: ast.ClassDef
    methods: dict[str, list[str]] = field(default_factory=dict)  # name -> unit qualnames
    properties: set[str] = field(default_factory=set)
    attrs: dict[str, list[Binding]] = field(default_factory=dict)  # クラス本体の束縛
    bases: list[ast.expr] = field(default_factory=list)
    keywords: list[ast.keyword] = field(default_factory=list)


@dataclass
class ModuleInfo:
    name: str
    path: str | None
    is_package: bool = False
    tree: ast.Module | None = None
    opaque: bool = False  # 拡張モジュール / 構文エラー / 名前空間 — 中身が読めない
    is_extension: bool = False
    parse_error: str | None = None
    bindings: dict[str, list[Binding]] = field(default_factory=dict)
    star_sources: list[str] = field(default_factory=list)
    has_getattr: bool = False
    future_annotations: bool = False
    classes: dict[str, ClassInfo] = field(default_factory=dict)
    functions: dict[str, ast.AST] = field(default_factory=dict)  # qualname -> def node
    func_class: dict[str, str] = field(default_factory=dict)  # qualname -> 所属クラス qualname
    qual_of: dict[int, str] = field(default_factory=dict)  # id(def node) -> qualname
    class_qual_of: dict[int, str] = field(default_factory=dict)
    all_names: list[str] | None = None  # __all__（リテラルの場合）
    mutated: set[str] = field(default_factory=set)  # 変更されうる名前（global / 属性・添字への代入 / メソッド呼び出し）
    attr_stores: set[str] = field(default_factory=set)  # `x.NAME = ...` で書き込まれる属性名
    global_names: set[str] = field(default_factory=set)  # 関数内で global 宣言された名前
    mutations_done: bool = False
    unit_built: bool = False


@dataclass(frozen=True)
class Ref:
    """解決済みの参照。

    trace: 辿ったモジュールと、そのモジュールでの属性列
    marks: extend（属性の追加）の記録 (その時点の trace 長, 追加した属性列)
    alts: 束縛が複数ある場合の全候補
    """

    kind: str
    module: str
    chain: tuple[str, ...]
    trace: tuple[tuple[str, tuple[str, ...]], ...]
    target: str = ""  # function/method/class の qualname（module 内）
    marks: tuple[tuple[int, tuple[str, ...]], ...] = ()
    alts: tuple["Ref", ...] = ()

    def all_refs(self):
        yield self
        for a in self.alts:
            yield from a.all_refs()

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
                if len(chain) > len(found[1]):
                    found = (mod, chain)
                    found_idx = i
            else:
                break
        if found is not None:
            extra: tuple[str, ...] = ()
            for before, rest in self.marks:
                if before > found_idx:
                    extra = extra + rest
            if extra:
                found = (found[0], found[1] + extra)
            return found
        for a in self.alts:
            e = a.entry_for(prefix)
            if e is not None:
                return e
        return None

    def touches(self, prefix: str) -> bool:
        if any(under(mod, prefix) for mod, _ in self.trace):
            return True
        return any(a.touches(prefix) for a in self.alts)

    def touched_tops(self) -> set[str]:
        out = {m.split(".")[0] for m, _ in self.trace}
        for a in self.alts:
            out |= a.touched_tops()
        return out


@dataclass
class Event:
    kind: str  # ref | import | method | attr | dynamic | classdef | family | subclasses | entry_points | unanalyzable
    ctx: str = ""
    ref: Ref | None = None
    name: str = ""
    lineno: int = 0
    modules: tuple[str, ...] = ()  # import: 読み込むモジュール
    from_base: str = ""  # import: from X import ... の X
    names: tuple[str, ...] = ()  # import: from で取り込む名前（* を含む）
    protected: bool = False  # try/except ImportError の中
    protect_ok: bool = False  # 保護が「単純」（try 本体が import だけ・except が pass か None 代入だけ）
    guard: str = ""  # `if 'X' not in sys.modules: return` の後なら X
    target: str = ""  # classdef / family: クラス qualname


@dataclass
class Unit:
    module: str
    qualname: str  # "<module>" = モジュール本体
    node: ast.AST
    cls: str = ""  # メソッドならクラスの qualname
    events: list[Event] = field(default_factory=list)
    escaped_units: list[str] = field(default_factory=list)  # 登録されうる関数（未知・登録デコレータ）

    @property
    def key(self) -> tuple[str, str]:
        return (self.module, self.qualname)


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------

_FOLD_LIMIT = 20000


def _fold_ok(node: ast.AST, depth: int = 0) -> bool:
    """リテラル（と、リテラルだけの算術・組み込み型名のタプル）か。"""
    if depth > 50:
        return False
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_fold_ok(e, depth + 1) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _fold_ok(k, depth + 1) for k in node.keys) and all(
            _fold_ok(v, depth + 1) for v in node.values
        )
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not, ast.Invert)):
        return _fold_ok(node.operand, depth + 1)
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.BitOr, ast.BitAnd,
                  ast.BitXor, ast.LShift, ast.RShift)
    ):
        return _fold_ok(node.left, depth + 1) and _fold_ok(node.right, depth + 1)
    if isinstance(node, ast.Name) and node.id in _BUILTIN_TYPE_NAMES:
        return True
    return False


def literal_source(node: ast.AST | None) -> str | None:
    """定数として再現できる式ならソース表記を返す（評価して検算する）。"""
    if node is None or not _fold_ok(node):
        return None
    try:
        src = ast.unparse(node)
    except Exception:  # noqa: BLE001
        return None
    if len(src) > _FOLD_LIMIT:
        return None
    try:
        val = eval(compile(ast.Expression(body=node), "<const>", "eval"), {"__builtins__": builtins}, {})  # noqa: S307
    except Exception:  # noqa: BLE001
        return None
    try:
        if len(repr(val)) > _FOLD_LIMIT:
            return None
    except Exception:  # noqa: BLE001
        return None
    return src


def _is_mutable_literal(node: ast.AST | None) -> bool:
    return isinstance(node, (ast.List, ast.Dict, ast.Set))


def _decorator_last_name(dec: ast.expr) -> str:
    if isinstance(dec, ast.Call):
        dec = dec.func
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
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


def _const_prefix(expr: ast.expr) -> str:
    """名前式の定数の接頭辞（f"nan{x}" / "do_" + x → "nan" / "do_"）。"""
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


def _sys_modules_guard(stmt: ast.stmt) -> str:
    """`if 'X' not in sys.modules: return/raise` なら X。"""
    if not isinstance(stmt, ast.If) or stmt.orelse:
        return ""
    t = stmt.test
    if not (isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.NotIn)):
        return ""
    if not (isinstance(t.left, ast.Constant) and isinstance(t.left.value, str)):
        return ""
    c = t.comparators[0]
    if not (isinstance(c, ast.Attribute) and c.attr == "modules" and isinstance(c.value, ast.Name) and c.value.id == "sys"):
        return ""
    if len(stmt.body) == 1 and isinstance(stmt.body[0], (ast.Return, ast.Raise)):
        return t.left.value.split(".")[0]
    return ""


# ---------------------------------------------------------------------------
# デコレータが関数を外へ逃がすか（登録デコレータの検出）
# ---------------------------------------------------------------------------


def decorator_escapes(fn: ast.AST, depth: int = 0) -> bool:
    """デコレータ関数 fn（def）が引数の関数を保持・登録・その場で呼ぶか。

    透明（逃がさない）とみなすのは: 引数をそのまま返す / 属性を読む・書く /
    functools.wraps / update_wrapper に渡す / 入れ子のラッパー関数の中で呼ぶ だけの場合。
    デコレータ工場（引数を取って入れ子のデコレータを返す）はその入れ子を調べる。
    """
    if depth > 3 or not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return True
    params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    if not params:
        return True
    p = params[0]
    # デコレータ工場: 本体が入れ子の def を返すだけ
    returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
    nested = {n.name: n for n in fn.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    uses_p = any(isinstance(n, ast.Name) and n.id == p for n in ast.walk(fn))
    if not uses_p and nested and returns and all(
        isinstance(r.value, ast.Name) and r.value.id in nested for r in returns if r.value is not None
    ):
        return any(decorator_escapes(nested[r.value.id], depth + 1) for r in returns if isinstance(r.value, ast.Name))
    parent: dict[int, ast.AST] = {}
    for n in ast.walk(fn):
        for c in ast.iter_child_nodes(n):
            parent[id(c)] = n
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Name) and n.id == p and isinstance(n.ctx, ast.Load)):
            continue
        par = parent.get(id(n))
        if isinstance(par, ast.Return):
            continue
        if isinstance(par, ast.Attribute) and par.value is n:
            continue  # p.__name__ / p.__doc__ = ...
        if isinstance(par, ast.Call):
            fname = _decorator_last_name(par.func) if par.func is not n else ""
            if fname in ("wraps", "update_wrapper"):
                continue
            if par.func is n:
                # p(...) — 入れ子のラッパー内なら呼ばれるのはラッパー呼び出し時
                owner = par
                inside_nested = False
                while id(owner) in parent:
                    owner = parent[id(owner)]
                    if owner is fn:
                        break
                    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                        inside_nested = True
                        break
                if inside_nested:
                    continue
                return True
        return True
    return False


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
        self._plain_cache: dict[tuple[str, str], bool] = {}
        self.local_modules: set[str] = set()
        self._const_cache: dict[tuple[str, str], bool] = {}
        self._stores_cache: dict[str, tuple[int, set[str]]] = {}
        self._blob_cache: dict[str, tuple[int, bytes]] = {}
        self._names_cache: dict[str, tuple] = {}
        self._pkg_store_names: dict[str, set[bytes]] = {}
        self._pkg_dyn_store: set[str] = set()

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
        try:
            self._note_stores(name, p.read_bytes())
        except OSError:
            pass
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
        self._collect_bindings(info, tree.body, info.bindings, "", True, False)
        all_b = info.bindings.get("__all__")
        if all_b and len(all_b) == 1 and all_b[0].kind == "literal" and all_b[0].node is not None:
            try:
                val = ast.literal_eval(all_b[0].node)
                if isinstance(val, (list, tuple)) and all(isinstance(x, str) for x in val):
                    info.all_names = list(val)
            except Exception:  # noqa: BLE001
                pass
        self._collect_defs(info, tree.body, "", None)

    def _add(self, out: dict[str, list[Binding]], name: str, b: Binding) -> None:
        out.setdefault(name, []).append(b)

    def _collect_bindings(
        self, info: ModuleInfo, body: list[ast.stmt], out: dict[str, list[Binding]], qual_prefix: str,
        is_module: bool, cond: bool,
    ) -> None:
        for stmt in body:
            if isinstance(stmt, ast.Import):
                for alias in stmt.names:
                    if alias.asname:
                        self._add(out, alias.asname, Binding("module", target=alias.name, conditional=cond))
                    else:
                        top = alias.name.split(".")[0]
                        self._add(out, top, Binding("module", target=top, conditional=cond))
            elif isinstance(stmt, ast.ImportFrom):
                base = _abs_from_base(info.name, info.is_package, stmt.level or 0, stmt.module)
                if base == "__future__":
                    continue
                for alias in stmt.names:
                    if alias.name == "*":
                        if is_module and base:
                            info.star_sources.append(base)
                        continue
                    self._add(out, alias.asname or alias.name,
                              Binding("from", target=base, name=alias.name, conditional=cond))
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add(out, stmt.name, Binding("def", node=stmt, qualname=qual_prefix + stmt.name, conditional=cond))
                if is_module and stmt.name == "__getattr__":
                    info.has_getattr = True
            elif isinstance(stmt, ast.ClassDef):
                self._add(out, stmt.name, Binding("class", node=stmt, qualname=qual_prefix + stmt.name, conditional=cond))
            elif isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    self._bind_target(target, stmt.value, out, cond)
            elif isinstance(stmt, ast.AnnAssign):
                if stmt.value is not None:
                    self._bind_target(stmt.target, stmt.value, out, cond)
            elif isinstance(stmt, ast.AugAssign):
                if isinstance(stmt.target, ast.Name):
                    self._add(out, stmt.target.id, Binding("unknown", conditional=cond))
            elif isinstance(stmt, ast.If):
                if is_type_checking_test(stmt.test):
                    self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module, True)
                    continue
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module, True)
                self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module, True)
            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module, True)
                for h in stmt.handlers:
                    if h.name:
                        self._add(out, h.name, Binding("unknown", conditional=True))
                    self._collect_bindings(info, h.body, out, qual_prefix, is_module, True)
                self._collect_bindings(info, stmt.orelse, out, qual_prefix, is_module, True)
                self._collect_bindings(info, stmt.finalbody, out, qual_prefix, is_module, True)
            elif isinstance(stmt, (ast.With, ast.AsyncWith, ast.For, ast.AsyncFor, ast.While)):
                if isinstance(stmt, (ast.With, ast.AsyncWith)):
                    for item in stmt.items:
                        if item.optional_vars is not None:
                            for n in ast.walk(item.optional_vars):
                                if isinstance(n, ast.Name):
                                    self._add(out, n.id, Binding("unknown", conditional=True))
                if isinstance(stmt, (ast.For, ast.AsyncFor)):
                    for n in ast.walk(stmt.target):
                        if isinstance(n, ast.Name):
                            self._add(out, n.id, Binding("unknown", conditional=True))
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module, True)
                self._collect_bindings(info, getattr(stmt, "orelse", []), out, qual_prefix, is_module, True)
            elif isinstance(stmt, ast.Match):
                for case in stmt.cases:
                    self._collect_bindings(info, case.body, out, qual_prefix, is_module, True)
            elif isinstance(stmt, ast.Delete):
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        # 削除も束縛の変化として扱う（定数ではなくなる）
                        self._add(out, t.id, Binding("unknown", conditional=True))
            # walrus
            for n in ast.walk(stmt) if isinstance(stmt, (ast.Expr, ast.Assign, ast.If, ast.While)) else ():
                if isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
                    self._add(out, n.target.id, Binding("unknown", conditional=True))

    def _bind_target(self, target: ast.expr, value: ast.expr, out: dict[str, list[Binding]], cond: bool) -> None:
        if isinstance(target, ast.Name):
            if _fold_ok(value):
                self._add(out, target.id, Binding("literal", node=value, conditional=cond))
            elif isinstance(value, (ast.Name, ast.Attribute)):
                self._add(out, target.id, Binding("alias", node=value, conditional=cond))
            elif isinstance(value, ast.Call):
                self._add(out, target.id, Binding("call", node=value, conditional=cond))
            else:
                self._add(out, target.id, Binding("unknown", node=value, conditional=cond))
        elif isinstance(target, (ast.Tuple, ast.List, ast.Starred)):
            for n in ast.walk(target):
                if isinstance(n, ast.Name):
                    self._add(out, n.id, Binding("unknown", conditional=True))

    def _collect_mutations(self, info: ModuleInfo) -> None:
        if info.mutations_done or info.tree is None:
            return
        info.mutations_done = True
        tree = info.tree
        for n in ast.walk(tree):
            if isinstance(n, ast.Global):
                info.mutated.update(n.names)
                info.global_names.update(n.names)
            elif isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
                targets = n.targets if isinstance(n, (ast.Assign, ast.Delete)) else [n.target]
                for t in targets:
                    for x in ast.walk(t):
                        if isinstance(x, ast.Attribute) and isinstance(x.ctx, (ast.Store, ast.Del)):
                            info.attr_stores.add(x.attr)
                            if isinstance(x.value, ast.Name):
                                info.mutated.add(x.value.id)
                        elif isinstance(x, ast.Subscript) and isinstance(x.ctx, (ast.Store, ast.Del)):
                            if isinstance(x.value, ast.Name):
                                info.mutated.add(x.value.id)
            elif isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    info.mutated.add(f.value.id)  # NAME.append(...) 等（不変値なら後で無視）
                if isinstance(f, ast.Name) and f.id in ("setattr", "delattr") and len(n.args) >= 2:
                    a = n.args[1]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        info.attr_stores.add(a.value)
                    else:
                        info.attr_stores.add("*")

    def _collect_defs(
        self, info: ModuleInfo, body: list[ast.stmt], prefix: str, cls: ClassInfo | None
    ) -> None:
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + stmt.name
                key = qual
                n = 1
                while key in info.functions:
                    n += 1
                    key = f"{qual}#{n}"
                info.functions[key] = stmt
                info.qual_of[id(stmt)] = key
                if cls is not None:
                    info.func_class[key] = cls.qualname
                    cls.methods.setdefault(stmt.name, []).append(key)
                    if any(_decorator_last_name(d) in _PROPERTY_DECORATORS for d in stmt.decorator_list):
                        cls.properties.add(stmt.name)
                self._collect_defs(info, stmt.body, qual + ".<locals>.", None)
            elif isinstance(stmt, ast.ClassDef):
                qual = prefix + stmt.name
                key = qual
                n = 1
                while key in info.classes:
                    n += 1
                    key = f"{qual}#{n}"
                ci = ClassInfo(module=info.name, qualname=key, node=stmt, bases=list(stmt.bases),
                               keywords=list(stmt.keywords))
                self._collect_bindings(info, stmt.body, ci.attrs, qual + ".", False, False)
                info.classes[key] = ci
                info.class_qual_of[id(stmt)] = key
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

    # --- 定数・クラスの性質 ---

    def _package_blob(self, top: str) -> bytes:
        """パッケージ top の（読み込んだ）モジュールのソースを連結したもの（属性への書き込みの検出用）。"""
        n = len(self.modules)
        cached = self._blob_cache.get(top)
        if cached is not None and cached[0] == n:
            return cached[1]
        parts: list[bytes] = []
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top or not other.path:
                continue
            try:
                parts.append(Path(other.path).read_bytes())
            except OSError:
                continue
        blob = b"\n".join(parts)
        self._blob_cache[top] = (n, blob)
        return blob

    _STORE_RE = re.compile(rb"\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:[-+*/%&|^@]|<<|>>|//|\*\*)?=(?!=)")
    _DEL_RE = re.compile(rb"del\s+[^\n]*?\.([A-Za-z_][A-Za-z0-9_]*)")
    _SETATTR_RE = re.compile(rb"setattr\([^,)]*,\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")
    _SETATTR_DYN_RE = re.compile(rb"setattr\([^,)]*,\s*[^'\"\s]")

    def _note_stores(self, name: str, data: bytes) -> None:
        top = name.split(".")[0]
        names = self._pkg_store_names.setdefault(top, set())
        names.update(self._STORE_RE.findall(data))
        names.update(self._DEL_RE.findall(data))
        names.update(self._SETATTR_RE.findall(data))
        if self._SETATTR_DYN_RE.search(data) or b"__dict__[" in data:
            self._pkg_dyn_store.add(top)

    def attr_stored_in_package(self, top: str, name: str) -> bool:
        """`x.NAME = ...` / `x.NAME += ...` / `del x.NAME` / setattr(x, "NAME", ...) がパッケージ内にありうるか（安全側）。"""
        names = self._pkg_store_names.get(top, set())
        return name.encode() in names or (top in self._pkg_dyn_store and name.isupper())

    def package_attr_stores(self, top: str) -> set[str]:  # 互換（未使用）
        return set()

    def binding_is_const(self, info: ModuleInfo, name: str, blist: list[Binding]) -> bool:
        """無条件のトップレベルで 1 回だけ束縛され、変更されないリテラルか（仕様 2 節 A / 指示 1）。"""
        if len(blist) != 1:
            return False
        b = blist[0]
        if b.kind != "literal" or b.conditional:
            return False
        key = (info.name, name)
        if key in self._const_cache and len(self.modules) == self._const_cache.get(("#n", ""), -1):
            return self._const_cache[key]
        ok = literal_source(b.node) is not None
        if ok:
            self._collect_mutations(info)
            if name in info.mutated and (_is_mutable_literal(b.node) or name in info.global_names):
                ok = False
        if ok and self.attr_stored_in_package(info.name.split(".")[0], name):
            ok = False
        self._const_cache[key] = ok
        self._const_cache[("#n", "")] = len(self.modules)  # type: ignore[assignment]
        return ok

    def class_is_plain(self, module: str, qual: str, depth: int = 0) -> bool:
        """基底が object か解析対象の単純なクラスだけで、metaclass 指定が無いか。"""
        key = (module, qual)
        if key in self._plain_cache:
            return self._plain_cache[key]
        self._plain_cache[key] = False
        info = self.get(module)
        ci = info.classes.get(qual) if info is not None else None
        ok = ci is not None and not ci.keywords and depth < 50
        if ok:
            for b in ci.bases:  # type: ignore[union-attr]
                if isinstance(b, ast.Name) and b.id == "object":
                    continue
                r = self.ref_of_expr(info, b, None)  # type: ignore[arg-type]
                if r is None or r.kind != "class" or r.alts or not self.class_is_plain(r.module, r.target, depth + 1):
                    ok = False
                    break
        self._plain_cache[key] = ok
        return ok

    # --- 解決 ---

    def resolve(
        self, mod: str, chain: tuple[str, ...], trace: tuple = (), depth: int = 0
    ) -> Ref:
        trace = trace + ((mod, chain),)
        if depth > 40:
            return Ref("unknown", mod, chain, trace)
        if not chain:
            if is_external_module(mod):
                return Ref("external_module", mod, (), trace)
            return Ref("module", mod, (), trace)
        if is_external_module(mod):
            return Ref("external", mod, chain, trace)
        info = self.get(mod)
        if info is None:
            return Ref("opaque", mod, chain, trace)
        head, rest = chain[0], chain[1:]
        if info.opaque:
            sub = self.submodule(info, head)
            if sub:
                return self.resolve(sub, rest, trace, depth + 1)
            return Ref("opaque", mod, chain, trace)
        blist = info.bindings.get(head)
        if not blist:
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
        if len(blist) == 1:
            return self._resolve_binding(info, head, blist, blist[0], rest, trace, depth)
        alts = tuple(self._resolve_binding(info, head, blist, b, rest, trace, depth) for b in blist)
        # 同じ定義（def / class）への複数束縛でも、どれが実行されるかは決めない
        return Ref("multi", mod, chain, trace, alts=alts)

    def _resolve_binding(
        self, info: ModuleInfo, head: str, blist: list[Binding], b: Binding, rest: tuple[str, ...],
        trace: tuple, depth: int,
    ) -> Ref:
        mod = info.name
        chain = (head,) + rest
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
            q = info.qual_of.get(id(b.node), b.qualname)
            if rest:
                return Ref("unknown", mod, chain, trace)
            return Ref("function", mod, chain, trace, target=q)
        if b.kind == "class":
            q = info.class_qual_of.get(id(b.node), b.qualname)
            return self._resolve_class_attr(info, q, (head,), rest, trace)
        if b.kind == "literal":
            if self.binding_is_const(info, head, blist):
                if not rest:
                    return Ref("const", mod, chain, trace)
                return Ref("constattr", mod, chain, trace)
            return Ref("unknown", mod, chain, trace)
        if b.kind in ("alias", "call") and b.node is not None:
            base = self.ref_of_expr(info, b.node, None, depth + 1, trace)
            if base is None:
                return Ref("unknown", mod, chain, trace)
            if base.kind in ("external_value", "builtin_value", "derived"):
                # 呼び出しの結果の値はスタブで再現できない（定数ではない）
                return Ref("unknown", mod, chain, trace)
            return self.extend(base, rest, depth + 1)
        return Ref("unknown", mod, chain, trace)

    def _class_attr_lookup(self, info: ModuleInfo, ci: ClassInfo, attr: str, depth: int = 0):
        """クラス（と解析対象の基底）でのクラス属性の束縛を探す。(info, ci, kind, payload)"""
        if depth > 50:
            return None
        if attr in ci.methods:
            return (info, ci, "method", ci.methods[attr])
        if attr in ci.attrs:
            return (info, ci, "attr", ci.attrs[attr])
        for b in ci.bases:
            r = self.ref_of_expr(info, b, None)
            if r is not None and r.kind == "class" and not r.alts:
                binfo = self.get(r.module)
                bci = binfo.classes.get(r.target) if binfo is not None else None
                if binfo is not None and bci is not None:
                    found = self._class_attr_lookup(binfo, bci, attr, depth + 1)
                    if found is not None:
                        return found
        return None

    def _resolve_class_attr(
        self, info: ModuleInfo, cls_qual: str, chain: tuple[str, ...], rest: tuple[str, ...], trace: tuple
    ) -> Ref:
        if not rest:
            return Ref("class", info.name, chain, trace, target=cls_qual)
        ci = info.classes.get(cls_qual)
        attr = rest[0]
        if ci is not None:
            found = self._class_attr_lookup(info, ci, attr)
            if found is not None:
                finfo, fci, kind, payload = found
                if kind == "method":
                    if len(rest) == 1:
                        quals = payload
                        if len(quals) == 1:
                            return Ref("method", finfo.name, chain + rest, trace, target=quals[0])
                        alts = tuple(Ref("method", finfo.name, chain + rest, trace, target=q) for q in quals)
                        return Ref("multi", info.name, chain + rest, trace, alts=alts)
                    return Ref("unknown", info.name, chain + rest, trace)
                blist = payload
                plain = self.class_is_plain(info.name, cls_qual)
                if len(blist) == 1 and not blist[0].conditional:
                    ab = blist[0]
                    if ab.kind == "literal" and plain and len(rest) == 1 and literal_source(ab.node) is not None \
                            and not self._class_attr_stored(finfo, attr):
                        return Ref("classconst", info.name, chain + rest, trace, target=cls_qual)
                    if ab.kind == "class":
                        q = finfo.class_qual_of.get(id(ab.node), ab.qualname)
                        return self._resolve_class_attr(finfo, q, chain + (attr,), rest[1:], trace)
        return Ref("classattr", info.name, chain + rest, trace, target=cls_qual)

    def _class_attr_stored(self, info: ModuleInfo, attr: str) -> bool:
        return self.attr_stored_in_package(info.name.split(".")[0], attr)

    def extend(self, base: Ref, rest: tuple[str, ...], depth: int = 0) -> Ref:
        if not rest:
            return base
        r = self._extend(base, rest, depth)
        return replace(r, marks=base.marks + ((len(base.trace), rest),))

    def _extend(self, base: Ref, rest: tuple[str, ...], depth: int = 0) -> Ref:
        if base.alts:
            alts = tuple(self.extend(a, rest, depth + 1) for a in base.alts)
            return Ref("multi", base.module, base.chain + rest, base.trace, alts=alts)
        if base.kind in ("module", "external_module"):
            return self.resolve(base.module, rest, base.trace, depth + 1)
        if base.kind == "class":
            info = self.get(base.module)
            if info is not None and not info.opaque:
                return self._resolve_class_attr(info, base.target, base.chain, rest, base.trace)
            return Ref("classattr", base.module, base.chain + rest, base.trace, target=base.target)
        if base.kind == "instance" and len(rest) == 1:
            info = self.get(base.module)
            ci = info.classes.get(base.target) if info is not None else None
            if ci is not None:
                found = self._class_attr_lookup(info, ci, rest[0])  # type: ignore[arg-type]
                if found is not None and found[2] == "method":
                    quals = found[3]
                    if len(quals) == 1:
                        return Ref("method", found[0].name, base.chain + rest, base.trace, target=quals[0])
                    alts = tuple(Ref("method", found[0].name, base.chain + rest, base.trace, target=q) for q in quals)
                    return Ref("multi", base.module, base.chain + rest, base.trace, alts=alts)
        if base.kind in ("const", "constattr", "classconst"):
            return Ref("constattr", base.module, base.chain + rest, base.trace)
        if base.kind == "external":
            return Ref("external", base.module, base.chain + rest, base.trace)
        if base.kind in ("external_value", "builtin_value"):
            # stdlib / 組み込みの値の属性: 解析対象のメソッドではない
            return Ref(base.kind, base.module, base.chain + rest, base.trace)
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
                if expr.id in ("True", "False", "None"):
                    return None
                if hasattr(builtins, expr.id):
                    return Ref("external", "builtins", (expr.id,), trace + (("builtins", (expr.id,)),))
                return None
            r = self.resolve(info.name, (expr.id,), trace, depth + 1)
            if r.kind == "missing" and len(r.trace) <= 1 + len(trace):
                if hasattr(builtins, expr.id):
                    return Ref("external", "builtins", (expr.id,), trace + (("builtins", (expr.id,)),))
                return None
            return r
        if isinstance(expr, ast.Attribute):
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)
            if base is None:
                return None
            return self.extend(base, (expr.attr,), depth + 1)
        if isinstance(expr, ast.Call):
            # importlib.import_module("x") / __import__("x") → モジュール
            lit = _import_call_literal(expr)
            if lit:
                name, top_only = lit
                return self.resolve(name.split(".")[0] if top_only else name, (), trace, depth + 1)
            base = self.ref_of_expr(info, expr.func, scope, depth + 1, trace)
            if base is None:
                return None
            if base.kind == "class" and not base.alts:
                return replace(base, kind="instance", marks=base.marks + ((len(base.trace), ("()",)),))
            if base.kind == "external":
                return replace(base, kind="external_value", chain=base.chain + ("()",),
                               marks=base.marks + ((len(base.trace), ("()",)),))
            return replace(base, kind="derived", chain=base.chain + ("()",), alts=(),
                           marks=base.marks + ((len(base.trace), ("()",)),))
        if isinstance(expr, ast.Subscript):
            v = expr.value
            if isinstance(v, ast.Attribute) and v.attr == "modules" and isinstance(v.value, ast.Name) \
                    and v.value.id == "sys":
                sl = expr.slice
                if isinstance(sl, ast.Name) and sl.id == "__name__":
                    return self.resolve(info.name, (), trace, depth + 1)
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    return self.resolve(sl.value, (), trace, depth + 1)
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)
            if base is None:
                return None
            if base.kind == "class":
                return base  # Generic[...] 等
            return replace(base, kind="derived", chain=base.chain + ("[]",), alts=(),
                           marks=base.marks + ((len(base.trace), ("[]",)),))
        if isinstance(expr, (ast.Constant, ast.JoinedStr, ast.List, ast.Dict, ast.Set, ast.Tuple,
                             ast.ListComp, ast.DictComp, ast.SetComp)):
            return Ref("builtin_value", "builtins", (), trace)
        return None

    # --- コード単位 ---

    def build_module_unit(self, info: ModuleInfo) -> None:
        if info.unit_built or info.opaque or info.tree is None:
            info.unit_built = True
            return
        info.unit_built = True
        _UnitWalker(self, info).run_module()

    def function_unit(self, module: str, qual: str) -> Unit | None:
        key = (module, qual)
        u = self.units.get(key)
        if u is not None:
            return u
        info = self.get(module)
        if info is None or info.opaque or qual not in info.functions:
            return None
        if not info.unit_built:
            self.build_module_unit(info)
        return _UnitWalker(self, info).run_function(qual)


def _import_call_literal(call: ast.Call) -> tuple[str, bool] | None:
    """importlib.import_module("x") → ("x", False)、__import__("x") → ("x", True)。"""
    f = call.func
    is_im = (isinstance(f, ast.Attribute) and f.attr == "import_module") or (
        isinstance(f, ast.Name) and f.id == "import_module"
    )
    is_dunder = isinstance(f, ast.Name) and f.id == "__import__"
    if not (is_im or is_dunder) or not call.args:
        return None
    a = call.args[0]
    if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
        return None
    if is_im and a.value.startswith("."):
        return None
    top_only = is_dunder and not any(k.arg == "fromlist" for k in call.keywords) and len(call.args) < 4
    return a.value, top_only


# ---------------------------------------------------------------------------
# コード単位のイベント収集
# ---------------------------------------------------------------------------


class _UnitWalker:
    def __init__(self, program: Program, info: ModuleInfo) -> None:
        self.p = program
        self.info = info
        self.guard = ""
        self.unit: Unit | None = None

    # --- 入口 ---

    def run_module(self) -> None:
        tree = self.info.tree
        assert tree is not None
        unit = Unit(self.info.name, "<module>", tree)
        self.p.units[unit.key] = unit
        self.unit = unit
        self._walk_body(tree.body, None, None, protected=False, protect_ok=False)
        if self.info.has_getattr:
            for q in self.info.functions:
                if q == "__getattr__" or q.startswith("__getattr__#"):
                    unit.escaped_units.append(q)

    def run_function(self, qual: str) -> Unit:
        node = self.info.functions[qual]
        cls_qual = self.info.func_class.get(qual, "")
        u = Unit(self.info.name, qual, node, cls=cls_qual)
        self.p.units[u.key] = u
        self.unit = u
        assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        scope, unique = self._function_scope(node)
        self.unique = unique
        self._walk_body(node.body, scope, None, protected=False, protect_ok=False)
        return u

    # --- スコープ ---

    def _function_scope(self, node: ast.AST) -> tuple[dict[str, Ref | None], set[str]]:
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
            return scope, set()
        globals_: set[str] = set()
        counts: dict[str, int] = {}
        pending_alias: list[tuple[str, ast.expr]] = []
        for n in self._iter_own_nodes(node.body):
            if isinstance(n, (ast.Global, ast.Nonlocal)):
                globals_.update(n.names)
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    nm = alias.asname or alias.name.split(".")[0]
                    counts[nm] = counts.get(nm, 0) + 1
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    scope[nm] = self.p.resolve(target, ())
            elif isinstance(n, ast.ImportFrom):
                base = _abs_from_base(self.info.name, self.info.is_package, n.level or 0, n.module)
                for alias in n.names:
                    if alias.name == "*" or not base:
                        continue
                    nm = alias.asname or alias.name
                    counts[nm] = counts.get(nm, 0) + 1
                    scope[nm] = self.p.resolve(base, (alias.name,))
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                counts[n.name] = counts.get(n.name, 0) + 1
                q = self.info.qual_of.get(id(n)) if not isinstance(n, ast.ClassDef) else self.info.class_qual_of.get(id(n))
                if q and isinstance(n, ast.ClassDef):
                    scope[n.name] = Ref("class", self.info.name, (n.name,), ((self.info.name, (n.name,)),), target=q)
                elif q:
                    scope[n.name] = Ref("function", self.info.name, (n.name,), ((self.info.name, (n.name,)),), target=q)
                else:
                    scope[n.name] = None
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        counts[t.id] = counts.get(t.id, 0) + 1
                        pending_alias.append((t.id, n.value))
                        scope.setdefault(t.id, None)
                    else:
                        for x in ast.walk(t):
                            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store):
                                counts[x.id] = counts.get(x.id, 0) + 2
                                scope.setdefault(x.id, None)
            elif isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
                t = n.target
                if isinstance(t, ast.Name):
                    counts[t.id] = counts.get(t.id, 0) + (1 if not isinstance(n, ast.AugAssign) else 2)
                    if not isinstance(n, ast.AugAssign) and n.value is not None:
                        pending_alias.append((t.id, n.value))
                    scope.setdefault(t.id, None)
            elif isinstance(n, (ast.For, ast.AsyncFor, ast.comprehension)):
                for x in ast.walk(n.target):
                    if isinstance(x, ast.Name):
                        counts[x.id] = counts.get(x.id, 0) + 2
                        scope.setdefault(x.id, None)
            elif isinstance(n, (ast.With, ast.AsyncWith)):
                for item in n.items:
                    if item.optional_vars is not None:
                        for x in ast.walk(item.optional_vars):
                            if isinstance(x, ast.Name):
                                counts[x.id] = counts.get(x.id, 0) + 2
                                scope.setdefault(x.id, None)
            elif isinstance(n, ast.ExceptHandler):
                if n.name:
                    counts[n.name] = counts.get(n.name, 0) + 2
                    scope.setdefault(n.name, None)
            elif isinstance(n, ast.Delete):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        counts[t.id] = counts.get(t.id, 0) + 2
        for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            counts[a.arg] = counts.get(a.arg, 0) + 2
        for g in globals_:
            scope.pop(g, None)
            counts.pop(g, None)
        unique = {n for n, c in counts.items() if c == 1}
        for name, value in pending_alias:
            if name in globals_ or name not in unique:
                continue
            r = self.p.ref_of_expr(self.info, value, scope)
            if r is not None:
                scope[name] = r
        return scope, unique

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

    def _ev(self, ev: Event) -> None:
        if self.guard and not ev.guard:
            ev.guard = self.guard
        assert self.unit is not None
        self.unit.events.append(ev)

    def _walk_body(
        self, body: list[ast.stmt], scope: dict[str, Ref | None] | None, cls_scope: dict[str, Ref | None] | None,
        protected: bool, protect_ok: bool,
    ) -> None:
        saved = self.guard
        for stmt in body:
            self._walk_stmt(stmt, scope, cls_scope, protected, protect_ok)
            g = _sys_modules_guard(stmt) if scope is not None else ""
            if g and not self.guard:
                self.guard = g
        self.guard = saved

    def _bind_ctx(self, name: str, scope: dict[str, Ref | None] | None, cls_scope) -> str:
        """`name = <式>` の右辺の文脈。束縛が一意で無条件なら bind（A）、それ以外は store（C）。"""
        if cls_scope is not None:
            return "store"  # クラス本体の代入は self 経由で使われうる（S8）
        if scope is not None:
            return "bind" if name in getattr(self, "unique", set()) else "store"
        blist = self.info.bindings.get(name, [])
        if len(blist) == 1 and not blist[0].conditional:
            return "bind"
        return "store"

    def _walk_stmt(
        self, stmt: ast.stmt, scope: dict[str, Ref | None] | None, cls_scope: dict[str, Ref | None] | None,
        protected: bool, protect_ok: bool,
    ) -> None:
        ev = self._expr
        sc = cls_scope if cls_scope is not None else scope
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._walk_def(stmt, scope, cls_scope)
        elif isinstance(stmt, ast.ClassDef):
            for d in stmt.decorator_list:
                ev(d, "decorator", sc)
            for b in stmt.bases:
                ev(b, "base", sc)
            for k in stmt.keywords:
                ev(k.value, "base", sc)
            q = self.info.class_qual_of.get(id(stmt), stmt.name)
            self._ev(Event("classdef", target=q, lineno=stmt.lineno))
            # クラス本体は定義時に実行される。本体内の名前（メソッド等）はクラス本体のスコープ
            cscope: dict[str, Ref | None] = dict(scope) if scope is not None else {}
            for s in stmt.body:
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self._walk_def(s, scope, cscope)
                    mq = self.info.qual_of.get(id(s))
                    if mq:
                        cscope[s.name] = Ref("function", self.info.name, (s.name,),
                                             ((self.info.name, (s.name,)),), target=mq)
                else:
                    self._walk_stmt(s, scope, cscope, protected, protect_ok)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                parts = alias.name.split(".")
                mods = tuple(".".join(parts[: i + 1]) for i in range(len(parts)))
                self._ev(Event("import", modules=mods, lineno=stmt.lineno, protected=protected,
                               protect_ok=protect_ok, names=(alias.asname or parts[0],)))
        elif isinstance(stmt, ast.ImportFrom):
            base = _abs_from_base(self.info.name, self.info.is_package, stmt.level or 0, stmt.module)
            if base and base != "__future__":
                parts = base.split(".")
                mods = [".".join(parts[: i + 1]) for i in range(len(parts))]
                names = tuple(a.name for a in stmt.names)
                self._ev(Event("import", modules=tuple(mods), from_base=base, names=names,
                               lineno=stmt.lineno, protected=protected, protect_ok=protect_ok))
        elif isinstance(stmt, ast.Expr):
            ev(stmt.value, "read", sc)
        elif isinstance(stmt, ast.Assign):
            if all(isinstance(t, ast.Name) for t in stmt.targets):
                ctxs = {self._bind_ctx(t.id, scope, cls_scope) for t in stmt.targets}  # type: ignore[union-attr]
                ctx = "bind" if ctxs == {"bind"} else "store"
            else:
                ctx = "store"
            ev(stmt.value, ctx, sc)
            for t in stmt.targets:
                self._target(t, sc)
        elif isinstance(stmt, ast.AnnAssign):
            if not self.info.future_annotations and scope is None:
                ev(stmt.annotation, "annot", sc)
            if stmt.value is not None:
                ctx = self._bind_ctx(stmt.target.id, scope, cls_scope) if isinstance(stmt.target, ast.Name) else "store"
                ev(stmt.value, ctx, sc)
            self._target(stmt.target, sc)
        elif isinstance(stmt, ast.AugAssign):
            ev(stmt.value, "operand", sc)
            ev(stmt.target, "operand", sc)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                ev(stmt.value, "return", sc)
        elif isinstance(stmt, ast.If):
            ev(stmt.test, "operand", sc)
            if is_type_checking_test(stmt.test):
                self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
                return
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
        elif isinstance(stmt, ast.While):
            ev(stmt.test, "operand", sc)
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            ev(stmt.iter, "iter", sc)
            self._target(stmt.target, sc)
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                ev(item.context_expr, "with", sc)
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
        elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            prot = handlers_catch_import_error(stmt.handlers)  # type: ignore[arg-type]
            simple = prot and self._simple_protection(stmt)
            self._walk_body(stmt.body, scope, cls_scope, protected or prot, (protect_ok or not protected) and simple
                            if prot else protect_ok)
            for h in stmt.handlers:
                if h.type is not None:
                    ev(h.type, "except", sc)
                self._walk_body(h.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.finalbody, scope, cls_scope, protected, protect_ok)
        elif isinstance(stmt, ast.Raise):
            if stmt.exc is not None:
                ev(stmt.exc, "raise", sc)
            if stmt.cause is not None:
                ev(stmt.cause, "raise", sc)
        elif isinstance(stmt, ast.Assert):
            ev(stmt.test, "operand", sc)
            if stmt.msg is not None:
                ev(stmt.msg, "operand", sc)
        elif isinstance(stmt, ast.Delete):
            for t in stmt.targets:
                if isinstance(t, ast.Name):
                    ev(t, "del", sc)
                else:
                    self._target(t, sc, ctx="del")
        elif isinstance(stmt, ast.Match):
            ev(stmt.subject, "operand", sc)
            for case in stmt.cases:
                for n in ast.walk(case.pattern):
                    if isinstance(n, ast.MatchClass):
                        ev(n.cls, "isinst", sc)
                    elif isinstance(n, ast.MatchValue):
                        ev(n.value, "eq", sc)
                if case.guard is not None:
                    ev(case.guard, "operand", sc)
                self._walk_body(case.body, scope, cls_scope, protected, protect_ok)
        # Pass / Break / Continue / Global / Nonlocal: 何もしない

    @staticmethod
    def _simple_protection(stmt: ast.Try) -> bool:
        """try 本体が import だけで、except が pass か（本体で束縛した名前への）None/False 代入だけか（仕様 3 節）。"""
        bound: set[str] = set()
        for s in stmt.body:
            if isinstance(s, ast.Import):
                bound.update(a.asname or a.name.split(".")[0] for a in s.names)
            elif isinstance(s, ast.ImportFrom):
                bound.update(a.asname or a.name for a in s.names)
            elif not isinstance(s, ast.Pass):
                return False
        if stmt.orelse or stmt.finalbody:
            return False
        for h in stmt.handlers:
            for s in h.body:
                if isinstance(s, ast.Pass):
                    continue
                if isinstance(s, ast.Assign) and all(isinstance(t, ast.Name) and t.id in bound for t in s.targets) \
                        and isinstance(s.value, ast.Constant) and s.value.value in (None, False):
                    continue
                return False
        return True

    def _walk_def(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef,
        scope: dict[str, Ref | None] | None, cls_scope: dict[str, Ref | None] | None,
    ) -> None:
        """def: デコレータ・既定引数・注釈は定義時（このユニット）に評価される。"""
        sc = cls_scope if cls_scope is not None else scope
        escaped = False
        for d in node.decorator_list:
            self._expr(d, "decorator", sc)
            name = _decorator_last_name(d)
            if name in _TRANSPARENT_DECORATORS:
                continue
            target = d.func if isinstance(d, ast.Call) else d
            r = self.p.ref_of_expr(self.info, target, sc)
            # 解析対象の関数で、引数を逃がさないと示せたものだけ透明（S10）
            if r is not None and r.kind == "function" and not r.alts:
                dinfo = self.p.get(r.module)
                dnode = dinfo.functions.get(r.target) if dinfo is not None else None
                if dnode is not None and not decorator_escapes(dnode):
                    continue
            escaped = True
        args = node.args
        for dflt in list(args.defaults) + [x for x in args.kw_defaults if x is not None]:
            self._expr(dflt, "store", sc)
        if not self.info.future_annotations:
            for a in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs) + [
                x for x in (args.vararg, args.kwarg) if x is not None
            ]:
                if a.annotation is not None:
                    self._expr(a.annotation, "annot", sc)
            if node.returns is not None:
                self._expr(node.returns, "annot", sc)
        if escaped:
            qual = self.info.qual_of.get(id(node), "")
            if qual:
                assert self.unit is not None
                self.unit.escaped_units.append(qual)

    def _target(self, t: ast.expr, sc: dict[str, Ref | None] | None, ctx: str = "store") -> None:
        # `P.x = 1` / `P.d[k] = v` は P のオブジェクトの変更（C）。`self.x = ...` 等は無関係
        if isinstance(t, ast.Attribute):
            self._expr(t.value, ctx, sc)
        elif isinstance(t, ast.Subscript):
            self._expr(t.value, ctx, sc)
            self._expr(t.slice, "operand", sc)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                self._target(e, sc, ctx)
        elif isinstance(t, ast.Starred):
            self._target(t.value, sc, ctx)

    # --- 式 ---

    def _record(self, ref: Ref | None, ctx: str, node: ast.AST) -> None:
        if ref is None:
            return
        self._ev(Event("ref", ctx=ctx, ref=ref, lineno=getattr(node, "lineno", 0)))

    def _receiver_is_builtin(self, value: ast.expr, sc) -> bool:
        if isinstance(value, (ast.Constant, ast.JoinedStr, ast.List, ast.Dict, ast.Set, ast.Tuple,
                              ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
            return True
        r = self.p.ref_of_expr(self.info, value, sc)
        if r is None or r.alts:
            return False
        return r.kind in ("builtin_value", "external", "external_value", "external_module", "const", "constattr")

    def _expr(self, e: ast.AST | None, ctx: str, sc: dict[str, Ref | None] | None) -> None:
        if e is None:
            return
        ev = self._expr
        if isinstance(e, (ast.Name, ast.Attribute)):
            ref = self.p.ref_of_expr(self.info, e, sc)
            if ref is not None:
                self._record(ref, ctx, e)
                root = e
                while isinstance(root, ast.Attribute):
                    root = root.value
                if not isinstance(root, ast.Name):
                    ev(root, "operand", sc)
                return
            if isinstance(e, ast.Attribute):
                # 受け手が解決できない属性: 同名のメソッド・property を名前で到達させる
                if not self._receiver_is_builtin(e.value, sc):
                    kind = "method" if ctx in ("call", "decorator", "arg", "store", "return") else "attr"
                    self._ev(Event(kind, name=e.attr, lineno=e.lineno))
                ev(e.value, "read", sc)
            return
        if isinstance(e, ast.Call):
            self._call(e, ctx, sc)
            return
        if isinstance(e, ast.Lambda):
            for dflt in list(e.args.defaults) + [x for x in e.args.kw_defaults if x is not None]:
                ev(dflt, "store", sc)
            lscope = dict(sc) if sc is not None else {}
            ls, _u = self._function_scope(e)
            lscope.update(ls)
            ev(e.body, "return", lscope)
            return
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            cscope = dict(sc) if sc is not None else {}
            for gen in e.generators:
                ev(gen.iter, "iter", cscope)
                for x in ast.walk(gen.target):
                    if isinstance(x, ast.Name):
                        cscope[x.id] = None
                for cond in gen.ifs:
                    ev(cond, "operand", cscope)
            if isinstance(e, ast.DictComp):
                ev(e.key, "store", cscope)
                ev(e.value, "store", cscope)
            else:
                ev(e.elt, "store", cscope)
            return
        if isinstance(e, (ast.Tuple, ast.List, ast.Set)):
            inner = ctx if ctx in ("isinst", "except", "annot") else ("store" if ctx not in ("arg", "return", "raise") else ctx)
            for x in e.elts:
                ev(x, inner, sc)
            return
        if isinstance(e, ast.Dict):
            for k in e.keys:
                ev(k, "store", sc)
            for v in e.values:
                ev(v, "store", sc)
            return
        if isinstance(e, ast.Starred):
            ev(e.value, ctx if ctx in ("arg", "isinst", "except") else "store", sc)
            return
        if isinstance(e, ast.Compare):
            if len(e.ops) == 1 and isinstance(e.ops[0], (ast.In, ast.NotIn)) and isinstance(e.left, ast.Constant) \
                    and isinstance(e.left.value, str):
                c0 = e.comparators[0]
                if isinstance(c0, ast.Attribute) and c0.attr == "modules" and isinstance(c0.value, ast.Name) \
                        and c0.value.id == "sys":
                    self._record(self.p.resolve(e.left.value, ()), "presence", e)
                    self._ev(Event("presence_check", name=e.left.value, lineno=e.lineno))
                    return
            c = "is" if all(isinstance(o, (ast.Is, ast.IsNot)) for o in e.ops) else "eq"
            ev(e.left, c, sc)
            for x in e.comparators:
                ev(x, c, sc)
            return
        if isinstance(e, ast.Subscript):
            if ctx == "annot":
                ev(e.value, "annot", sc)
                ev(e.slice, "annot", sc)
                return
            self._subscript_special(e, sc)
            ev(e.value, "subscript", sc)
            ev(e.slice, "operand", sc)
            return
        if isinstance(e, ast.NamedExpr):
            ev(e.value, "store", sc)
            return
        if isinstance(e, ast.IfExp):
            ev(e.test, "operand", sc)
            inner = ctx if ctx in ("isinst", "except", "annot") else "store"
            ev(e.body, inner, sc)
            ev(e.orelse, inner, sc)
            return
        if isinstance(e, (ast.Await, ast.Yield, ast.YieldFrom)):
            ev(e.value, "return", sc)
            return
        if isinstance(e, ast.Constant):
            if ctx == "annot" and isinstance(e.value, str):
                return  # 文字列の注釈は評価されない（get_type_hints は別途）
            return
        # BoolOp / BinOp / UnaryOp / JoinedStr / FormattedValue / Slice など
        inner = "annot" if ctx == "annot" else "operand"
        for child in ast.iter_child_nodes(e):
            if isinstance(child, ast.expr):
                ev(child, inner, sc)

    def _subscript_special(self, e: ast.Subscript, sc) -> None:
        """globals()[...] / sys.modules[...] の動的な取り出し。"""
        v = e.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id in ("globals", "vars", "locals") and not v.args:
            if isinstance(e.slice, ast.Constant) and isinstance(e.slice.value, str):
                r = self.p.resolve(self.info.name, (e.slice.value,))
                self._record(r, "unknown", e)
            else:
                self._ev(Event("dynamic", ref=Ref("module", self.info.name, (), ((self.info.name, ()),)),
                               name=_const_prefix(e.slice), lineno=e.lineno))
        elif isinstance(v, ast.Attribute) and v.attr == "modules" and isinstance(v.value, ast.Name) and v.value.id == "sys":
            s = e.slice
            if isinstance(s, ast.Name) and s.id == "__name__":
                return
            if isinstance(s, ast.Constant) and isinstance(s.value, str):
                self._record(self.p.resolve(s.value, ()), "unknown", e)
            else:
                self._ev(Event("unanalyzable", name="sys.modules[...]", lineno=e.lineno))

    def _call(self, e: ast.Call, ctx: str, sc) -> None:
        ev = self._expr
        func = e.func
        fname = ""
        if isinstance(func, ast.Name) and (sc is None or func.id not in sc) and func.id not in self.info.bindings:
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = "." + func.attr
        elif isinstance(func, ast.Name):
            fr = self.p.ref_of_expr(self.info, func, sc)
            if fr is not None and not fr.alts and fr.kind == "external" and fr.chain:
                fname = fr.chain[-1]

        lit = _import_call_literal(e)
        if lit is not None:
            name = lit[0]
            parts = name.split(".")
            self._ev(Event("import", modules=tuple(".".join(parts[: i + 1]) for i in range(len(parts))), lineno=e.lineno))
            for a in e.args[1:]:
                ev(a, "operand", sc)
            return
        if fname in ("__import__",) or fname == ".import_module" or fname == "import_module":
            self._dynamic_import(e, sc)
            return
        if fname in ("exec", "eval") or fname in (".exec", ".eval"):
            if e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str):
                try:
                    tree = ast.parse(e.args[0].value)
                except SyntaxError:
                    self._ev(Event("unanalyzable", name=fname.lstrip("."), lineno=e.lineno))
                    return
                # 文字列のコードも、このユニットの中で実行されるものとして扱う
                saved_unique = getattr(self, "unique", set())
                self._walk_body(tree.body, sc if isinstance(sc, dict) else None, None, False, False)
                self.unique = saved_unique
            else:
                self._ev(Event("unanalyzable", name=fname.lstrip("."), lineno=e.lineno))
            for a in e.args[1:]:
                ev(a, "arg", sc)
            return

        ev(func, "call" if ctx != "decorator" else "decorator", sc)

        if fname in ("isinstance", "issubclass") and len(e.args) >= 2:
            ev(e.args[0], "operand", sc)
            ev(e.args[1], "isinst", sc)
            for a in e.args[2:]:
                ev(a, "arg", sc)
        elif fname in ("hasattr", "getattr", "setattr", "delattr") and e.args:
            base_ref = self.p.ref_of_expr(self.info, e.args[0], sc)
            name_lit = self._literal_name(e.args[1], sc) if len(e.args) >= 2 else None
            if fname == "hasattr":
                ev(e.args[0], "hasattr" if name_lit is not None else "unknown", sc)
                if name_lit is not None and base_ref is None:
                    self._ev(Event("attr", name=name_lit, lineno=e.lineno))
            elif fname == "getattr" and name_lit is not None:
                if base_ref is not None:
                    self._record(self.p.extend(base_ref, (name_lit,)), ctx, e)
                else:
                    ev(e.args[0], "read", sc)
                    self._ev(Event("method" if ctx in ("call", "arg", "store", "return") else "attr",
                                   name=name_lit, lineno=e.lineno))
                for a in e.args[2:]:
                    ev(a, "arg", sc)
            elif fname == "getattr":
                on_self = isinstance(e.args[0], ast.Name) and e.args[0].id in ("self", "cls")
                ev(e.args[0], "unknown", sc)
                param = self._unreassigned_param(e.args[1])
                if param is not None and not (base_ref is not None and base_ref.kind == "module"):
                    # 名前が関数の引数: 呼び出し側のリテラルに絞る（仕様 4 節: 定数伝播）
                    assert self.unit is not None
                    self._ev(Event("dynamic_param", ref=base_ref, name=self.unit.qualname, lineno=e.lineno,
                                   ctx="self" if on_self else "", target=f"{param[0]}:{param[1]}"))
                else:
                    self._ev(Event("dynamic", ref=base_ref, name=_const_prefix(e.args[1]), lineno=e.lineno,
                                   ctx="self" if on_self else ""))
                for a in e.args[1:]:
                    ev(a, "operand", sc)
            else:  # setattr / delattr
                ev(e.args[0], "store", sc)
                for a in e.args[1:]:
                    ev(a, "arg", sc)
        elif fname in ("cast",) and len(e.args) >= 2:
            ev(e.args[0], "annot", sc)
            ev(e.args[1], ctx, sc)
        elif fname in ("TypeVar", "NewType"):
            for a in e.args[1:]:
                ev(a, "annot", sc)
            for k in e.keywords:
                ev(k.value, "annot", sc)
            return
        elif fname in (".methodcaller",) or fname == "methodcaller":
            if e.args and isinstance(e.args[0], ast.Constant) and isinstance(e.args[0].value, str):
                self._ev(Event("method", name=e.args[0].value, lineno=e.lineno))
            for a in e.args:
                ev(a, "arg", sc)
        elif fname in (".find_spec", "find_spec") and e.args and isinstance(e.args[0], ast.Constant) \
                and isinstance(e.args[0].value, str):
            self._record(self.p.resolve(e.args[0].value, ()), "presence", e)
            self._ev(Event("presence_check", name=e.args[0].value, lineno=e.lineno))
        elif fname in (".entry_points", "entry_points", ".iter_entry_points", "iter_entry_points") and \
                self._is_metadata_entry_points(func, sc):
            gexpr = None
            for k in e.keywords:
                if k.arg == "group":
                    gexpr = k.value
            if gexpr is None and fname.endswith("iter_entry_points") and e.args:
                gexpr = e.args[0]
            groups = self._string_values(gexpr) if gexpr is not None else None
            if groups is None:
                self._ev(Event("entry_points", name="", lineno=e.lineno))
            else:
                for grp in sorted(groups):
                    self._ev(Event("entry_points", name=grp, lineno=e.lineno))
            for a in e.args:
                ev(a, "arg", sc)
        elif fname in (".__subclasses__",):
            base = func.value if isinstance(func, ast.Attribute) else None
            r = self.p.ref_of_expr(self.info, base, sc) if base is not None else None
            self._ev(Event("subclasses", ref=r, lineno=e.lineno))
        else:
            for a in e.args:
                ev(a, "arg", sc)
        for k in e.keywords:
            ev(k.value, "arg", sc)
        # cls() / type(self)() / self.__class__() → このクラス族のインスタンス化（S10）
        assert self.unit is not None
        if self.unit.cls:
            if (isinstance(func, ast.Name) and func.id == "cls") or (
                isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "type"
                and func.args and isinstance(func.args[0], ast.Name) and func.args[0].id in ("self", "cls")
            ) or (isinstance(func, ast.Attribute) and func.attr == "__class__"):
                self._ev(Event("family", target=self.unit.cls, lineno=e.lineno))

    def _is_metadata_entry_points(self, func: ast.expr, sc) -> bool:
        """entry_points が importlib.metadata / importlib_metadata / pkg_resources のものか（不明なら真: 安全側）。"""
        r = self.p.ref_of_expr(self.info, func, sc)
        if r is None:
            return True
        for rr in r.all_refs():
            mods = {m for m, _ in rr.trace}
            if any(m.split(".")[0] in ("importlib", "importlib_metadata", "pkg_resources") for m in mods):
                return True
            if rr.kind in ("function", "method", "class") and not any(
                m.split(".")[0] in ("importlib", "importlib_metadata", "pkg_resources") for m in mods
            ):
                continue
            if rr.kind in ("opaque", "unknown", "derived", "external"):
                return True
        return False

    def _string_values(self, expr: ast.expr) -> set[str] | None:
        """式が取りうる文字列（リテラル / 関数内で 1 回だけリテラルを代入した名前 / リテラルの並びを回す
        ループ変数 / モジュールの定数）。求められなければ None。"""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return {expr.value}
        if not isinstance(expr, ast.Name):
            return None
        node = self.unit.node if self.unit is not None else None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            vals: set[str] = set()
            literal_targets = 0
            stores = 0
            for n in self._iter_own_nodes(node.body):
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == expr.id for t in n.targets):
                    if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                        vals.add(n.value.value)
                        literal_targets += 1
                    else:
                        return None
                elif isinstance(n, (ast.For, ast.AsyncFor)) and isinstance(n.target, ast.Name) and n.target.id == expr.id:
                    it = n.iter
                    if isinstance(it, (ast.Tuple, ast.List, ast.Set)) and all(
                        isinstance(x, ast.Constant) and isinstance(x.value, str) for x in it.elts
                    ):
                        vals.update(x.value for x in it.elts)  # type: ignore[union-attr]
                        literal_targets += 1
                    else:
                        return None
                elif isinstance(n, ast.Name) and n.id == expr.id and isinstance(n.ctx, (ast.Store, ast.Del)):
                    stores += 1
            args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            if expr.id in args:
                return None
            if literal_targets and stores == literal_targets:
                return vals
        r = self.p.ref_of_expr(self.info, expr, None)
        if r is not None and r.kind == "const" and not r.alts and r.chain:
            info = self.p.get(r.module)
            bl = info.bindings.get(r.chain[0], []) if info is not None else []
            if len(bl) == 1 and isinstance(bl[0].node, ast.Constant) and isinstance(bl[0].node.value, str):
                return {bl[0].node.value}
        return None

    def _unreassigned_param(self, expr: ast.expr) -> tuple[int, str] | None:
        """expr が囲む関数の（代入し直されない）引数なら (位置, 名前)。"""
        if not isinstance(expr, ast.Name) or self.unit is None or self.unit.qualname == "<module>":
            return None
        node = self.unit.node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None
        params = [a.arg for a in node.args.posonlyargs + node.args.args]
        kwonly = [a.arg for a in node.args.kwonlyargs]
        if expr.id not in params and expr.id not in kwonly:
            return None
        for n in self._iter_own_nodes(node.body):
            if isinstance(n, ast.Name) and n.id == expr.id and isinstance(n.ctx, (ast.Store, ast.Del)):
                return None
            if isinstance(n, (ast.Global, ast.Nonlocal)) and expr.id in n.names:
                return None
        return (params.index(expr.id) if expr.id in params else -1, expr.id)

    def _literal_name(self, expr: ast.expr, sc) -> str | None:
        """getattr の名前: 文字列リテラル、または定数に解決できる属性（self._axis_name 等）。"""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return expr.value
        # self.ATTR / cls.ATTR → 囲むクラスのクラス定数（仕様 4 節: 定数伝播による絞り込み）
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name) and expr.value.id in ("self", "cls"):
            assert self.unit is not None
            if self.unit.cls:
                ci = self.info.classes.get(self.unit.cls)
                if ci is not None:
                    found = self.p._class_attr_lookup(self.info, ci, expr.attr)
                    if found is not None and found[2] == "attr":
                        blist = found[3]
                        if len(blist) == 1 and blist[0].kind == "literal" and not blist[0].conditional \
                                and not self.p._class_attr_stored(found[0], expr.attr):
                            try:
                                v = ast.literal_eval(blist[0].node)  # type: ignore[arg-type]
                            except Exception:  # noqa: BLE001
                                v = None
                            if isinstance(v, str):
                                return v
            return None
        r = self.p.ref_of_expr(self.info, expr, sc)
        if r is not None and r.kind == "const" and not r.alts:
            info = self.p.get(r.module)
            if info is not None and r.chain:
                bl = info.bindings.get(r.chain[0], [])
                if bl and bl[0].node is not None:
                    try:
                        v = ast.literal_eval(bl[0].node)
                    except Exception:  # noqa: BLE001
                        v = None
                    if isinstance(v, str):
                        return v
        return None

    def _dynamic_import(self, e: ast.Call, sc) -> None:
        """定数でない import_module / __import__（仕様 4 節: 解析不能）。"""
        arg = e.args[0] if e.args else None
        prefix = _const_prefix(arg) if arg is not None else ""
        package = None
        for k in e.keywords:
            if k.arg == "package":
                package = k.value
        if len(e.args) >= 2 and package is None:
            package = e.args[1]
        relative = prefix.startswith(".") or (package is not None)
        self._ev(Event("dynamic_import", name=prefix, lineno=e.lineno, ctx="relative" if relative else ""))
        for a in e.args:
            self._expr(a, "operand", sc)


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
    dynamic_modules: set[tuple[str, str]] = field(default_factory=set)
    unanalyzable: list[tuple[str, str, int]] = field(default_factory=list)  # (module, what, line)
    presence_checked: set[str] = field(default_factory=set)
    entry_point_modules: set[str] = field(default_factory=set)  # entry_points() で読み込まれうるモジュール
    entry_point_calls: set[tuple[str, str]] = field(default_factory=set)  # (呼び出し元モジュール, 対象モジュール)  # 'X' in sys.modules / find_spec("X") の X
    build_roots: list[str] = field(default_factory=list)
    why: dict[tuple[str, str], tuple] = field(default_factory=dict)
    # モジュール → そのモジュールの到達コードが import するモジュール
    module_imports: dict[str, set[str]] = field(default_factory=dict)


_DYNAMIC_DISPATCH_BASES: frozenset[str] = frozenset({
    "NodeVisitor", "NodeTransformer", "Cmd", "BaseHTTPRequestHandler",
    "SimpleHTTPRequestHandler", "CGIHTTPRequestHandler", "TestCase", "IsolatedAsyncioTestCase",
    "SimpleXMLRPCServer", "SimpleXMLRPCRequestHandler", "SimpleXMLRPCDispatcher",
    "BaseRequestHandler", "StreamRequestHandler", "DatagramRequestHandler",
    "HTMLParser", "ContentHandler", "Dispatcher",
})

_BASE_NAMES_CACHE: dict[tuple[str, tuple[str, ...]], frozenset[str] | None] = {}


def stdlib_object(module: str, chain: tuple[str, ...]):
    """stdlib / 組み込みのオブジェクトを取り出す（失敗なら例外）。"""
    import importlib

    obj = builtins if module == "builtins" else importlib.import_module(module)
    for c in chain:
        obj = getattr(obj, c)
    return obj


def _stdlib_class_names(module: str, chain: tuple[str, ...]) -> frozenset[str] | None:
    key = (module, chain)
    if key in _BASE_NAMES_CACHE:
        return _BASE_NAMES_CACHE[key]
    result: frozenset[str] | None = None
    try:
        if is_external_module(module):
            obj = stdlib_object(module, chain)
            if isinstance(obj, type):
                if obj.__name__ in _DYNAMIC_DISPATCH_BASES:
                    result = None
                else:
                    result = frozenset(dir(obj))
    except Exception:  # noqa: BLE001
        result = None
    _BASE_NAMES_CACHE[key] = result
    return result


def _unwrap_base(b: ast.expr) -> ast.expr:
    """`Base[T]` → `Base`（Generic[T] 等の添字つき基底）。"""
    while isinstance(b, ast.Subscript):
        b = b.value
    return b


def framework_hook_names(program: Program, ci: ClassInfo) -> frozenset[str] | None:
    """解析外の基底クラスが名前で呼びうるメソッド名（None = 全メソッド）。"""
    info = program.get(ci.module)
    if info is None:
        return None
    names: set[str] = set()
    for b0 in ci.bases:
        b = _unwrap_base(b0)
        r = program.ref_of_expr(info, b, None)
        if r is not None and r.kind == "class" and not r.alts:
            continue
        if r is None:
            return None
        if r.kind == "external" and r.chain:
            got = _stdlib_class_names(r.module, r.chain)
        else:
            return None
        if got is None:
            return None
        names |= got
    return frozenset(names)


# コマンド（実行ファイル）用の群: プログラムから entry_points() で読み込むものではない
_SCRIPT_GROUPS = frozenset({"console_scripts", "gui_scripts", "pyinstaller40", "distutils.commands",
                            "distutils.setup_keywords", "setuptools.finalize_distribution_options"})


def _read_entry_points(resolver: ModuleResolver, group: str = "") -> list[tuple[str, str]]:
    """site-packages の dist-info/entry_points.txt から (module, attr) を集める（群の指定があればその群だけ）。"""
    out: list[tuple[str, str]] = []
    for sp in resolver.site_packages:
        try:
            for d in sp.glob("*.dist-info"):
                f = d / "entry_points.txt"
                if not f.is_file():
                    continue
                cur = ""
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    ls = line.strip()
                    if ls.startswith("[") and ls.endswith("]"):
                        cur = ls[1:-1].strip()
                        continue
                    if group and cur != group:
                        continue
                    if not group and cur in _SCRIPT_GROUPS:
                        continue
                    if "=" not in line or ls.startswith("#"):
                        continue
                    val = line.split("=", 1)[1].strip().split("[")[0].strip()
                    mod, _, attr = val.partition(":")
                    if mod:
                        out.append((mod.strip(), attr.strip()))
        except OSError:
            continue
    return out


def compute_reachability(
    program: Program, entry_module: str, build_roots: list[str] | None = None,
    graph_modules: list[str] | None = None,
) -> Reachability:
    """エントリーポイント（と、ビルド時に実行されるモジュール）から到達可能なコード単位を求める。"""
    st = Reachability()
    st.build_roots = list(build_roots or [])
    graph_mods = list(graph_modules or [])
    work: deque[tuple[str, str]] = deque()
    current: list = [(None, "entry")]
    pending_guard: dict[str, list[tuple[Unit, Event]]] = {}

    def reach(key: tuple[str, str], reason: str = "") -> None:
        if key in st.reached:
            return
        if key not in program.units:
            if key[1] == "<module>" or program.function_unit(*key) is None:
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
        if "." in name:
            load_module(name.rsplit(".", 1)[0])
        program.build_module_unit(info)
        reach((name, "<module>"))
        for g in [g for g in pending_guard if g == name.split(".")[0]]:
            for unit, ev in pending_guard.pop(g):
                handle(unit, ev)

    def class_info(module: str, qual: str) -> ClassInfo | None:
        info = program.get(module)
        return info.classes.get(qual) if info is not None else None

    methods_by_name: dict[str, list[tuple[str, str]]] = {}
    dyn_by_class: dict[tuple[str, str], set[str]] = {}
    family_roots: set[tuple[str, str]] = set()
    mro_cache: dict[tuple[str, str], frozenset[tuple[str, str]]] = {}
    classes_seen: list[tuple[str, str]] = []

    def mro_of(module: str, qual: str, depth: int = 0) -> frozenset[tuple[str, str]]:
        key = (module, qual)
        if key in mro_cache:
            return mro_cache[key]
        mro_cache[key] = frozenset({key})
        out = {key}
        # 深い継承でも再帰が深くならないよう反復で辿る
        stack = [key]
        while stack:
            m, q = stack.pop()
            ci = class_info(m, q)
            info = program.get(m)
            if ci is None or info is None:
                continue
            for b in ci.bases:
                r = program.ref_of_expr(info, _unwrap_base(b), None)
                for rr in (r.all_refs() if r is not None else ()):
                    if rr.kind == "class" and (rr.module, rr.target) not in out:
                        out.add((rr.module, rr.target))
                        stack.append((rr.module, rr.target))
        mro_cache[key] = frozenset(out)
        return mro_cache[key]

    def instantiate(module: str, qual: str) -> None:
        stack = [(module, qual)]
        while stack:
            module_, qual_ = stack.pop()
            key = (module_, qual_)
            if key in st.instantiated:
                continue
            st.instantiated.add(key)
            ci = class_info(module_, qual_)
            if ci is None:
                continue
            for mname, quals in ci.methods.items():
                for uq in quals:
                    methods_by_name.setdefault(mname, []).append((module_, uq))
            info = program.get(module_)
            hooks = framework_hook_names(program, ci) if info is not None else frozenset()
            dyn_prefixes = [pfx for (m, pfx) in st.dynamic_modules if m == module_]
            for anc in mro_of(module_, qual_):
                dyn_prefixes.extend(dyn_by_class.get(anc, ()))
            all_methods = hooks is None
            for mname, quals in ci.methods.items():
                if (
                    all_methods
                    or (mname.startswith("__") and mname.endswith("__"))
                    or mname in ci.properties
                    or mname in st.called_names
                    or (mname in st.attr_names)
                    or (hooks is not None and mname in hooks)
                    or any(mname.startswith(pfx) for pfx in dyn_prefixes)
                ):
                    for uq in quals:
                        reach((module_, uq), f"method {mname}")
            if info is not None:
                for b in ci.bases:
                    r = program.ref_of_expr(info, _unwrap_base(b), None)
                    for rr in (r.all_refs() if r is not None else ()):
                        if rr.kind == "class" and (rr.module, rr.target) not in st.instantiated:
                            load_module(rr.module)
                            stack.append((rr.module, rr.target))

    def on_method_name(name: str, attr_only: bool) -> None:
        target = st.attr_names if attr_only else st.called_names
        if name in target:
            return
        target.add(name)
        for key in methods_by_name.get(name, ()):
            reach(key, f"name {name}")

    def check_class_defined(module: str, qual: str) -> None:
        """定義された時点でインスタンス化されうるクラス（__init_subclass__ / metaclass / クラス族）。"""
        classes_seen.append((module, qual))
        ci = class_info(module, qual)
        if ci is None:
            return
        anc = mro_of(module, qual) - {(module, qual)}
        if any(r in family_roots for r in mro_of(module, qual)):
            instantiate(module, qual)
            return
        for a in anc:
            aci = class_info(*a)
            if aci is not None and "__init_subclass__" in aci.methods:
                instantiate(module, qual)
                for uq in aci.methods["__init_subclass__"]:
                    reach((a[0], uq), "__init_subclass__")
                return
        info = program.get(module)
        for k in ci.keywords:
            if k.arg == "metaclass" and info is not None:
                r = program.ref_of_expr(info, k.value, None)
                if r is not None and r.kind == "class":
                    instantiate(r.module, r.target)
                    instantiate(module, qual)
                    return

    def add_family(module: str, qual: str) -> None:
        if (module, qual) in family_roots:
            return
        family_roots.add((module, qual))
        instantiate(module, qual)
        for m, q in list(classes_seen):
            if (module, qual) in mro_of(m, q):
                instantiate(m, q)

    def load_graph_modules(prefix: str) -> None:
        for m in graph_mods:
            if under(m, prefix):
                load_module(m)

    def handle(unit: Unit, ev: Event) -> None:
        if ev.guard and ev.guard not in {m.split(".")[0] for m in st.loaded_modules}:
            pending_guard.setdefault(ev.guard, []).append((unit, ev))
            return
        imports = st.module_imports.setdefault(unit.module, set())
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
                        sub = program.submodule(info, n)
                        if sub:
                            imports.add(sub)
                            load_module(sub)
            return
        if ev.kind == "method":
            on_method_name(ev.name, attr_only=False)
            return
        if ev.kind == "attr":
            on_method_name(ev.name, attr_only=True)
            return
        if ev.kind == "classdef":
            check_class_defined(unit.module, ev.target)
            return
        if ev.kind == "family":
            add_family(unit.module, ev.target)
            return
        if ev.kind == "subclasses":
            for rr in (ev.ref.all_refs() if ev.ref is not None else ()):
                if rr.kind == "class":
                    add_family(rr.module, rr.target)
            if ev.ref is None:
                st.unanalyzable.append((unit.module, "__subclasses__", ev.lineno))
            return
        if ev.kind == "entry_points":
            for mod, attr in _read_entry_points(program.resolver, ev.name):
                st.entry_point_modules.add(mod)
                st.entry_point_calls.add((unit.module, mod))
                load_module(mod)
                if attr:
                    r = program.resolve(mod, tuple(attr.split(".")))
                    for rr in r.all_refs():
                        if rr.kind in ("function", "method"):
                            reach((rr.module, rr.target), "entry point")
                        elif rr.kind == "class":
                            instantiate(rr.module, rr.target)
            return
        if ev.kind == "presence_check":
            st.presence_checked.add(ev.name)
            return
        if ev.kind == "unanalyzable":
            st.unanalyzable.append((unit.module, ev.name, ev.lineno))
            return
        if ev.kind == "dynamic_import":
            # 相対・package 指定・定数の接頭辞があれば、その範囲のグラフ上のモジュールを全て読み込む
            top = unit.module.split(".")[0]
            if ev.name and not ev.name.startswith("."):
                load_graph_modules(ev.name.rstrip("."))
            elif unit.module in program.local_modules:
                st.unanalyzable.append((unit.module, "dynamic import", ev.lineno))
            else:
                # ライブラリ内の遅延 import は、そのライブラリ自身のサブモジュールを対象とみなす（仕様 4 節）
                load_graph_modules(top)
            return
        if ev.kind == "dynamic":
            prefix = ev.name
            ref = ev.ref
            if ref is not None and ref.kind == "module":
                info = program.get(ref.module)
                if info is not None:
                    load_module(ref.module)
                    for q in list(info.functions):
                        if "." not in q and q.split("#")[0].startswith(prefix):
                            reach((ref.module, q), "getattr(module)")
                    for q in list(info.classes):
                        if "." not in q and q.startswith(prefix):
                            instantiate(ref.module, q)
            elif ev.ctx == "self" and unit.cls:
                ckey = (unit.module, unit.cls)
                pf = dyn_by_class.setdefault(ckey, set())
                if prefix not in pf:
                    pf.add(prefix)
                    for inst in list(st.instantiated):
                        if ckey in mro_of(*inst):
                            ci = class_info(*inst)
                            if ci:
                                for mname, quals in ci.methods.items():
                                    if mname.startswith(prefix):
                                        for uq in quals:
                                            reach((inst[0], uq), "getattr(self)")
            else:
                dkey = (unit.module, prefix)
                if dkey not in st.dynamic_modules:
                    st.dynamic_modules.add(dkey)
                    for module, qual in list(st.instantiated):
                        ci = class_info(module, qual)
                        if ci and (module == unit.module or qual == unit.cls):
                            for mname, quals in ci.methods.items():
                                if mname.startswith(prefix):
                                    for uq in quals:
                                        reach((module, uq), "getattr")
                    if not prefix:
                        on_method_name("*dynamic*", attr_only=False)
            return
        ref = ev.ref
        if ref is None:
            return
        for rr in ref.all_refs():
            if rr.alts:
                continue
            if rr.kind in ("function", "method"):
                if ev.ctx in _REACH_CTX:
                    reach((rr.module, rr.target))
            elif rr.kind == "class":
                if ev.ctx in ("call", "decorator", "arg", "store", "return", "raise", "unknown", "operand",
                              "subscript", "iter", "with"):
                    load_module(rr.module)
                    instantiate(rr.module, rr.target)
            elif rr.kind == "instance":
                load_module(rr.module)
                instantiate(rr.module, rr.target)
            elif rr.kind in ("derived", "classattr", "unknown", "opaque") and ev.ctx in (
                "call", "decorator", "arg", "store", "return"
            ):
                if rr.chain:
                    last = rr.chain[-1]
                    if last not in ("()", "[]"):
                        on_method_name(last, attr_only=False)

    deferred: list[tuple[Unit, Event]] = []
    applied_names: dict[int, set[str]] = {}
    call_index: dict[str, list[ast.Call]] = {}
    indexed_modules: set = set()

    def index_calls() -> None:
        wanted = {u.qualname.split(".")[-1].split("#")[0] for u, _ in deferred}
        wanted_b = [w.encode() for w in wanted]
        for m in list(st.loaded_modules):
            if (m, frozenset(wanted)) in indexed_modules:
                continue
            indexed_modules.add((m, frozenset(wanted)))
            info = program.get(m)
            if info is None or info.tree is None or not info.path:
                continue
            try:
                data = Path(info.path).read_bytes()
            except OSError:
                continue
            if not any(w in data for w in wanted_b):
                continue
            for n in ast.walk(info.tree):
                if isinstance(n, ast.Call):
                    f = n.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    if nm in wanted and n not in call_index.setdefault(nm, []):
                        call_index[nm].append(n)

    def literal_args(unit: Unit, ev: Event) -> set[str] | None:
        """関数 unit の引数（ev.target = "位置:名前"）に、全呼び出し側が渡す文字列リテラル。1 つでも不明なら None。"""
        pos_s, _, pname = ev.target.partition(":")
        pos = int(pos_s)
        fname = unit.qualname.split(".")[-1].split("#")[0]
        is_method = bool(unit.cls)
        out: set[str] = set()
        calls = call_index.get(fname, [])
        if not calls:
            return None
        for c in calls:
            if any(isinstance(a, ast.Starred) for a in c.args) or any(k.arg is None for k in c.keywords):
                return None
            val = None
            for k in c.keywords:
                if k.arg == pname:
                    val = k.value
            if val is None and pos >= 0:
                idx = pos - (1 if is_method and isinstance(c.func, ast.Attribute) else 0)
                if 0 <= idx < len(c.args):
                    val = c.args[idx]
            if val is None:
                return None  # 既定値（静的に見ない）
            if not (isinstance(val, ast.Constant) and isinstance(val.value, str)):
                return None
            out.add(val.value)
        return out

    def run_deferred() -> bool:
        index_calls()
        progressed = False
        for unit, ev in list(deferred):
            names = literal_args(unit, ev)
            done = applied_names.setdefault(id(ev), set())
            if names is None:
                if "*" in done:
                    continue
                done.add("*")
                handle(unit, replace(ev, kind="dynamic", name=""))
                progressed = True
                continue
            new = names - done
            if not new:
                continue
            done |= new
            progressed = True
            for n in sorted(new):
                if ev.ctx == "self" and unit.cls:
                    # self の属性: クラス族のメソッドで、その名前のもの
                    ckey = (unit.module, unit.cls)
                    for inst in list(st.instantiated):
                        if ckey in mro_of(*inst):
                            ci = class_info(*inst)
                            if ci and n in ci.methods:
                                for uq in ci.methods[n]:
                                    reach((inst[0], uq), f"getattr {n}")
                    on_method_name(n, attr_only=False)
                else:
                    on_method_name(n, attr_only=False)
        return progressed

    for m in [entry_module] + list(build_roots or []):
        load_module(m)

    while True:
        while work:
            key = work.popleft()
            unit = program.units[key]
            current[0] = (key, "")
            for qual in unit.escaped_units:
                reach((unit.module, qual), "escaped (decorator)")
            for ev in unit.events:
                current[0] = (key, ev)
                if ev.kind == "dynamic_param":
                    deferred.append((unit, ev))
                    continue
                handle(unit, ev)
        if not deferred or not run_deferred():
            break
        if not work:
            # 新しい呼び出し側が増えていなければ終わり
            if not run_deferred():
                break
    return st
