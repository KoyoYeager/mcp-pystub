"""Round-3 re-measurement (proven / proven+conditional / violating entry; OUT=after_round3). Reuses venvs / expected / base results of the first run (read-only)."""
import sys, os, json, time, subprocess, shutil, ast, re, traceback
from pathlib import Path
sys.path.insert(0, r"C:\tmp\pystub-e2e")
from scenarios import S

ROOT = Path(r"C:\tmp\pystub-e2e")
OUT = ROOT / "after_round3"
REPO = r"C:\workspace\mcp-pystub\.claude\worktrees\agent-ab7fe9091ba1b9f66"
SYS_PY = r"C:\Python313\python.exe"
BASH = r"C:\Program Files\Git\bin\bash.exe"
DEADLINE_MIN = 40
BUILD_TIMEOUT = 1500
os.environ["UV_LINK_MODE"] = "copy"
os.environ["UV_CACHE_DIR"] = "C:/tmp/pystub-e2e/uvcache"

VIOLATE = {
    # Signal.plot() は Qt の画面を開くので元の環境では実行しない（スタブ適用時に例外になることだけ確かめる）
    "12_asammdf": ("""
import numpy as np
from asammdf import Signal
t = np.arange(0, 10, 1.0)
s = Signal(samples=t*2, timestamps=t, name="sig1", unit="V")
s.plot()
print("plotted")
""", False),
    "02_openpyxl": ("""
import openpyxl, os
wb = openpyxl.load_workbook(os.path.join(os.path.dirname(os.path.abspath(__file__)), "with_image.xlsx"))
ws = wb.active
print("images", len(ws._images), ws["A1"].value)
""", True),
}
MAKE_IMAGE_BOOK = """
import io, openpyxl, sys
from PIL import Image as PI
from openpyxl.drawing.image import Image
im = PI.new("RGB", (4, 4), (255, 0, 0)); b = io.BytesIO(); im.save(b, format="PNG"); b.seek(0)
wb = openpyxl.Workbook(); ws = wb.active; ws["A1"] = "hello"
ws.add_image(Image(b), "B2"); wb.save(sys.argv[1])
"""

ENTRY2 = {
    "06_requests": ('''
import requests
req = requests.Request("GET", "http://\u4f8b\u3048.\u30c6\u30b9\u30c8/\u30d1\u30b9?q=\u65e5\u672c", headers={"X-A": "b"})
p = req.prepare()
print(p.method, p.url)
r2 = requests.Request("POST", "http://example.com/a", params={"q": "1"}, data={"k": "v"}).prepare()
print(r2.url, r2.body)
'''),
    "10_flask": ('''
from flask import Flask, jsonify, session
app = Flask(__name__)
app.secret_key = "k"
@app.route("/s")
def s():
    session["x"] = 2
    return jsonify(ok=True)
c = app.test_client()
r = c.get("/s")
print(r.status_code, r.get_json(), "session=" in r.headers.get("Set-Cookie", ""))
with c.session_transaction() as ss:
    ss["y"] = 5
r = c.get("/s")
print(r.status_code)
'''),
}


def sh(cmd, cwd=None, env=None, timeout=1800, log=None):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        out, err, rc = p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace")
        err = (e.stderr or b"").decode("utf-8", "replace") + "\nTIMEOUT"
        rc = -999
    if log:
        Path(log).write_text(f"CMD: {cmd}\nRC: {rc}\n--- STDOUT ---\n{out}\n--- STDERR ---\n{err}", encoding="utf-8")
    return rc, out, err, time.time() - t


