"""round2 修正 d: instantiate を反復に（深い継承で RecursionError にしない）。"""
from pathlib import Path

R = Path(__file__).resolve().parent.parent / "mcp_pystub"
p = R / "semantics.py"
s = p.read_text(encoding="utf-8")
start = s.index("    def instantiate(module: str, qual: str) -> None:")
end = s.index("    def on_method_name(name: str, attr_only: bool) -> None:")
new = '''    def instantiate(module: str, qual: str) -> None:
        stack = [(module, qual)]
        while stack:
            module_, qual_ = stack.pop()
            key = (module_, qual_)
            if key in st.instantiated:
                continue
            st.instantiated.add(key)
            ci = class_info(module_, qual_)
            if ci is None:
                continue
            for mname, quals in ci.methods.items():
                for uq in quals:
                    methods_by_name.setdefault(mname, []).append((module_, uq))
            info = program.get(module_)
            hooks = framework_hook_names(program, ci) if info is not None else frozenset()
            dyn_prefixes = [pfx for (m, pfx) in st.dynamic_modules if m == module_]
            for anc in mro_of(module_, qual_):
                dyn_prefixes.extend(dyn_by_class.get(anc, ()))
            all_methods = hooks is None
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
                        reach((module_, uq), f"method {mname}")
            if info is not None:
                for b in ci.bases:
                    r = program.ref_of_expr(info, _unwrap_base(b), None)
                    for rr in (r.all_refs() if r is not None else ()):
                        if rr.kind == "class" and (rr.module, rr.target) not in st.instantiated:
                            load_module(rr.module)
                            stack.append((rr.module, rr.target))

'''
s = s[:start] + new + s[end:]
p.write_text(s, encoding="utf-8")
print("patched")
