"""round2 修正 n（速度）: import 抽出は文だけを辿り、動的 import の文字列があるファイルだけ式も辿る。"""
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


patch("import_extractor.py", [
    ('''class _ImportVisitor(ast.NodeVisitor):
    """AST を走査して import 文を収集する NodeVisitor。"""

    def __init__(self, source_file: str) -> None:
        self.source_file = source_file''',
     '''_STMT_FIELDS = ("body", "orelse", "finalbody", "handlers", "cases")


class _ImportVisitor(ast.NodeVisitor):
    """AST を走査して import 文を収集する NodeVisitor。"""

    def __init__(self, source_file: str, exprs: bool = True) -> None:
        self.source_file = source_file
        self._exprs = exprs  # 式の中（importlib.import_module 等）も辿るか'''),
    ('''    @property
    def _context(self) -> str:
        return self._context_stack[-1]''',
     '''    def generic_visit(self, node: ast.AST) -> None:
        if self._exprs:
            super().generic_visit(node)
            return
        # 文の並びだけを辿る（import 文は式の中に現れない）
        for f in _STMT_FIELDS:
            v = getattr(node, f, None)
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, ast.AST):
                        self.visit(item)

    @property
    def _context(self) -> str:
        return self._context_stack[-1]'''),
    ('''    visitor = _ImportVisitor(source_file=str(path.resolve()))''',
     '''    try:
        data = path.read_bytes()
        exprs = b"import_module" in data or b"__import__" in data
    except OSError:
        exprs = True
    visitor = _ImportVisitor(source_file=str(path.resolve()), exprs=exprs)'''),
])
print("patched")