def run(name, entry2=False):
    pkgs, _ = S[name]
    OLD = ROOT / name
    outname = name + ("_entry2" if entry2 else "")
    D = OUT / outname
    D.mkdir(parents=True, exist_ok=True)
    R = {"scenario": outname, "variants": {}, "defects": [], "notes": [], "uncompensated": True}
    t0 = time.time()

    def over():
        return (time.time() - t0) / 60 > DEADLINE_MIN

    def save():
        (D / "result.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")

    V = OLD / "venv"
    VPY = V / "Scripts" / "python.exe"
    SP = V / "Lib" / "site-packages"
    if not VPY.exists():
        R["error"] = "previous venv missing"
        save()
        return R
    env = dict(os.environ)
    env["PATH"] = str(V / "Scripts") + os.pathsep + env["PATH"]
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("VIRTUAL_ENV", None)
    penv = dict(os.environ, PYTHONPATH=REPO)

    if entry2:
        (D / "proj").mkdir(exist_ok=True)
        entry = D / "proj" / "entry.py"
        entry.write_text(ENTRY2[name].strip() + "\n", encoding="utf-8")
    else:
        entry = OLD / "proj" / "entry.py"
    # --- venv sanity
    freeze0 = (OLD / "freeze_orig.txt").read_text()
    norm = lambda s: sorted(x.strip() for x in s.splitlines() if x.strip())
    rc, o, e, _ = sh([str(VPY), "-m", "pip", "freeze"])
    pre = {"freeze_equal": norm(o) == norm(freeze0)}
    leftovers = [str(p) for p in list(SP.rglob("*.disabled"))[:5]] + [str(p) for p in [V / "Lib" / "_pystub_bak"] if p.exists()]
    pre["leftovers"] = leftovers
    if not pre["freeze_equal"]:
        miss = [l.strip() for l in freeze0.splitlines() if l.strip() and l.strip() not in set(norm(o))]
        rcx, ox, ex, _ = sh(["uv", "pip", "install", "--python", str(VPY), "--reinstall", "--no-deps"] + miss, log=D / "venv_repair.log")
        pre["repair"] = {"missing": miss, "rc": rcx}
        rc, o, e, _ = sh([str(VPY), "-m", "pip", "freeze"])
        pre["freeze_equal_after_repair"] = norm(o) == norm(freeze0)
    # expected output
    if entry2:
        rc, o, e, _ = sh([str(VPY), str(entry)], env=env, log=D / "expected.log")
        expected = o.replace("\r\n", "\n").strip()
        (D / "expected.txt").write_text(expected, encoding="utf-8")
        pre["expected_rc"] = rc
        if rc != 0:
            R["error"] = "entry2 fails in venv: " + e[-1500:]
            R["pre"] = pre
            save()
            return R
    else:
        expected = (OLD / "expected.txt").read_text(encoding="utf-8").strip()
        rc, o, e, _ = sh([str(VPY), str(entry)], env=env)
        pre["expected_rerun_match"] = (rc == 0 and o.replace("\r\n", "\n").strip() == expected)
    R["pre"] = pre

    def build(tag, excludes=()):
        out = {"tag": tag, "excludes": list(excludes)}
        dist = D / f"dist_{tag}"
        work = D / f"work_{tag}"
        for d in (dist, work):
            shutil.rmtree(d, ignore_errors=True)
        cmd = [str(VPY), "-m", "PyInstaller", "--onefile", "--noconfirm", "--clean", "--name", "app",
               "--distpath", str(dist), "--workpath", str(work), "--specpath", str(work)]
        for x in excludes:
            cmd += ["--exclude-module", x]
        cmd.append(str(entry))
        rc, o, e, dt = sh(cmd, cwd=str(D), env=env, timeout=BUILD_TIMEOUT, log=D / f"build_{tag}.log")
        out["build_rc"] = rc
        out["build_s"] = round(dt, 1)
        exe = dist / "app.exe"
        if rc == 0 and exe.exists():
            out["exe_bytes"] = exe.stat().st_size
            out["exe_mb"] = round(out["exe_bytes"] / 1048576, 1)
            rc2, o2, e2, dt2 = sh([str(exe)], cwd=str(D), env=env, timeout=300, log=D / f"run_{tag}.log")
            got = o2.replace("\r\n", "\n").strip()
            out.update(run_rc=rc2, run_s=round(dt2, 1), match=(got == expected), stdout=got[:1500], stderr=e2[-3000:])
        else:
            out["match"] = None
            out["build_tail"] = (o + e)[-2500:]
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(dist, ignore_errors=True)
        return out

    # base
    if entry2 or not (OLD / "result.json").exists():
        R["variants"]["base"] = build("base")
    else:
        old = json.load(open(OLD / "result.json", encoding="utf-8"))
        b = dict(old["variants"]["base"])
        b["reused_from_first_run"] = True
        R["variants"]["base"] = b
        R["old_analysis_time_ms"] = old.get("analysis_time_ms")
    save()
    # analyze
    rc, o, e, dt = sh([SYS_PY, str(ROOT / "runner.py"), "analyze", str(entry), str(SP), str(D / "analyze.json")],
                      env=penv, log=D / "analyze.log")
    R["analyze_rc"] = rc
    R["analyze_wall_s"] = round(dt, 1)
    if rc != 0:
        R["error"] = "analyze exception"
        R["analyze_err"] = (o + e)[-3000:]
        save()
        return R
    A = json.loads((D / "analyze.json").read_text(encoding="utf-8"))
    R["analysis_time_ms"] = A.get("analysis_time_ms")
    R["analyze_warnings"] = (A.get("warnings") or [])[:30]
    R["analyze_warnings_count"] = len(A.get("warnings") or [])

    def slim(x):
        return {k: x.get(k) for k in ("package_name", "estimated_size_mb", "reason", "warnings")}

    R["stubbable"] = [slim(x) for x in A["stubbable"]]
    R["nofollow"] = [slim(x) for x in A["nofollow"]]
    R["required"] = [slim(x) for x in A["required"]]
    hints = {}
    for x in A.get("submodule_stub_hints", []) or []:
        hints[x["submodule"]] = x
    for x in A["required"] + A["stubbable"] + A["nofollow"]:
        for h in x.get("submodule_stubs", []) or []:
            hints.setdefault(h["submodule"], h)
    R["hints"] = [{k: h.get(k) for k in ("submodule", "parent_package", "target_package", "estimated_savings_mb")} for h in hints.values()]
    R["top_keys"] = list(A.keys())
    stub_pkgs = [x["package_name"] for x in A["stubbable"]]
    nof = [x["package_name"] for x in A["nofollow"]]
    cond_pkgs = [x["package_name"] for x in A.get("conditional", [])]
    cond_hints = {}
    for x in A.get("conditional_submodule_hints", []) or []:
        cond_hints[x["submodule"]] = x
    R["conditional"] = [{k: x.get(k) for k in ("package_name", "estimated_savings_mb", "conditions_total", "unproven_because")}
                        | {"conditions": [{k: g.get(k) for k in ("entry", "public_api", "entry_location", "why_unproven", "uses", "may_be_swallowed")}
                                          for g in x.get("conditions", [])]} for x in A.get("conditional", [])]
    R["conditional_hints"] = [{k: h.get(k) for k in ("submodule", "parent_package", "target_package", "estimated_savings_mb", "conditions_total")}
                              | {"conditions": [{k: g.get(k) for k in ("entry", "public_api", "entry_location", "why_unproven", "uses", "may_be_swallowed")}
                                                for g in h.get("conditions", [])]} for h in cond_hints.values()]
    save()
    if not (stub_pkgs or nof or hints or cond_pkgs or cond_hints):
        R["notes"].append("no proposals; stub build skipped")
        save()
        return R

    def gen(kind, *args):
        key = args[-1]
        tagn = key.replace(".", "_")
        outj = D / f"gen_{kind}_{tagn}.json"
        rc, o, e, dt = sh([SYS_PY, str(ROOT / "runner.py"), "gen_" + kind, str(entry), str(SP), str(outj)] + list(args),
                          env=penv, log=D / f"gen_{kind}_{tagn}.log")
        if rc != 0:
            R["defects"].append({"what": f"generate {kind} {key} raised", "log": (o + e)[-1500:]})
            return None
        g = json.loads(outj.read_text(encoding="utf-8"))
        if "error" in g:
            R["defects"].append({"what": f"generate {kind} {key} returned error", "error": g["error"]})
            return None
        return g

    items = []
    for p in stub_pkgs:
        g = gen("pkg", p)
        if g:
            items.append((p, "pkg", g))
    for sub, h in hints.items():
        g = gen("sub", h["parent_package"], sub)
        if g:
            items.append((sub, "sub", g))
    cond_keys = []
    for p in cond_pkgs:
        g = gen("pkg", p)
        if g:
            items.append((p, "pkg", g))
            cond_keys.append(p)
    for sub, h in cond_hints.items():
        if sub in hints:
            continue
        g = gen("sub", h["parent_package"], sub)
        if g:
            items.append((sub, "sub", g))
            cond_keys.append(sub)
    syntax_errors = []
    nfiles = 0
    gen_warn = {}
    for key, kind, g in items:
        for rel, code in g["files"].items():
            nfiles += 1
            f = D / "_stubs" / key.replace(".", "_") / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(code, encoding="utf-8")
            try:
                ast.parse(code)
            except SyntaxError as ex:
                syntax_errors.append({"item": key, "file": rel, "err": str(ex)})
        R.setdefault("gen_summary", {})[key] = {k: g.get(k) for k in (
            "stub_file_count", "stub_total_bytes", "original_file_count", "eliminated_packages",
            "original_size_bytes", "stub_size_bytes", "symbols_exported")}
        if g.get("warnings"):
            gen_warn[key] = g["warnings"][:5]
        (D / f"build_instructions_{key.replace('.', '_')}.json").write_text(
            json.dumps(g["build_instructions"], ensure_ascii=False, indent=1), encoding="utf-8")
    R["stub_files_total"] = nfiles
    R["stub_syntax_errors"] = syntax_errors
    R["gen_warnings"] = gen_warn
    save()

    def bash(lines, item_dir):
        script = "\n".join(lines)
        rc, o, e, dt = sh([BASH, "-c", script], cwd=str(item_dir), env=env)
        return {"rc": rc, "out": (o + e)[-600:]}

    hook_log = []

    def disable_hooks(lines):
        for l in lines:
            m = re.match(r"# リネームして無効化: (\S+) →", l)
            if not m:
                continue
            n = m.group(1)
            for c in (SP / "PyInstaller" / "hooks" / n, SP / n):
                if c.exists():
                    c.rename(str(c) + ".disabled")
                    hook_log.append(str(c))
                    break
            else:
                hook_log.append("NOT FOUND: " + n)

    def enable_hooks():
        for h in list(hook_log):
            if h.startswith("NOT FOUND"):
                continue
            if os.path.exists(h + ".disabled"):
                os.rename(h + ".disabled", h)
        hook_log.clear()

    staged = D / "_stage"
    staged.mkdir(exist_ok=True)

    def apply_item(key, kind, g):
        bi = g["build_instructions"]
        shutil.rmtree(staged / "_stubs", ignore_errors=True)
        for rel, code in g["files"].items():
            p = staged / "_stubs" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(code, encoding="utf-8")
        r = {"backup": bash(bi["backup_commands"], staged)}
        r["install"] = bash(bi["install_commands"], staged)
        disable_hooks(bi.get("hook_disable", []))
        return r

    def restore_item(key, kind, g):
        bi = g["build_instructions"]
        lines = []
        for l in bi["uninstall_commands"]:
            if "方法2" in l:
                break
            lines.append(l)
        r = {"restore": bash(lines, staged)}
        r["verify"] = bash(bi["verify_commands"], staged)
        return r

    def freeze_state():
        rc, o, e, _ = sh([str(VPY), "-m", "pip", "freeze"])
        eq = norm(o) == norm(freeze0)
        d = None
        if not eq:
            a, b = set(norm(freeze0)), set(norm(o))
            d = {"missing": sorted(a - b), "extra": sorted(b - a)}
        rc, o2, e2, _ = sh([str(VPY), str(entry)], env=env)
        return {"freeze_equal": eq, "freeze_diff": d, "expected_reproduced": (rc == 0 and o2.replace("\r\n", "\n").strip() == expected),
                "bak_dir_exists": (V / "Lib" / "_pystub_bak").exists(),
                "bak_dir_files": sum(1 for _ in (V / "Lib" / "_pystub_bak").rglob("*") if _.is_file()) if (V / "Lib" / "_pystub_bak").exists() else 0}

    def repair_if_needed(v, key):
        st = v[key]
        if not st["freeze_equal"] or not st["expected_reproduced"]:
            miss = (st["freeze_diff"] or {}).get("missing", [])
            if miss:
                rcx, _, _, _ = sh(["uv", "pip", "install", "--python", str(VPY), "--reinstall", "--no-deps"] + miss)
                v[key + "_repair"] = {"cmd": "uv pip install --reinstall --no-deps " + " ".join(miss), "rc": rcx}
            else:
                v[key + "_repair"] = "needed but nothing identified (not repaired)"

    viol = None
    if name in VIOLATE:
        vsrc, run_orig = VIOLATE[name]
        vdir = D / "proj_violate"
        vdir.mkdir(exist_ok=True)
        vent = vdir / "entry_violate.py"
        vent.write_text(vsrc.strip() + "\n", encoding="utf-8")
        if name == "02_openpyxl":
            (vdir / "mk.py").write_text(MAKE_IMAGE_BOOK, encoding="utf-8")
            sh([str(VPY), str(vdir / "mk.py"), str(vdir / "with_image.xlsx")], env=env, log=D / "violate_make_book.log")
        vres = {"entry": str(vent)}
        if run_orig:
            rc, o, e, _ = sh([str(VPY), str(vent)], env=env, log=D / "violate_original.log")
            vres["original"] = {"rc": rc, "stdout": o[-2000:], "stderr": e[-3000:]}
        else:
            vres["original"] = "not run (opens a Qt window)"
        viol = (vent, vres)
        R["violate"] = vres
        save()

    def variant(tag, keys, excl, cycle=False, violate=False):
        if over():
            R["variants"][tag] = {"tag": tag, "note": "時間切れ(40分超)"}
            save()
            return None
        v = {}
        if cycle:  # apply -> restore twice-in-a-row test before the real build
            applied = []
            st = {}
            for key, kind, g in items:
                if key in keys:
                    st[key] = apply_item(key, kind, g)
                    applied.append((key, kind, g))
            enable_hooks()
            rr = {}
            for key, kind, g in reversed(applied):
                rr[key] = restore_item(key, kind, g)
            v["cycle1"] = {"apply": st, "restore": rr}
            v["cycle1_state"] = freeze_state()
            repair_if_needed(v, "cycle1_state")
        applied = []
        steps = {}
        for key, kind, g in items:
            if key in keys:
                steps[key] = apply_item(key, kind, g)
                applied.append((key, kind, g))
        b = build(tag, excl)
        v.update(b)
        if violate and viol is not None:
            vent, vres = viol
            venv2 = dict(env, QT_QPA_PLATFORM="offscreen")
            rc, o, e, _ = sh([str(VPY), str(vent)], env=venv2, timeout=300, log=D / f"violate_{tag}.log")
            vres["with_stubs"] = {"rc": rc, "stdout": o[-2000:], "stderr": e[-4000:]}
            R["violate"] = vres
            save()
        v["keys"] = list(keys)
        v["apply"] = steps
        v["hooks_disabled"] = list(hook_log)
        enable_hooks()
        rst = {}
        for key, kind, g in reversed(applied):
            rst[key] = restore_item(key, kind, g)
        v["restore"] = rst
        v["state_after_restore"] = freeze_state()
        repair_if_needed(v, "state_after_restore")
        R["variants"][tag] = v
        save()
        return v

    proven_keys = [k for k, _, _ in items if k not in cond_keys]
    all_keys = [k for k, _, _ in items]
    if proven_keys or nof:
        variant("proven", proven_keys, nof, cycle=bool(proven_keys))
    if cond_keys:
        variant("proven_plus_conditional", all_keys, nof, cycle=False, violate=True)
    R["elapsed_min"] = round((time.time() - t0) / 60, 1)
    save()
    for p in D.glob("work_*"):
        shutil.rmtree(p, ignore_errors=True)
    return R


if __name__ == "__main__":
    for n in sys.argv[1:]:
        try:
            run(n[:-7] if n.endswith("_entry2") else n, entry2=n.endswith("_entry2"))
        except Exception:
            d = OUT / n
            d.mkdir(parents=True, exist_ok=True)
            (d / "driver_exception.txt").write_text(traceback.format_exc())
