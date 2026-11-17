import sys, os, json, shutil, subprocess, textwrap
from pathlib import Path
REPO = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, REPO)
from mcp_pystub.analyzer import analyze, check_usage, inspect_graph  # noqa
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa

BASE = Path(__file__).resolve().parent / "runs"


def write_tree(root, files):
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(code, bytes):
            p.write_bytes(code)
        else:
            p.write_text(textwrap.dedent(code), encoding="utf-8")


def run_py(paths, entry):
    code = ("import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')" % (paths, entry))
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60)
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, " | ".join(out[-3:])


def setup(name, sp_files, proj_files):
    root = BASE / name
    if root.exists():
        shutil.rmtree(root)
    sp = root / "sp"
    proj = root / "proj"
    sp.mkdir(parents=True)
    proj.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n")
    write_tree(sp, sp_files)
    write_tree(proj, proj_files)
    return root, sp, proj


def apply_stub(root, sp, files, tag, remove=None):
    ssp = root / f"stubsp_{tag}"
    if ssp.exists():
        shutil.rmtree(ssp)
    shutil.copytree(sp, ssp)
    if remove:
        t = ssp / remove
        if t.is_dir():
            shutil.rmtree(t)
        elif t.exists():
            t.unlink()
    bad = []
    for rel, code in files.items():
        try:
            compile(code, rel, "exec")
        except SyntaxError as e:
            bad.append((rel, str(e)))
        p = ssp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
    return ssp, bad


def scenario(name, sp_files, proj_files, entry="main.py", stub_pkgs=None, verbose=False):
    root, sp, proj = setup(name, sp_files, proj_files)
    ep = str(proj / entry)
    print(f"===== {name}")
    rc, out = run_py([str(proj), str(sp)], ep)
    print(f"  [original run] rc={rc} {out!r}")
    res = analyze(ep, python_path=str(sp))
    verdicts = {}
    for k in ("stubbable", "nofollow", "required"):
        for p in res[k]:
            verdicts[p["package_name"]] = k
            print(f"  {k:10s} {p['package_name']}: {p['reason'][:100]}")
    for h in res.get("submodule_stub_hints", []):
        print("  hint:", h["submodule"], "->", h["target_package"], h["imported_symbols"])
    if verbose:
        print("  warnings:", res["warnings"])
    targets = stub_pkgs if stub_pkgs is not None else [p for p, v in verdicts.items() if v == "stubbable"]
    for pkg in targets:
        g = generate_stubs(ep, pkg, python_path=str(sp))
        if "error" in g:
            print("  generate error:", g["error"])
            continue
        ssp, bad = apply_stub(root, sp, g["files"], pkg, remove=pkg if (sp / pkg).is_dir() else f"{pkg}.py")
        if bad:
            print("  SYNTAX ERRORS in stub:", bad)
        if verbose:
            for rel, code in g["files"].items():
                print("   ---", rel)
                print(textwrap.indent(code, "     "))
        rc, out = run_py([str(proj), str(ssp)], ep)
        print(f"  [stub {pkg} run] rc={rc} {out!r}")
    return res, root
