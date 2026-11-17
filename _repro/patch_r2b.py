"""round2 修正 b: 外部呼び出しの結果・sys.modules[...]・from-import された stdlib 関数・有無の検出・スタブの欠名。"""
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


BS = "\\"

patch("semantics.py", [
    ("""        if b.kind in ("alias", "call") and b.node is not None:
            base = self.ref_of_expr(info, b.node, None, depth + 1, trace)
            if base is None:
                return Ref("unknown", mod, chain, trace)
            return self.extend(base, rest, depth + 1)""",
     """        if b.kind in ("alias", "call") and b.node is not None:
            base = self.ref_of_expr(info, b.node, None, depth + 1, trace)
            if base is None:
                return Ref("unknown", mod, chain, trace)
            if base.kind in ("external_value", "builtin_value", "derived"):
                # 呼び出しの結果の値はスタブで再現できない（定数ではない）
                return Ref("unknown", mod, chain, trace)
            return self.extend(base, rest, depth + 1)"""),
    ("""        if isinstance(expr, ast.Subscript):
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)""",
     """        if isinstance(expr, ast.Subscript):
            v = expr.value
            if isinstance(v, ast.Attribute) and v.attr == "modules" and isinstance(v.value, ast.Name) """ + BS + """
                    and v.value.id == "sys":
                sl = expr.slice
                if isinstance(sl, ast.Name) and sl.id == "__name__":
                    return self.resolve(info.name, (), trace, depth + 1)
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    return self.resolve(sl.value, (), trace, depth + 1)
            base = self.ref_of_expr(info, expr.value, scope, depth + 1, trace)"""),
    ("""        fname = ""
        if isinstance(func, ast.Name) and (sc is None or func.id not in sc) and func.id not in self.info.bindings:
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = "." + func.attr""",
     """        fname = ""
        if isinstance(func, ast.Name) and (sc is None or func.id not in sc) and func.id not in self.info.bindings:
            fname = func.id
        elif isinstance(func, ast.Attribute):
            fname = "." + func.attr
        elif isinstance(func, ast.Name):
            fr = self.p.ref_of_expr(self.info, func, sc)
            if fr is not None and not fr.alts and fr.kind == "external" and fr.chain:
                fname = fr.chain[-1]"""),
    ("""        if isinstance(e, ast.Compare):
            c = "is\"""",
     """        if isinstance(e, ast.Compare):
            if len(e.ops) == 1 and isinstance(e.ops[0], (ast.In, ast.NotIn)) and isinstance(e.left, ast.Constant) """ + BS + """
                    and isinstance(e.left.value, str):
                c0 = e.comparators[0]
                if isinstance(c0, ast.Attribute) and c0.attr == "modules" and isinstance(c0.value, ast.Name) """ + BS + """
                        and c0.value.id == "sys":
                    self._record(self.p.resolve(e.left.value, ()), "presence", e)
                    self._ev(Event("presence_check", name=e.left.value, lineno=e.lineno))
                    return
            c = "is\""""),
    ("""            self._record(self.p.resolve(e.args[0].value, ()), "presence", e)
""",
     """            self._record(self.p.resolve(e.args[0].value, ()), "presence", e)
            self._ev(Event("presence_check", name=e.args[0].value, lineno=e.lineno))
"""),
    ("""    unanalyzable: list[tuple[str, str, int]] = field(default_factory=list)  # (module, what, line)""",
     """    unanalyzable: list[tuple[str, str, int]] = field(default_factory=list)  # (module, what, line)
    presence_checked: set[str] = field(default_factory=set)  # 'X' in sys.modules / find_spec("X") の X"""),
    ("""        if ev.kind == "unanalyzable":""",
     """        if ev.kind == "presence_check":
            st.presence_checked.add(ev.name)
            return
        if ev.kind == "unanalyzable":"""),
])

patch("usage_analyzer.py", [
    ('''_ALWAYS_SAFE_KINDS = frozenset({
    "const", "constattr", "classconst", "external", "external_value", "external_module", "builtin_value",
})''', '''_ALWAYS_SAFE_KINDS = frozenset({
    "const", "constattr", "classconst", "external", "external_module",
})'''),
    ('''    # 解析不能（仕様 4 節）: プロジェクトのコードにある非定数の動的 import / exec / sys.modules[...]''',
     '''    # 有無の検出（仕様 3 節）: 'X' in sys.modules / find_spec("X") の X が、P をスタブにすると
    # 読み込まれなくなる（または P の一部）なら、検出結果が変わる
    if sem.reach.presence_checked:
        full = import_closure(sem)
        without = None
        for x in sorted(sem.reach.presence_checked):
            if under(x, package_name):
                changed = False  # スタブ化しても P 自体は（スタブとして）読み込まれる
            else:
                if without is None:
                    without = import_closure(sem, exclude_prefix=package_name)
                changed = x in full and x not in without
            if changed:
                return PackageAnalysis(
                    verdict="required",
                    reason=f"到達可能なコードがモジュール '{x}' の有無を調べており、{package_name} を"
                    "スタブにすると結果が変わります（安全側判定）。",
                    gateway_functions=gateways,
                    **common,
                )

    # 解析不能（仕様 4 節）: プロジェクトのコードにある非定数の動的 import / exec / sys.modules[...]'''),
])

patch("stub_generator.py", [
    ('''        r = self.p.resolve(module, (name,))
        if name == "__version__":''',
     '''        r = self.p.resolve(module, (name,))
        if r.kind == "missing" and not r.alts:
            return  # 元のモジュールに無い名前は出さない（hasattr / getattr の既定値を元と同じに: S13）
        if name == "__all__" and (info is None or info.all_names is None):
            return  # 定数でない __all__ は出さない（スターは出した公開名だけになる）
        if name == "__version__":'''),
])
print("patched")
