"""Continue S40: generate + run stubs for stubbable packages listed in analyze.json (skip ones already done)."""
import json, subprocess, sys, time
from pathlib import Path
from harness import generate_stubs, BASE

root = BASE / "S40_real"
proj = root / "proj"
ep = str(proj / "main.py")
r = json.loads((root / "analyze.json").read_text(encoding="utf-8"))
done = set(sys.argv[1:])
pkgs = [p["package_name"] for p in r["stubbable"] if p["package_name"] not in done]


def run_with_overlay(overlay):
    code = f"import sys; sys.path.insert(0, {str(overlay)!r}); sys.path.insert(0, {str(proj)!r}); import runpy; runpy.run_path({ep!r}, run_name='__main__')"
    rr = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, timeout=300)
    lines = (rr.stdout + rr.stderr).strip().splitlines()
    return rr.returncode, " | ".join(lines[-2:])


for pkg in pkgs:
    t = time.time()
    try:
        g = generate_stubs(ep, pkg)
    except Exception as e:
        print(f"[{pkg}] generate EXCEPTION {type(e).__name__}: {e}", flush=True)
        continue
    if "error" in g:
        print(f"[{pkg}] generate error: {g['error']}", flush=True)
        continue
    overlay = root / f"overlay_{pkg}"
    bad = []
    for rel, code in g["files"].items():
        try:
            compile(code, rel, "exec")
        except SyntaxError as e:
            bad.append((rel, str(e)))
        p = overlay / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")
    rc, out = run_with_overlay(overlay)
    print(f"[{pkg}] files={g['stub_file_count']} syntax_err={len(bad)} {bad[:2]} run rc={rc} {out[:300]!r} ({time.time()-t:.1f}s)", flush=True)
