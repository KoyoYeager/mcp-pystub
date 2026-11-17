"""F3 攻撃の共通部: 合成のパッケージ群で 記録 → verify(run_entry=True, record=True / trace_files) → 確認済み（実行）を
実際に適用（site のコピーのパッケージを生成スタブに置き換え）→ 同じ入力で実行して比べる。"""
import json, os, shutil, subprocess, sys
from pathlib import Path

REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
from mcp_pystub.verifier import verify  # noqa
from mcp_pystub.analyzer import analyze  # noqa
from mcp_pystub.stub_generator import generate_stubs  # noqa
from mcp_pystub import trace as tr  # noqa

ROOT = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "f3"


def write(base: Path, files: dict):
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def run_plain(entry: Path, sp: Path, args=(), py=None):
    env = dict(os.environ, PYTHONPATH=str(sp), PYTHONIOENCODING="utf-8")
    r = subprocess.run([py or sys.executable, str(entry), *args], cwd=str(entry.parent), capture_output=True, env=env)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")[-600:]


def run_trace(entry: Path, sp: Path, out: Path, args=(), py=None):
    env = dict(os.environ, PYTHONPATH=str(sp) + os.pathsep + REPO, PYTHONIOENCODING="utf-8")
    r = subprocess.run([py or sys.executable, "-m", "mcp_pystub.trace", "--out", str(out), str(entry), *args],
                       cwd=str(entry.parent), capture_output=True, env=env)
    return r.returncode


def apply_copy(sp: Path, dst: Path, entry: Path, pkgs):
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(sp, dst)
    for p in pkgs:
        g = generate_stubs(str(entry), p, str(sp))
        assert "error" not in g, g
        for c in (dst / p, dst / (p + ".py")):
            if c.is_dir():
                shutil.rmtree(c)
            elif c.exists():
                c.unlink()
        write(dst, g["files"])
    return dst


def case(name, sp_files, entry_src, packages, mode="record", args=(), py=None, sp_override=None):
    d = ROOT / name
    shutil.rmtree(d, ignore_errors=True)
    sp = sp_override or (d / "sp")
    if sp_override is None:
        write(sp, sp_files)
    write(d / "proj", {"entry.py": entry_src, "pyproject.toml": ""})
    entry = d / "proj" / "entry.py"
    base = run_plain(entry, sp, args, py)
    tf = d / "trace.json"
    trc = run_trace(entry, sp, tf, args, py)
    t = tr.load(str(tf))
    obs = {p: tr.observe(t, p, [str(sp)])["state"] for p in packages}
    a = analyze(str(entry), python_path=str(sp), trace_files=[str(tf)])
    verd = {}
    for k in ("stubbable", "conditional", "nofollow"):
        for x in a.get(k, []):
            verd[x["package_name"]] = k
    for x in a.get("trace_candidates", []):
        verd.setdefault(x["package_name"], "trace_candidate")
    if mode == "record":
        v = verify(str(entry), python_path=str(sp), packages=packages, submodules=[], nofollow=[], run_entry=True,
                   record=True, entry_args=list(args))
    else:
        v = verify(str(entry), python_path=str(sp), packages=packages, submodules=[], nofollow=[], run_entry=True,
                   trace_files=[str(tf)], entry_args=list(args))
    conf = v["status_summary"]["確認済み（実行）"]
    res = {"case": name, "mode": mode, "base": base[:2], "trace_rc": trc, "observed": obs, "static": verd,
           "passed": v["passed"]["packages"], "confirmed_run": conf, "baseline_failed": v["baseline_failed"],
           "rejected": [(r["item"], r["reason"]) for r in v["rejected"]], "unresolved": [u["item"] for u in v["unresolved"]]}
    if conf:
        dst = apply_copy(sp, d / "sp_applied", entry, conf)
        ap = run_plain(entry, dst, args, py)
        res["applied"] = ap[:2]
        res["applied_stderr"] = ap[2][-300:]
        res["F3"] = (ap[0] != base[0] or ap[1] != base[1])
    else:
        res["F3"] = False
    print(json.dumps({k: (v if not isinstance(v, str) or len(v) < 300 else v[:150] + "..." + v[-100:]) for k, v in res.items()},
                     ensure_ascii=False), flush=True)
    return res
