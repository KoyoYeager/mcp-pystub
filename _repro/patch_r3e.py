"""round3 修正 e: entry_points(group=引数) を呼び出し側のリテラルで解く / 真偽の判定は ImportError の代替を除いて判断。"""
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
    # entry_points の群が引数なら、呼び出し側のリテラルを後で求める
    ('''            groups = self._string_values(gexpr) if gexpr is not None else None
            if groups is None:
                self._ev(Event("entry_points", name="", lineno=e.lineno))''',
     '''            groups = self._string_values(gexpr) if gexpr is not None else None
            param = self._unreassigned_param(gexpr) if (groups is None and gexpr is not None) else None
            if param is not None and self.unit is not None:
                self._ev(Event("dynamic_param", name=self.unit.qualname, lineno=e.lineno, ctx="entry_points",
                               target=f"{param[0]}:{param[1]}"))
            elif groups is None:
                self._ev(Event("entry_points", name="", lineno=e.lineno))'''),
    ('''            if names is None:
                if "*" in done:
                    continue
                done.add("*")
                handle(unit, replace(ev, kind="dynamic", name=""))
                progressed = True
                continue''',
     '''            if names is None:
                if "*" in done:
                    continue
                done.add("*")
                if ev.ctx == "entry_points":
                    handle(unit, replace(ev, kind="entry_points", name="", ctx=""))
                else:
                    handle(unit, replace(ev, kind="dynamic", name=""))
                progressed = True
                continue'''),
    ('''            current[0] = (unit.key, ev)
            tag[0] = "name"
            for n in sorted(new):''',
     '''            current[0] = (unit.key, ev)
            if ev.ctx == "entry_points":
                for n in sorted(new):
                    handle(unit, replace(ev, kind="entry_points", name=n, ctx=""))
                continue
            tag[0] = "name"
            for n in sorted(new):'''),
    # 真偽の判定の文脈
    ('''        elif isinstance(stmt, ast.If):
            ev(stmt.test, "operand", sc)''',
     '''        elif isinstance(stmt, ast.If):
            ev(stmt.test, "truth", sc)'''),
    ('''        elif isinstance(stmt, ast.While):
            ev(stmt.test, "operand", sc)''',
     '''        elif isinstance(stmt, ast.While):
            ev(stmt.test, "truth", sc)'''),
    ('''        if isinstance(e, ast.IfExp):
            ev(e.test, "operand", sc)''',
     '''        if isinstance(e, ast.IfExp):
            ev(e.test, "truth", sc)'''),
    ('''        if isinstance(e, ast.BoolOp):
            ev(e.values[0], "operand", sc)
            self.branch += 1  # 2 番目以降は短絡評価で実行されないことがある
            for x in e.values[1:]:
                ev(x, "operand", sc)
            self.branch -= 1
            return''',
     '''        if isinstance(e, ast.BoolOp):
            inner = "truth" if ctx == "truth" else "operand"
            ev(e.values[0], inner, sc)
            self.branch += 1  # 2 番目以降は短絡評価で実行されないことがある
            for x in e.values[1:]:
                ev(x, inner, sc)
            self.branch -= 1
            return
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            ev(e.operand, "truth", sc)
            return'''),
    # ImportError の代替の束縛に印
    ('''    qualname: str = ""
    conditional: bool = False


@dataclass
class ClassInfo:''',
     '''    qualname: str = ""
    conditional: bool = False
    fallback: bool = False  # `except ImportError:` の中の束縛（元の環境では import が成功するので実行されない）


@dataclass
class ClassInfo:'''),
    ('''            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module, True)
                for h in stmt.handlers:
                    if h.name:
                        self._add(out, h.name, Binding("unknown", conditional=True))
                    self._collect_bindings(info, h.body, out, qual_prefix, is_module, True)''',
     '''            elif isinstance(stmt, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                self._collect_bindings(info, stmt.body, out, qual_prefix, is_module, True)
                import_fallback = handlers_catch_import_error(stmt.handlers) and all(
                    isinstance(s, (ast.Import, ast.ImportFrom, ast.Pass)) for s in stmt.body
                )
                for h in stmt.handlers:
                    if h.name:
                        self._add(out, h.name, Binding("unknown", conditional=True))
                    before = {k: len(v) for k, v in out.items()}
                    self._collect_bindings(info, h.body, out, qual_prefix, is_module, True)
                    if import_fallback:
                        for k, v in out.items():
                            for b in v[before.get(k, 0):]:
                                b.fallback = True'''),
    # Ref に代替の印
    ('''    marks: tuple[tuple[int, tuple[str, ...]], ...] = ()
    alts: tuple["Ref", ...] = ()''',
     '''    marks: tuple[tuple[int, tuple[str, ...]], ...] = ()
    alts: tuple["Ref", ...] = ()
    fallback: bool = False  # except ImportError の代替の束縛から来た'''),
    ('''        alts = tuple(self._resolve_binding(info, head, blist, b, rest, trace, depth) for b in blist)''',
     '''        alts = tuple(
            replace(self._resolve_binding(info, head, blist, b, rest, trace, depth), fallback=True) if b.fallback
            else self._resolve_binding(info, head, blist, b, rest, trace, depth)
            for b in blist
        )'''),
])

patch("usage_analyzer.py", [
    ('''    if ctx == "is":
        return kind not in ("class", "function", "method", "module")
    return True  # C. 実行（未知の文脈も含めて既定は使用）''',
     '''    if ctx == "is":
        return kind not in ("class", "function", "method", "module")
    if ctx == "truth":
        # 真偽の判定: クラス・関数・モジュールは元でもスタブでも常に真
        return kind not in ("class", "function", "method", "module", "external_module")
    return True  # C. 実行（未知の文脈も含めて既定は使用）'''),
    ('''    for rr in ref.all_refs():
        if rr.alts or not rr.touches(prefix):
            continue
        if _leaf_is_use(rr, ev.ctx):
            return "use"
    return "ref"''',
     '''    for rr in ref.all_refs():
        if rr.alts or not rr.touches(prefix):
            continue
        if rr.fallback and ev.ctx == "truth":
            continue  # except ImportError の代替: 元の環境（パッケージがある）では実行されない
        if _leaf_is_use(rr, ev.ctx):
            return "use"
    return "ref"'''),
])
print("patched")
