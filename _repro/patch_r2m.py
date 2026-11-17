"""round2 修正 m（速度）: 変更の検出を遅延・キャッシュ、定数判定のキャッシュ、呼び出し索引の事前絞り込み。"""
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
    # ModuleInfo: global 名・変更検出は遅延
    ("""    mutated: set[str] = field(default_factory=set)  # 変更されうる名前（global / 属性・添字への代入 / メソッド呼び出し）
    attr_stores: set[str] = field(default_factory=set)  # `x.NAME = ...` で書き込まれる属性名""",
     """    mutated: set[str] = field(default_factory=set)  # 変更されうる名前（global / 属性・添字への代入 / メソッド呼び出し）
    attr_stores: set[str] = field(default_factory=set)  # `x.NAME = ...` で書き込まれる属性名
    global_names: set[str] = field(default_factory=set)  # 関数内で global 宣言された名前
    mutations_done: bool = False"""),
    ("""        self._collect_bindings(info, tree.body, info.bindings, "", True, False)
        self._collect_mutations(info)""",
     """        self._collect_bindings(info, tree.body, info.bindings, "", True, False)"""),
    ("""    def _collect_mutations(self, info: ModuleInfo) -> None:
        tree = info.tree
        assert tree is not None
        for n in ast.walk(tree):
            if isinstance(n, ast.Global):
                info.mutated.update(n.names)""",
     """    def _collect_mutations(self, info: ModuleInfo) -> None:
        if info.mutations_done or info.tree is None:
            return
        info.mutations_done = True
        tree = info.tree
        for n in ast.walk(tree):
            if isinstance(n, ast.Global):
                info.mutated.update(n.names)
                info.global_names.update(n.names)"""),
    ("""        self._plain_cache: dict[tuple[str, str], bool] = {}
        self.local_modules: set[str] = set()""",
     """        self._plain_cache: dict[tuple[str, str], bool] = {}
        self.local_modules: set[str] = set()
        self._const_cache: dict[tuple[str, str], bool] = {}
        self._stores_cache: dict[str, tuple[int, set[str]]] = {}"""),
    ("""    def binding_is_const(self, info: ModuleInfo, name: str, blist: list[Binding]) -> bool:
        \"\"\"無条件のトップレベルで 1 回だけ束縛され、変更されないリテラルか（仕様 2 節 A / 指示 1）。\"\"\"
        if len(blist) != 1:
            return False
        b = blist[0]
        if b.kind != "literal" or b.conditional or literal_source(b.node) is None:
            return False
        if name in info.mutated and _is_mutable_literal(b.node):
            return False
        if name in info.mutated and any(
            isinstance(n, ast.Global) and name in n.names for n in ast.walk(info.tree)  # type: ignore[arg-type]
        ):
            return False
        top = info.name.split(".")[0]
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top:
                continue
            if name in other.attr_stores or "*" in other.attr_stores:
                return False
        return True""",
     """    def package_attr_stores(self, top: str) -> set[str]:
        \"\"\"パッケージ top の（読み込んだ）モジュールで `x.NAME = ...` と書き込まれる属性名。\"\"\"
        n = len(self.modules)
        cached = self._stores_cache.get(top)
        if cached is not None and cached[0] == n:
            return cached[1]
        out: set[str] = set()
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top:
                continue
            self._collect_mutations(other)
            out |= other.attr_stores
        self._stores_cache[top] = (n, out)
        return out

    def binding_is_const(self, info: ModuleInfo, name: str, blist: list[Binding]) -> bool:
        \"\"\"無条件のトップレベルで 1 回だけ束縛され、変更されないリテラルか（仕様 2 節 A / 指示 1）。\"\"\"
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
        if ok:
            stores = self.package_attr_stores(info.name.split(".")[0])
            if name in stores or "*" in stores:
                ok = False
        self._const_cache[key] = ok
        self._const_cache[("#n", "")] = len(self.modules)  # type: ignore[assignment]
        return ok"""),
    ("""    def _class_attr_stored(self, info: ModuleInfo, attr: str) -> bool:
        top = info.name.split(".")[0]
        for m, other in list(self.modules.items()):
            if other is None or other.opaque or m.split(".")[0] != top:
                continue
            if attr in other.attr_stores or "*" in other.attr_stores:
                return True
        return False""",
     """    def _class_attr_stored(self, info: ModuleInfo, attr: str) -> bool:
        stores = self.package_attr_stores(info.name.split(".")[0])
        return attr in stores or "*" in stores"""),
    # 呼び出し索引: 名前の文字列を含むファイルだけ歩く
    ("""    def index_calls() -> None:
        for m in list(st.loaded_modules):
            if m in indexed_modules:
                continue
            indexed_modules.add(m)
            info = program.get(m)
            if info is None or info.tree is None:
                continue
            for n in ast.walk(info.tree):""",
     """    def index_calls() -> None:
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
            for n in ast.walk(info.tree):"""),
    ("""                    if nm:
                        call_index.setdefault(nm, []).append(n)""",
     """                    if nm in wanted and n not in call_index.setdefault(nm, []):
                        call_index[nm].append(n)"""),
    ("""    indexed_modules: set[str] = set()""",
     """    indexed_modules: set = set()"""),
])
print("patched")
