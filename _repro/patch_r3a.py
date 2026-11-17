"""round3 修正 a: 分岐・広い except の印、到達の辺に「確実 / 過大近似の種類」の印、確実に実行される集合。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"


def patch(fname, pairs):
    p = R / fname
    s = p.read_text(encoding="utf-8")
    for a, b in pairs:
        if s.count(a) != 1:
            raise SystemExit(f"NOT FOUND/AMBIGUOUS ({s.count(a)}) in {fname}: {a[:90]!r}")
        s = s.replace(a, b, 1)
    p.write_text(s, encoding="utf-8")


patch("semantics.py", [
    # Event に印
    ('''    guard: str = ""  # `if 'X' not in sys.modules: return` の後なら X
    target: str = ""  # classdef / family: クラス qualname''',
     '''    guard: str = ""  # `if 'X' not in sys.modules: return` の後なら X
    target: str = ""  # classdef / family: クラス qualname
    cond: bool = False  # 関数の中の if / except / 条件式の枝（入力・呼び出し次第で実行される）
    broad: bool = False  # `except Exception` / `except:` で囲まれている（例外が握りつぶされうる）'''),
    # 歩く側の状態
    ('''    def __init__(self, program: Program, info: ModuleInfo) -> None:
        self.p = program
        self.info = info
        self.guard = ""
        self.unit: Unit | None = None''',
     '''    def __init__(self, program: Program, info: ModuleInfo) -> None:
        self.p = program
        self.info = info
        self.guard = ""
        self.unit: Unit | None = None
        self.branch = 0  # 関数の中の分岐の深さ
        self.broad = 0  # 広い except の try 本体の深さ'''),
    ('''    def _ev(self, ev: Event) -> None:
        if self.guard and not ev.guard:
            ev.guard = self.guard''',
     '''    def _ev(self, ev: Event) -> None:
        if self.guard and not ev.guard:
            ev.guard = self.guard
        if self.branch and self.unit is not None and self.unit.qualname != "<module>":
            ev.cond = True
        if self.broad:
            ev.broad = True'''),
    # if の枝
    ('''        elif isinstance(stmt, ast.If):
            ev(stmt.test, "operand", sc)
            if is_type_checking_test(stmt.test):
                self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
                return
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)''',
     '''        elif isinstance(stmt, ast.If):
            ev(stmt.test, "operand", sc)
            if is_type_checking_test(stmt.test):
                self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
                return
            self.branch += 1
            self._walk_body(stmt.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
            self.branch -= 1'''),
    # try: 本体は実行される、except の枝は分岐、広い except なら本体に印
    ('''            for h in stmt.handlers:
                if h.type is not None:
                    ev(h.type, "except", sc)
                self._walk_body(h.body, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.finalbody, scope, cls_scope, protected, protect_ok)''',
     '''            for h in stmt.handlers:
                if h.type is not None:
                    ev(h.type, "except", sc)
                self.branch += 1
                self._walk_body(h.body, scope, cls_scope, protected, protect_ok)
                self.branch -= 1
            self._walk_body(stmt.orelse, scope, cls_scope, protected, protect_ok)
            self._walk_body(stmt.finalbody, scope, cls_scope, protected, protect_ok)'''),
    ('''            prot = handlers_catch_import_error(stmt.handlers)  # type: ignore[arg-type]
            simple = prot and self._simple_protection(stmt)
            self._walk_body(stmt.body, scope, cls_scope, protected or prot, (protect_ok or not protected) and simple
                            if prot else protect_ok)''',
     '''            prot = handlers_catch_import_error(stmt.handlers)  # type: ignore[arg-type]
            simple = prot and self._simple_protection(stmt)
            broad_h = any(
                h.type is None or (isinstance(h.type, ast.Name) and h.type.id in ("Exception", "BaseException"))
                or (isinstance(h.type, ast.Tuple) and any(isinstance(x, ast.Name) and x.id in ("Exception", "BaseException")
                                                           for x in h.type.elts))
                for h in stmt.handlers
            )
            self.broad += 1 if broad_h else 0
            self._walk_body(stmt.body, scope, cls_scope, protected or prot, (protect_ok or not protected) and simple
                            if prot else protect_ok)
            self.broad -= 1 if broad_h else 0'''),
    # match の case は分岐
    ('''                if case.guard is not None:
                    ev(case.guard, "operand", sc)
                self._walk_body(case.body, scope, cls_scope, protected, protect_ok)''',
     '''                if case.guard is not None:
                    ev(case.guard, "operand", sc)
                self.branch += 1
                self._walk_body(case.body, scope, cls_scope, protected, protect_ok)
                self.branch -= 1'''),
    # 条件式の枝
    ('''        if isinstance(e, ast.IfExp):
            ev(e.test, "operand", sc)
            inner = ctx if ctx in ("isinst", "except", "annot") else "store"
            ev(e.body, inner, sc)
            ev(e.orelse, inner, sc)
            return''',
     '''        if isinstance(e, ast.IfExp):
            ev(e.test, "operand", sc)
            inner = ctx if ctx in ("isinst", "except", "annot") else "store"
            self.branch += 1
            ev(e.body, inner, sc)
            ev(e.orelse, inner, sc)
            self.branch -= 1
            return
        if isinstance(e, ast.BoolOp):
            ev(e.values[0], "operand", sc)
            self.branch += 1  # 2 番目以降は短絡評価で実行されないことがある
            for x in e.values[1:]:
                ev(x, "operand", sc)
            self.branch -= 1
            return'''),
    # self / cls の属性呼び出しは自分のクラス（とその族）のメソッドの呼び出し（確実）
    ('''                if not self._receiver_is_builtin(e.value, sc):
                    kind = "method" if ctx in ("call", "decorator", "arg", "store", "return") else "attr"
                    self._ev(Event(kind, name=e.attr, lineno=e.lineno))''',
     '''                if not self._receiver_is_builtin(e.value, sc):
                    kind = "method" if ctx in ("call", "decorator", "arg", "store", "return") else "attr"
                    on_self = isinstance(e.value, ast.Name) and e.value.id in ("self", "cls") and \\
                        self.unit is not None and bool(self.unit.cls)
                    self._ev(Event(kind, name=e.attr, lineno=e.lineno, ctx="self" if on_self else ""))'''),
    # Reachability に辺と確実な集合
    ('''    why: dict[tuple[str, str], tuple] = field(default_factory=dict)''',
     '''    why: dict[tuple[str, str], tuple] = field(default_factory=dict)
    # 到達の辺: (呼び出し元, 到達先, 印, 辺を作ったイベント)。印は precise か、過大近似の種類
    # （name: 名前だけの振り分け / argclass: 引数に渡したクラス / decorator: 登録デコレータ / branch: 入力次第の分岐）
    edges: list[tuple] = field(default_factory=list)
    roots: list[tuple[str, str]] = field(default_factory=list)'''),
    ('''    def reach(key: tuple[str, str], reason: str = "") -> None:
        if key in st.reached:
            return
        if key not in program.units:
            if key[1] == "<module>" or program.function_unit(*key) is None:
                return
        st.reached.add(key)''',
     '''    tag = ["precise"]
    edge_seen: set = set()

    def reach(key: tuple[str, str], reason: str = "") -> None:
        if key in st.reached:
            src = current[0][0]
            ek = (src, key, tag[0])
            if ek not in edge_seen:
                edge_seen.add(ek)
                st.edges.append((src, key, tag[0], current[0][1]))
            return
        if key not in program.units:
            if key[1] == "<module>" or program.function_unit(*key) is None:
                return
        src = current[0][0]
        edge_seen.add((src, key, tag[0]))
        st.edges.append((src, key, tag[0], current[0][1]))
        st.reached.add(key)'''),
    ('''    def on_method_name(name: str, attr_only: bool) -> None:
        target = st.attr_names if attr_only else st.called_names
        if name in target:
            return
        target.add(name)
        for key in methods_by_name.get(name, ()):
            reach(key, f"name {name}")''',
     '''    seen_name_tags: set = set()

    def on_method_name(name: str, attr_only: bool) -> None:
        target = st.attr_names if attr_only else st.called_names
        nt = (name, attr_only, tag[0])
        if name in target and nt in seen_name_tags:
            return
        target.add(name)
        seen_name_tags.add(nt)
        for key in methods_by_name.get(name, ()):
            reach(key, f"name {name}")'''),
    # instantiate: 生成の印ごとに、メソッドへの辺を記録し直す（より確実な生成が後から来た場合）
    ('''    def instantiate(module: str, qual: str) -> None:
        stack = [(module, qual)]
        while stack:
            module_, qual_ = stack.pop()
            key = (module_, qual_)
            if key in st.instantiated:
                continue
            st.instantiated.add(key)''',
     '''    inst_tags: set = set()

    def instantiate(module: str, qual: str) -> None:
        stack = [(module, qual)]
        while stack:
            module_, qual_ = stack.pop()
            key = (module_, qual_)
            if (key, tag[0]) in inst_tags:
                continue
            inst_tags.add((key, tag[0]))
            first = key not in st.instantiated
            st.instantiated.add(key)'''),
    ('''            ci = class_info(module_, qual_)
            if ci is None:
                continue
            for mname, quals in ci.methods.items():
                for uq in quals:
                    methods_by_name.setdefault(mname, []).append((module_, uq))''',
     '''            ci = class_info(module_, qual_)
            if ci is None:
                continue
            if first:
                for mname, quals in ci.methods.items():
                    for uq in quals:
                        methods_by_name.setdefault(mname, []).append((module_, uq))'''),
    ('''            all_methods = hooks is None
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
                        reach((module_, uq), f"method {mname}")''',
     '''            all_methods = hooks is None
            for mname, quals in ci.methods.items():
                if (
                    all_methods
                    or (mname.startswith("__") and mname.endswith("__"))
                    or mname in ci.properties
                    or (hooks is not None and mname in hooks)
                    or any(mname.startswith(pfx) for pfx in dyn_prefixes)
                ):
                    for uq in quals:
                        reach((module_, uq), f"method {mname}")
                elif mname in st.called_names or mname in st.attr_names:
                    # 以前に名前だけで呼ばれた: 名前による振り分け
                    saved = tag[0]
                    tag[0] = "name" if saved == "precise" else saved
                    for uq in quals:
                        reach((module_, uq), f"name {mname}")
                    tag[0] = saved'''),
])
print("patched semantics part 1")
