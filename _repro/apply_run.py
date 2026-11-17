"""仕上げの回: 静的な候補を全部適用 → エントリーを実行 → 標準出力のバイト列と終了コードを基準と比べる → 復元。

python apply_run.py <out_dir> <scenario> ...
候補 = stubbable・conditional・submodule_stub_hints・conditional_submodule_hints（nofollow はビルドから外すだけなので対象外）。
"""
import hashlib, json, subprocess, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.analyzer import analyze  # noqa: E402
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa: E402

BASH = r"C:\Program Files\Git\bin\bash.exe"
out = Path(sys.argv[1])
assert "after_final" in str(out), out  # after_final / after_final2
out.mkdir(parents=True, exist_ok=True)


def snap(sp: Path):
    return {str(p.relative_to(sp)): (p.stat().st_size, hashlib.md5(p.read_bytes()).hexdigest() if p.stat().st_size < 65536 else p.stat().st_mtime_ns)
            for p in sp.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def sh(cmds, cwd):
    r = subprocess.run([BASH, "-c", "set -e\n" + "\n".join(cmds)], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout + r.stderr)[-400:]


def run(py, e):
    r = subprocess.run([str(py), str(e)], cwd=str(e.parent), capture_output=True, timeout=600)
    return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")[-600:]


for n in sys.argv[2:]:
    base = Path(rf"C:\tmp\pystub-e2e\{n}")
    E, SP, PY = base / "proj" / "entry.py", base / "venv" / "Lib" / "site-packages", base / "venv" / "Scripts" / "python.exe"
    t0 = time.time()
    a = analyze(str(E), python_path=str(SP))
    pk = [p["package_name"] for p in a["stubbable"]] + [p["package_name"] for p in a["conditional"]]
    subs = {}
    for h in a["submodule_stub_hints"] + a["conditional_submodule_hints"]:
        subs.setdefault(h["submodule"], h)
    gen = {}
    for p in pk:
        gen[p] = generate_stubs(str(E), p, str(SP))
    for s_, h in subs.items():
        gen[s_] = generate_submodule_stubs(str(E), h["parent_package"], s_, str(SP))
    errs = {k: g["error"] for k, g in gen.items() if "error" in g}
    gen = {k: g for k, g in gen.items() if "error" not in g}
    st = out / n / "apply"
    st.mkdir(parents=True, exist_ok=True)
    for k, g in gen.items():
        for f, c in g["files"].items():
            q = st / "_stubs" / f
            q.parent.mkdir(parents=True, exist_ok=True)
            q.write_text(c, encoding="utf-8", newline="\n")
    b0 = run(PY, E)
    before = snap(SP)
    rec = {"scenario": n, "items": list(gen), "gen_errors": errs, "base_rc": b0[0], "base_len": len(b0[1])}
    applied = []
    try:
        for k, g in gen.items():
            bi = g["build_instructions"]
            r1, r2 = sh(bi["backup_commands"], st), sh(bi["install_commands"], st)
            applied.append(k)
            if r1[0] or r2[0]:
                rec.setdefault("apply_errors", {})[k] = [r1, r2]
        r = run(PY, E)
        rec.update({"applied_rc": r[0], "applied_len": len(r[1]), "stdout_same": r[1] == b0[1], "rc_same": r[0] == b0[0],
                    "applied_stderr": r[2] if (r[1] != b0[1] or r[0] != b0[0]) else ""})
    finally:
        rl = {}
        for k in reversed(applied):
            m1 = []
            for line in gen[k]["build_instructions"]["uninstall_commands"]:
                if "方法2" in line:
                    break
                m1.append(line)
            rl[k] = sh(m1, st)
        rec["restore_rc"] = {k: v[0] for k, v in rl.items()}
        vc = {k: sh(gen[k]["build_instructions"].get("verify_commands", []), st)[0] for k in applied}
        rec["verify_commands_rc"] = vc
        rec["restored_identical"] = snap(SP) == before
        rec["bak_left"] = (SP.parent / "_pystub_bak").exists() or (SP / "_pystub_bak").exists()
    rec["s"] = round(time.time() - t0, 1)
    (out / f"{n}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rec[k] for k in ("scenario", "items", "base_rc", "applied_rc", "stdout_same", "rc_same",
                                          "restored_identical", "bak_left", "s")}, ensure_ascii=False), flush=True)
    if not rec.get("restored_identical"):
        print("RESTORE MISMATCH: stop", flush=True)
        sys.exit(2)
