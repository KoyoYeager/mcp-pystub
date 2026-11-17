"""設計検証（C:\\tmp\\pystub-e2e\\designed）を今のコードで回す。結果は after_round4\\designed に書く（元の結果は上書きしない）。

1. proj / proj_b を analyze し、expect.json（proj）と「optaccel・heavyframe は required」（proj_b）と照らす
2. proj の証明済みの提案を全部適用して PyInstaller でビルド、exe の大きさと出力を比べ、復元してハッシュを比べる
"""
import hashlib, json, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze  # noqa: E402
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa: E402

D = Path(r"C:\tmp\pystub-e2e\designed")
OUT = Path(r"C:\tmp\pystub-e2e\after_round7\designed")
OUT.mkdir(parents=True, exist_ok=True)
SP = D / "venv/Lib/site-packages"
PY = D / "venv/Scripts/python.exe"
BASH = r"C:\Program Files\Git\bin\bash.exe"
expect = json.loads((D / "expect.json").read_text(encoding="utf-8"))
expected_out = (D / "expected.txt").read_text()


def verdicts(r):
    v = {}
    for k in ("stubbable", "nofollow", "required"):
        for a in r[k]:
            v[a["package_name"]] = k
    for c in r.get("conditional", []):
        v[c["package_name"]] = "conditional"
    hints = {h["target_package"]: h["submodule"] for h in r["submodule_stub_hints"]}
    chints = {h["target_package"]: h["submodule"] for h in r.get("conditional_submodule_hints", [])}
    return v, hints, chints


res = {}
ra = analyze(str(D / "proj/entry.py"), str(SP), include_small_conditional=True)
(OUT / "analyze.json").write_text(json.dumps(ra, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
v, hints, chints = verdicts(ra)
judge = {}
for pkg, want in expect.items():
    got = v.get(pkg)
    if want == "removable":
        ok = got in ("stubbable", "nofollow")
    elif want.startswith("removable_via:"):
        ok = hints.get(pkg) == want.split(":", 1)[1]
    else:
        ok = got == "required"
    judge[pkg] = {"expect": want, "got": got, "hint": hints.get(pkg), "cond_hint": chints.get(pkg), "ok": ok}
res["proj"] = judge
res["proj_summary"] = ra["summary"]
rb = analyze(str(D / "proj_b/entry.py"), str(SP), include_small_conditional=True)
(OUT / "analyze_b.json").write_text(json.dumps(rb, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
vb, _hb, _cb = verdicts(rb)
res["proj_b"] = {p: {"got": vb.get(p), "ok": vb.get(p) == "required"} for p in ("optaccel", "heavyframe")}
res["analyze_s"] = [ra["analysis_time_ms"] / 1000, rb["analysis_time_ms"] / 1000]
print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)

if "--build" in sys.argv:
    def snap():
        return {str(p.relative_to(SP)): hashlib.md5(p.read_bytes()).hexdigest()
                for p in SP.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}

    def sh(cmds, cwd):
        r = subprocess.run([BASH, "-c", "\n".join(cmds)], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout + r.stderr)[-500:]

    E = str(D / "proj/entry.py")
    gen = {}
    for p in [a["package_name"] for a in ra["stubbable"]]:
        gen[p] = generate_stubs(E, p, str(SP))
    for h in ra["submodule_stub_hints"]:
        gen[h["submodule"]] = generate_submodule_stubs(E, h["parent_package"], h["submodule"], str(SP))
    nof = [a["package_name"] for a in ra["nofollow"]]
    st = OUT / "apply"
    shutil.rmtree(st, ignore_errors=True)
    for k, g in gen.items():
        for f, c in g["files"].items():
            q = st / "_stubs" / f
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(c, encoding="utf-8", newline="\n")
    before = snap()
    log = {}
    for k, g in gen.items():
        bi = g["build_instructions"]
        log[k] = [sh(bi["backup_commands"], st), sh(bi["install_commands"], st)]
    try:
        src = subprocess.run([str(PY), E], capture_output=True, text=True)
        d, w = OUT / "dist_all_proven", OUT / "work_all_proven"
        t = time.time()
        extra = sum((["--exclude-module", n] for n in nof), [])
        b = subprocess.run([str(PY), "-m", "PyInstaller", "--onefile", "--noconfirm", "--clean", "--distpath", str(d),
                            "--workpath", str(w), "--specpath", str(w), "-n", "app", *extra, E],
                           capture_output=True, text=True, cwd=str(OUT))
        (OUT / "build_all_proven.log").write_text(b.stdout + b.stderr, encoding="utf-8")
        exe = d / "app.exe"
        run = subprocess.run([str(exe)], capture_output=True, text=True) if exe.exists() else None
        res["all_proven"] = {"applied": list(gen), "nofollow": nof, "build_rc": b.returncode,
                             "mb": round(exe.stat().st_size / 1048576, 1) if exe.exists() else None,
                             "sec": round(time.time() - t), "rc": run.returncode if run else None,
                             "match": bool(run) and run.stdout == expected_out,
                             "source_run_match": src.stdout == expected_out,
                             "stderr": (run.stderr[-600:] if run else ""), "apply_log": log}
    finally:
        rlog = {}
        for k, g in gen.items():
            m1 = []
            for line in g["build_instructions"]["uninstall_commands"]:
                if "方法2" in line:
                    break
                m1.append(line)
            rlog[k] = sh(m1, st)
        after = snap()
        res["all_proven"]["restore_log"] = rlog
        res["all_proven"]["restored_identical"] = after == before
        res["all_proven"]["bak_left"] = (SP.parent / "_pystub_bak").exists()
    print(json.dumps(res["all_proven"], ensure_ascii=False, indent=1))
(OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
