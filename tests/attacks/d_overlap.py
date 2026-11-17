"""Overlap: inner submodule stub applied BEFORE whole-package stub (reverse of the tested order)."""
import shutil, subprocess, sys, json, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from hc import write_tree, EXT
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
bash = shutil.which("bash")
import os as _os
root = Path(_os.environ.get("PYSTUB_ATTACK_RUNS") or (Path(__file__).resolve().parent / "runs")) / "D_overlap"
if root.exists(): shutil.rmtree(root)
sp_files = {"cx/__init__.py": "def native():\n    return 1\n", f"cx/_core{EXT}": b"fake",
            "pk/__init__.py": "def f():\n    return 'f'\n", "pk/big.py": "X = 1\n", "pk/io/__init__.py": "",
            "pk/io/x.py": "def g():\n    import cx\n    return cx.native()\n"}
site = root / "sp"; write_tree(site, sp_files)
write_tree(root / "proj", {"main.py": "import pk, pk.io.x\nprint('ok')\n", "pyproject.toml": ""})
entry = str(root / "proj" / "main.py")
def tree():
    return {str(f.relative_to(site)): hashlib.md5(f.read_bytes()).hexdigest() for f in sorted(site.rglob("*")) if f.is_file() and "__pycache__" not in f.parts}
before = tree()
gp = generate_stubs(entry, "pk", python_path=str(site)); gs = generate_submodule_stubs(entry, "pk", "pk.io.x", python_path=str(site))
stage = root / "stage"
for g in (gp, gs):
    for rel, code in g["files"].items():
        f = stage / "_stubs" / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(code, encoding="utf-8")
def sh(lines, tag):
    r = subprocess.run([bash, "-c", "\n".join(lines)], cwd=stage, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(f"  {tag}: rc={r.returncode} {r.stdout.strip()[:200]} {r.stderr.strip()[:200]}")
def m1(bi):
    out = []
    for l in bi["uninstall_commands"]:
        if "方法2" in l: break
        out.append(l)
    return out
def state(tag):
    t = tree(); lost = sorted(set(before) - set(t)); changed = sorted(k for k in before if k in t and t[k] != before[k])
    bak = root / "_pystub_bak"
    print(f"[{tag}] lost={lost} changed={changed} bak_exists={bak.exists()} bak_files={[str(p.relative_to(bak)) for p in bak.rglob('*') if p.is_file()] if bak.exists() else []}")
print("== order: submodule(apply) -> package(apply) -> package(restore) -> submodule(restore)")
for g, n in ((gs, "sub"), (gp, "pkg")):
    sh(g["build_instructions"]["backup_commands"], n + " backup"); sh(g["build_instructions"]["install_commands"], n + " install")
state("after apply")
for g, n in ((gp, "pkg"), (gs, "sub")):
    sh(m1(g["build_instructions"]), n + " restore")
state("after restore")
print("verify_commands:", gp["build_instructions"]["verify_commands"])
for c in gp["build_instructions"]["verify_commands"] + gs["build_instructions"]["verify_commands"]:
    c2 = c.replace("python -c", f'"{sys.executable}" -c', 1)
    r = subprocess.run([bash, "-c", c2], cwd=str(site), capture_output=True, text=True)
    print("  ", c[:80], "->", r.returncode, r.stdout.strip(), r.stderr.strip()[-120:])
