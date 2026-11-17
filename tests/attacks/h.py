"""Checker harness: original run -> analyze -> stub/nofollow/hint apply -> rerun, compare full output."""
import sys, os, json, shutil, subprocess, textwrap
from pathlib import Path
REPO = str(Path(__file__).resolve().parents[2])  # リポジトリの根
sys.path.insert(0, REPO)
from mcp_pystub.analyzer import analyze, check_usage  # noqa
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa

import os as _os
BASE = Path(_os.environ.get("PYSTUB_ATTACK_RUNS") or (Path(__file__).resolve().parent / "runs"))
RESULTS = []

def write_tree(root, files):
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(code), encoding="utf-8")

def run_py(paths, entry):
    code = ("import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')" % (paths, entry))
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()

def short(o):
    lines = o.splitlines()
    return " | ".join(lines[-2:])

def copy_site(sp, dst):
    if dst.exists(): shutil.rmtree(dst)
    shutil.copytree(sp, dst)

def rm(site, name):
    for t in (site.joinpath(*name.split(".")), site.joinpath(*name.split(".")).with_suffix(".py")):
        if t.is_dir(): shutil.rmtree(t)
        elif t.exists(): t.unlink()

def put(site, files):
    for rel, code in files.items():
        compile(code, rel, "exec")
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")

def scenario(name, sp_files, proj_files, entry="main.py", show_stub=False, expect=None):
    root = BASE / name
    if root.exists(): shutil.rmtree(root)
    sp, proj = root / "sp", root / "proj"
    sp.mkdir(parents=True); proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    write_tree(sp, sp_files); write_tree(proj, proj_files)
    ep = str(proj / entry)
    rc0, out0 = run_py([str(proj), str(sp)], ep)
    res = analyze(ep, python_path=str(sp))
    v = {p["package_name"]: (k, p["reason"]) for k in ("stubbable", "nofollow", "required") for p in res[k]}
    hints = res.get("submodule_stub_hints", [])
    print(f"===== {name}  orig rc={rc0} {short(out0)!r}")
    for p, (k, r) in sorted(v.items()):
        print(f"   {k:9s} {p}: {r[:150]}")
    broken = []
    stubbables = [p for p, (k, _) in v.items() if k == "stubbable"]
    for p in stubbables:
        g = generate_stubs(ep, p, python_path=str(sp))
        if "error" in g:
            print("   generate error", p, g["error"]); broken.append((p, "generror")); continue
        s = root / f"stub_{p}"; copy_site(sp, s); rm(s, p); put(s, g["files"])
        if show_stub:
            for rel, c in g["files"].items():
                print("     ---", rel); print(textwrap.indent(c.split("__getattr__ = _g\n",1)[-1], "       "))
        rc, out = run_py([str(proj), str(s)], ep)
        same = (rc == rc0 and out == out0)
        print(f"   [stub {p}] rc={rc} same={same} {short(out)!r}")
        if not same: broken.append((p, "stub", out))
    for p in [p for p, (k, _) in v.items() if k == "nofollow"]:
        s = root / f"nof_{p}"; copy_site(sp, s); rm(s, p)
        rc, out = run_py([str(proj), str(s)], ep)
        same = (rc == rc0 and out == out0)
        print(f"   [nofollow {p}] rc={rc} same={same} {short(out)!r}")
        if not same: broken.append((p, "nofollow", out))
    for h in hints:
        g = generate_submodule_stubs(ep, h["parent_package"], h["submodule"], python_path=str(sp))
        if "error" in g:
            print("   subgen error", g["error"]); continue
        s = root / f"hint_{h['submodule']}"; copy_site(sp, s); rm(s, h["submodule"]); put(s, g["files"]); rm(s, h["target_package"])
        rc, out = run_py([str(proj), str(s)], ep)
        same = (rc == rc0 and out == out0)
        print(f"   [hint {h['submodule']} -X {h['target_package']}] rc={rc} same={same} {short(out)!r}")
        if not same: broken.append((h["submodule"], "hint", out))
    RESULTS.append((name, rc0, broken))
    return res, root, v
