"""実パッケージ通し試験（修正後）: analyze 1 回 + stubbable 全件の generate とスタブ差し替え実行。

generate_stubs は毎回グラフを作り直すため、_prepare の結果（グラフ・意味解析）を使い回す
（生成ロジックそのものは同じ関数を通す）。
"""
import json, shutil, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub import stub_generator
from mcp_pystub.analyzer import analyze

root = Path(__file__).resolve().parent / "runs" / "S40_real"
proj = root / "proj"
ep = str(proj / "main.py")


def run_with_overlay(overlay):
    code = (f"import sys; sys.path.insert(0, {str(overlay)!r}); sys.path.insert(0, {str(proj)!r}); "
            f"import runpy; runpy.run_path({ep!r}, run_name='__main__')")
    r = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True,
                       timeout=300, encoding="utf-8", errors="replace")
    lines = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode, " | ".join(lines[-2:])


print("original:", run_with_overlay(root / "none"), flush=True)
t = time.time()
r = analyze(ep)
print(f"analyze: {time.time()-t:.1f}s total={r['total_packages_analyzed']}", flush=True)
for k in ("stubbable", "nofollow", "required"):
    print(f"  {k} ({len(r[k])}):", sorted(p["package_name"] for p in r[k]), flush=True)
print("  hints:", [(h["submodule"], h["target_package"]) for h in r.get("submodule_stub_hints", [])], flush=True)
(root / "analyze_after.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

prepared, err = stub_generator._prepare(ep, "", 10)
stub_generator._prepare = lambda *a, **k: (prepared, None)

for pkg in sorted(p["package_name"] for p in r["stubbable"]):
    t = time.time()
    try:
        g = stub_generator.generate_stubs(ep, pkg)
    except Exception as e:  # noqa: BLE001
        print(f"[{pkg}] generate EXCEPTION {type(e).__name__}: {e}", flush=True)
        continue
    if "error" in g:
        print(f"[{pkg}] generate error: {g['error']}", flush=True)
        continue
    overlay = root / f"overlay2_{pkg}"
    if overlay.exists():
        shutil.rmtree(overlay)
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
    print(f"[{pkg}] files={g['stub_file_count']} bytes={g['stub_total_bytes']} syntax_err={len(bad)} run rc={rc} {out[:240]!r} ({time.time()-t:.1f}s)", flush=True)
print("done", flush=True)
