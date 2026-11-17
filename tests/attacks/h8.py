"""第 8 回の検証用ハーネス: 本物の venv（--without-pip）に合成パッケージを置き、
基準実行 → verify(run_entry=True, record=True) → 確認済み（実行）をその場で適用 → 同じ入力で実行 → 復元・照合。"""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
from mcp_pystub.verifier import verify  # noqa
from mcp_pystub.stub_generator import generate_stubs  # noqa

ROOT = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "h8"
TEMPLATE = ROOT / "_template_venv"


def write(base: Path, files: dict):
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            f.write_bytes(body)
        else:
            f.write_text(body, encoding="utf-8")


def make_venv(dst: Path, py=None):
    if not TEMPLATE.exists():
        subprocess.run([py or sys.executable, "-m", "venv", "--without-pip", str(TEMPLATE)], check=True)
    shutil.copytree(TEMPLATE, dst)
    # pyvenv.cfg は home を指すだけなので複製で動く
    return dst / "Lib" / "site-packages", dst / "Scripts" / "python.exe"


def snap(d: Path):
    out = {}
    for f in sorted(d.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            out[str(f.relative_to(d))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def run_plain(py, entry: Path, args=(), env_extra=None, cwd=None):
    env = dict(os.environ)
    for k in ("PYTHONPATH", "MCP_PYSTUB_BOOT", "MCP_PYSTUB_ROOT", "PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(k, None)
    env.update(env_extra or {})
    r = subprocess.run([str(py), str(entry), *args], cwd=str(cwd or entry.parent), capture_output=True, env=env, timeout=300)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")[-800:]


def case(name, sp_files, entry_src, packages, args=(), extra_proj=None, env_extra=None, record=True, py=None):
    d = ROOT / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    sp, vpy = make_venv(d / "venv", py)
    write(sp, sp_files)
    write(d / "proj", {"entry.py": entry_src, "pyproject.toml": "", **(extra_proj or {})})  # 置き場所の上の pyproject を拾わない
    entry = d / "proj" / "entry.py"
    s0 = snap(sp)
    base = run_plain(vpy, entry, args, env_extra)
    v = verify(str(entry), python_path=str(sp), packages=packages, submodules=[], nofollow=[], run_entry=True,
               record=record, entry_args=list(args))
    s1 = snap(sp)
    conf = v["status_summary"]["確認済み（実行）"]
    res = {"case": name, "base": base[:2], "base_err": base[2][-200:],
           "statuses": {k: (x["status"], (x.get("observed") or {}).get("state"), x.get("why_not_confirmed_run")) for k, x in v["statuses"].items()},
           "confirmed_run": conf, "verified_sets": v.get("verified_sets"), "baseline_failed": v["baseline_failed"],
           "child_processes": v.get("child_processes"), "startup_loaded": v.get("startup_loaded"),
           "rejected": [(r["item"], r["reason"]) for r in v["rejected"]], "verify_changed_venv": s0 != s1}
    if conf:
        bak = d / "bak"
        bak.mkdir()
        for p in conf:
            g = generate_stubs(str(entry), p, str(sp))
            assert "error" not in g, g
            for c in (sp / p, sp / (p + ".py")):
                if c.exists():
                    shutil.move(str(c), str(bak / c.name))
            write(sp, g["files"])
        ap = run_plain(vpy, entry, args, env_extra)
        for p in conf:
            for c in (sp / p, sp / (p + ".py")):
                if c.is_dir():
                    shutil.rmtree(c)
                elif c.exists():
                    c.unlink()
        for c in bak.iterdir():
            shutil.move(str(c), str(sp / c.name))
        bak.rmdir()
        res["applied"] = ap[:2]
        res["applied_err"] = ap[2][-300:]
        res["F3"] = (ap[0] != base[0] or ap[1] != base[1])
        res["restored_ok"] = snap(sp) == s0
    else:
        res["F3"] = False
    print("RES " + json.dumps(res, ensure_ascii=False), flush=True)
    return res
