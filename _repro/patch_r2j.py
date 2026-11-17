"""round2 修正 j: entry_points(group=変数) の群を定数伝播で求める（複数可。求められなければ全群）。"""
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
    ('''            grp = ""
            for k in e.keywords:
                if k.arg == "group" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                    grp = k.value.value
            if fname.endswith("iter_entry_points") and e.args and isinstance(e.args[0], ast.Constant) \\
                    and isinstance(e.args[0].value, str):
                grp = e.args[0].value
            self._ev(Event("entry_points", name=grp, lineno=e.lineno))''',
     '''            gexpr = None
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
                    self._ev(Event("entry_points", name=grp, lineno=e.lineno))'''),
    ('''    def _unreassigned_param(self, expr: ast.expr) -> tuple[int, str] | None:''',
     '''    def _string_values(self, expr: ast.expr) -> set[str] | None:
        """式が取りうる文字列（リテラル / 関数内で 1 回だけリテラルを代入した名前 / リテラルの並びを回す
        ループ変数 / モジュールの定数）。求められなければ None。"""
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            return {expr.value}
        if not isinstance(expr, ast.Name):
            return None
        node = self.unit.node if self.unit is not None else None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            vals: set[str] = set()
            found = 0
            for n in self._iter_own_nodes(node.body):
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == expr.id for t in n.targets):
                    found += 1
                    if isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                        vals.add(n.value.value)
                    else:
                        return None
                elif isinstance(n, (ast.For, ast.AsyncFor)) and isinstance(n.target, ast.Name) and n.target.id == expr.id:
                    found += 1
                    it = n.iter
                    if isinstance(it, (ast.Tuple, ast.List, ast.Set)) and all(
                        isinstance(x, ast.Constant) and isinstance(x.value, str) for x in it.elts
                    ):
                        vals.update(x.value for x in it.elts)  # type: ignore[union-attr]
                    else:
                        return None
                elif isinstance(n, ast.Name) and n.id == expr.id and isinstance(n.ctx, (ast.Store, ast.Del)):
                    found += 100  # その他の束縛（引数・with・内包など）
            args = [a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs]
            if expr.id in args:
                return None
            if found and found < 100:
                return vals
        r = self.p.ref_of_expr(self.info, expr, None)
        if r is not None and r.kind == "const" and not r.alts and r.chain:
            info = self.p.get(r.module)
            bl = info.bindings.get(r.chain[0], []) if info is not None else []
            if len(bl) == 1 and isinstance(bl[0].node, ast.Constant) and isinstance(bl[0].node.value, str):
                return {bl[0].node.value}
        return None

    def _unreassigned_param(self, expr: ast.expr) -> tuple[int, str] | None:'''),
])
print("patched")
