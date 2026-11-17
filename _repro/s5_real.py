import json, shutil, subprocess, sys, time, textwrap
from pathlib import Path
from harness import analyze, generate_stubs, inspect_graph, check_usage, BASE

SITE = ""
root = BASE / "S40_real"
if root.exists():
    shutil.rmtree(root)
proj = root / "proj"
proj.mkdir(parents=True)
(proj / "pyproject.toml").write_text("[project]\nname='real'\n")
(proj / "main.py").write_text(textwrap.dedent("""
    import requests
    import numpy as np
    from PIL import Image
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import yaml
    import pandas as pd

    def fetch(url):
        return requests.get(url, timeout=1)

    def as_frame(x):
        return isinstance(x, pd.DataFrame)

    def main():
        a = np.arange(5)
        img = Image.new("RGB", (4, 4))
        plt.figure(); plt.plot(a)
        print("ok", int(a.sum()), img.size, yaml.safe_dump({"a": 1}).strip(), as_frame(a))

    if __name__ == "__main__":
        main()
"""), encoding="utf-8")
ep = str(proj / "main.py")


def run_with_overlay(overlay):
    code = f"import sys; sys.path.insert(0, {str(overlay)!r}); sys.path.insert(0, {str(proj)!r}); import runpy; runpy.run_path({ep!r}, run_name='__main__')"
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, timeout=300)
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, " | ".join(lines[-3:])


print("original:", run_with_overlay(root / "none"))
for depth in (10,):
    t = time.time()
    r = analyze(ep, python_path=SITE, max_depth=depth)
    print(f"analyze depth={depth}: {time.time()-t:.1f}s total={r['total_packages_analyzed']}")
    for k in ("stubbable", "nofollow", "required"):
        print(f"  {k}:", sorted(p["package_name"] for p in r[k]))
    print("  hints:", [(h["submodule"], h["target_package"]) for h in r.get("submodule_stub_hints", [])][:20])
    print("  #warnings:", len(r["warnings"]))
    for w in r["warnings"][:8]:
        print("    ", w[:160])
    (root / "analyze.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

for pkg in sorted(p["package_name"] for p in r["stubbable"]):
    t = time.time()
    try:
        g = generate_stubs(ep, pkg, python_path=SITE)
    except Exception as e:
        print(f"[{pkg}] generate EXCEPTION {type(e).__name__}: {e}")
        continue
    if "error" in g:
        print(f"[{pkg}] generate error: {g['error']}")
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
    print(f"[{pkg}] files={g['stub_file_count']} syntax_err={len(bad)} {bad[:2]} run rc={rc} {out[:300]!r} ({time.time()-t:.1f}s)")
