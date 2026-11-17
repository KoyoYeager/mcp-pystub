"""round2 修正 k: _string_values の数え方 / entry_points は importlib.metadata 系に解決できるものだけ。"""
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
    ('''        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
                return vals''',
     '''        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
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
                return vals'''),
    ('''        elif fname in (".entry_points", "entry_points", ".iter_entry_points", "iter_entry_points"):''',
     '''        elif fname in (".entry_points", "entry_points", ".iter_entry_points", "iter_entry_points") and \\
                self._is_metadata_entry_points(func, sc):'''),
    ('''    def _string_values(self, expr: ast.expr) -> set[str] | None:''',
     '''    def _is_metadata_entry_points(self, func: ast.expr, sc) -> bool:
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

    def _string_values(self, expr: ast.expr) -> set[str] | None:'''),
])
print("patched")
