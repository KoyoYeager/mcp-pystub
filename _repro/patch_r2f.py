"""round2 修正 f: 注釈の文脈を式の中まで引き継ぐ / getattr の名前が引数なら呼び出し側のリテラルに絞る。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"


def patch(fname, pairs):
    p = R / fname
    s = p.read_text(encoding="utf-8")
    for a, b in pairs:
        if a not in s:
            raise SystemExit(f"NOT FOUND in {fname}: {a[:90]!r}")
        s = s.replace(a, b, 1)
    p.write_text(s, encoding="utf-8")


patch("semantics.py", [
    # 注釈: `X | None` / `Optional[X]` / `Literal["a"]` などの中も注釈
    ('''        # BoolOp / BinOp / UnaryOp / JoinedStr / FormattedValue / Slice など
        for child in ast.iter_child_nodes(e):
            if isinstance(child, ast.expr):
                ev(child, "operand", sc)
''', '''        # BoolOp / BinOp / UnaryOp / JoinedStr / FormattedValue / Slice など
        inner = "annot" if ctx == "annot" else "operand"
        for child in ast.iter_child_nodes(e):
            if isinstance(child, ast.expr):
                ev(child, inner, sc)
'''),
    ('''            elif fname == "getattr":
                on_self = isinstance(e.args[0], ast.Name) and e.args[0].id in ("self", "cls")
                ev(e.args[0], "unknown", sc)
                self._ev(Event("dynamic", ref=base_ref, name=_const_prefix(e.args[1]), lineno=e.lineno,
                               ctx="self" if on_self else ""))''',
     '''            elif fname == "getattr":
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
                                   ctx="self" if on_self else ""))'''),
    ('''    def _literal_name(self, expr: ast.expr, sc) -> str | None:''',
     '''    def _unreassigned_param(self, expr: ast.expr) -> tuple[int, str] | None:
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

    def _literal_name(self, expr: ast.expr, sc) -> str | None:'''),
    # 到達解析: dynamic_param は作業が尽きてから、読み込んだ全モジュールの呼び出し側を見て解く
    ('''    for m in [entry_module] + list(build_roots or []):
        load_module(m)
''', '''    deferred: list[tuple[Unit, Event]] = []
    applied_names: dict[int, set[str]] = {}
    call_index: dict[str, list[ast.Call]] = {}
    indexed_modules: set[str] = set()

    def index_calls() -> None:
        for m in list(st.loaded_modules):
            if m in indexed_modules:
                continue
            indexed_modules.add(m)
            info = program.get(m)
            if info is None or info.tree is None:
                continue
            for n in ast.walk(info.tree):
                if isinstance(n, ast.Call):
                    f = n.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    if nm:
                        call_index.setdefault(nm, []).append(n)

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
'''),
    ('''    while work:
        key = work.popleft()
        unit = program.units[key]
        current[0] = (key, "")
        for qual in unit.escaped_units:
            reach((unit.module, qual), "escaped (decorator)")
        for ev in unit.events:
            current[0] = (key, ev)
            handle(unit, ev)
    return st''', '''    while True:
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
    return st'''),
])
print("patched")
