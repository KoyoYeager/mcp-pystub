"""round3 修正 g: 条件付き候補のスタブは、踏んだら分かる例外で大きく落ちる（仕様 9 節）。"""
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


patch("stub_generator.py", [
    ('''_NET_KNOWN = """_PYSTUB_KNOWN = {known}''',
     '''# 条件付き候補のスタブ: 呼ばれたら「mcp-pystub のスタブ」と分かる例外で落ちる
_LOUD_HEADER = """# mcp-pystub stub (conditional): {module}
class PystubConditionalStubError(RuntimeError):
    pass
_PYSTUB_NOTE = {note!r}
def _raise(n):
    raise PystubConditionalStubError("mcp-pystub のスタブです。" + _PYSTUB_NOTE + "（呼ばれたもの: " + n + "）")
class _M(type):
    def __getattr__(c, n):
        k = c.__dict__.get("_pystub_known")
        if n[:2] != "__" and (k is None or n in k):
            return _M(n, (), {{"_pystub_known": ()}})
        raise AttributeError(n)
    def __call__(c, *a, **k): _raise(c.__name__)
    __getitem__ = lambda c, i: c
def _g(n):
    return _M(n, (), {{"_pystub_known": ()}})
"""

_NET_KNOWN = """_PYSTUB_KNOWN = {known}'''),
    ('''    def __init__(self, sem: Semantics, prefix: str, version: str | None) -> None:
        self.sem = sem
        self.p: Program = sem.program
        self.prefix = prefix
        self.version = version''',
     '''    def __init__(self, sem: Semantics, prefix: str, version: str | None, loud_note: str = "") -> None:
        self.sem = sem
        self.p: Program = sem.program
        self.prefix = prefix
        self.version = version
        self.loud_note = loud_note  # 条件付き候補: 呼ばれたら出す説明'''),
    ('''        header = _HEADER.format(module=module)''',
     '''        header = _LOUD_HEADER.format(module=module, note=self.loud_note) if self.loud_note else \\
            _HEADER.format(module=module)'''),
    ('''                defs.append(f"def {name}(*a, **k): raise RuntimeError({(module + '.' + name)!r})\\n")
                return
            else:''',
     '''                defs.append(self._func_stub(name, module + "." + name))
                return
            else:'''),
    ('''        if kind in ("function", "method"):
            defs.append(f"def {name}(*a, **k): raise RuntimeError({(module + '.' + name)!r})\\n")
            return''',
     '''        if kind in ("function", "method"):
            defs.append(self._func_stub(name, module + "." + name))
            return'''),
    ('''                body.append(f"    @staticmethod\\n    def {attr}(*a, **k): raise RuntimeError({(name + '.' + attr)!r})")''',
     '''                if self.loud_note:
                    body.append(f"    @staticmethod\\n    def {attr}(*a, **k): _raise({(name + '.' + attr)!r})")
                else:
                    body.append(f"    @staticmethod\\n    def {attr}(*a, **k): raise RuntimeError({(name + '.' + attr)!r})")'''),
    ('''    # --- クラス ---''',
     '''    def _func_stub(self, name: str, qual: str) -> str:
        if self.loud_note:
            return f"def {name}(*a, **k): _raise({qual!r})\\n"
        return f"def {name}(*a, **k): raise RuntimeError({qual!r})\\n"

    # --- クラス ---'''),
    ('''    builder = _StubBuilder(sem, package_name, version)''',
     '''    note, conditional_info = _conditional_note(graph, sem, package_name, is_package=True)
    builder = _StubBuilder(sem, package_name, version, loud_note=note)'''),
    ('''    builder = _StubBuilder(sem, submodule, None)''',
     '''    note, conditional_info = _conditional_note(graph, sem, submodule, is_package=False)
    builder = _StubBuilder(sem, submodule, None, loud_note=note)'''),
    ('''def _usage_warnings(sem: Semantics, prefix: str) -> list[str]:''',
     '''def _conditional_note(graph, sem: Semantics, prefix: str, is_package: bool) -> tuple[str, dict | None]:
    """条件付き候補なら、スタブが落ちる時に出す説明（「<prefix> は <条件> を使わない前提」）。"""
    from mcp_pystub.usage_analyzer import analyze_packages, build_conditions, is_certain_use

    info = None
    if is_package:
        a = analyze_packages(graph, {prefix})
        if a and a[0].verdict == "conditional":
            info = {"conditions": a[0].conditions, "total": a[0].conditions_total}
    else:
        uses = prefix_usage(sem, prefix).uses
        if uses and not any(is_certain_use(sem, u, ev) for u, ev, _ in uses):
            info = build_conditions(sem, uses, prefix)
    if not info:
        return "", None
    ents = []
    for g in info["conditions"][:3]:
        api = g.get("public_api") or ""
        ents.append((api + " → " if api and api != g["entry"] else "") + g["entry"])
    more = f" ほか {info['total'] - 3} 件" if info["total"] > 3 else ""
    note = f"{prefix} は、次の機能を使わない前提で外されています: " + "、".join(ents) + more
    return note, info


def _usage_warnings(sem: Semantics, prefix: str) -> list[str]:'''),
])
print("patched")
