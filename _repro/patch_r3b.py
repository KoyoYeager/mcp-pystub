"""round3 修正 b: handle の各イベントで辺の印を決める / 確実に実行される集合を求める関数。"""
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
    ('''    def handle(unit: Unit, ev: Event) -> None:
        if ev.guard and ev.guard not in {m.split(".")[0] for m in st.loaded_modules}:''',
     '''    def handle(unit: Unit, ev: Event) -> None:
        saved = tag[0]
        tag[0] = "branch" if ev.cond else "precise"
        try:
            _handle(unit, ev)
        finally:
            tag[0] = saved

    def _handle(unit: Unit, ev: Event) -> None:
        if ev.guard and ev.guard not in {m.split(".")[0] for m in st.loaded_modules}:'''),
    ('''        if ev.kind == "method":
            on_method_name(ev.name, attr_only=False)
            return
        if ev.kind == "attr":
            on_method_name(ev.name, attr_only=True)
            return''',
     '''        if ev.kind == "method":
            if ev.ctx != "self":
                tag[0] = "name"  # 受け手が分からない: 名前だけの振り分け
            on_method_name(ev.name, attr_only=False)
            return
        if ev.kind == "attr":
            if ev.ctx != "self":
                tag[0] = "name"
            on_method_name(ev.name, attr_only=True)
            return'''),
    ('''            elif rr.kind == "class":
                if ev.ctx in ("call", "decorator", "arg", "store", "return", "raise", "unknown", "operand",
                              "subscript", "iter", "with"):
                    load_module(rr.module)
                    instantiate(rr.module, rr.target)''',
     '''            elif rr.kind == "class":
                if ev.ctx in ("call", "decorator", "arg", "store", "return", "raise", "unknown", "operand",
                              "subscript", "iter", "with"):
                    load_module(rr.module)
                    if ev.ctx not in ("call", "decorator", "raise"):
                        saved_t = tag[0]
                        tag[0] = "argclass"  # 値として渡したクラス: 生成されると仮定
                        instantiate(rr.module, rr.target)
                        tag[0] = saved_t
                    else:
                        instantiate(rr.module, rr.target)'''),
    ('''                if rr.chain:
                    last = rr.chain[-1]
                    if last not in ("()", "[]"):
                        on_method_name(last, attr_only=False)

    deferred: list[tuple[Unit, Event]] = []''',
     '''                if rr.chain:
                    last = rr.chain[-1]
                    if last not in ("()", "[]"):
                        saved_t = tag[0]
                        tag[0] = "name"
                        on_method_name(last, attr_only=False)
                        tag[0] = saved_t

    deferred: list[tuple[Unit, Event]] = []'''),
    ('''            new = names - done
            if not new:
                continue
            done |= new
            progressed = True
            for n in sorted(new):''',
     '''            new = names - done
            if not new:
                continue
            done |= new
            progressed = True
            current[0] = (unit.key, ev)
            tag[0] = "name"
            for n in sorted(new):'''),
    ('''        for qual in unit.escaped_units:
                reach((unit.module, qual), "escaped (decorator)")''',
     '''        for qual in unit.escaped_units:
                tag[0] = "decorator"  # 登録デコレータ（関数を保持して後で呼びうる）
                reach((unit.module, qual), "escaped (decorator)")
                tag[0] = "precise"'''),
    ('''    for m in [entry_module] + list(build_roots or []):
        load_module(m)
''',
     '''    for m in [entry_module] + list(build_roots or []):
        st.roots.append((m, "<module>"))
        load_module(m)
'''),
])

# 確実に実行される集合と、過大近似の入口
with open(R / "semantics.py", "a", encoding="utf-8") as f:
    f.write('''

# ---------------------------------------------------------------------------
# 確実に実行されるコード（仕様 9 節: 条件付き候補の区別）
# ---------------------------------------------------------------------------


def certain_units(st: Reachability) -> set[tuple[str, str]]:
    """起点（エントリー・ビルド時の起点）から、確実な辺（precise）だけで届くコード単位。"""
    adj: dict = {}
    for src, dst, t, _ev in st.edges:
        if t == "precise":
            adj.setdefault(src, []).append(dst)
    seen: set = set()
    q = deque(k for k in st.roots if k in st.reached)
    # 親パッケージの本体など、起点が読み込むモジュール本体は辺（src=None）で入る
    for src, dst, t, _ev in st.edges:
        if src is None and t == "precise":
            q.append(dst)
    while q:
        k = q.popleft()
        if k in seen:
            continue
        seen.add(k)
        q.extend(adj.get(k, ()))
    return seen


def entry_paths(st: Reachability, certain: set) -> dict:
    """確実な集合の外のユニット → (入口のユニット, 入口へ入る辺の印, 入口へ入る辺のイベント, 確実側の呼び出し元)。

    確実な集合から、全ての辺で幅優先に辿り、最初に確実な集合を出た辺を「入口」とする。
    """
    adj: dict = {}
    for src, dst, t, ev in st.edges:
        adj.setdefault(src, []).append((dst, t, ev))
    out: dict = {}
    q: deque = deque()
    for k in certain:
        for dst, t, ev in adj.get(k, ()):
            if dst not in certain and dst not in out:
                out[dst] = (dst, t, ev, k)
                q.append(dst)
    while q:
        k = q.popleft()
        info = out[k]
        for dst, t, ev in adj.get(k, ()):
            if dst not in certain and dst not in out:
                out[dst] = info
                q.append(dst)
    return out
''')
print("patched part 2")
