"""第 9 回 受け入れ 6: 孫を残すエントリーで timeout + 5 秒以内に戻り、孫が残らない。一時フォルダが残らない。

python timeout9.py <out.json>
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.verifier import verify  # noqa: E402

out = Path(sys.argv[1])
work = Path(tempfile.mkdtemp(prefix="t9_"))


def alive(pid):
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


def temps():
    t = Path(tempfile.gettempdir())
    return {p.name for p in t.iterdir() if p.name.startswith(("pystub_verify_", "pystub_boot_"))}


def proj(name, src):
    d = work / name
    (d / "sp" / "xv").mkdir(parents=True)
    (d / "sp" / "xv" / "__init__.py").write_text("V = 1\n", encoding="utf-8")
    (d / "proj").mkdir()
    (d / "proj" / "pyproject.toml").write_text("", encoding="utf-8")
    (d / "proj" / "entry.py").write_text(src, encoding="utf-8")
    return d


res = {}
before = temps()
for name, tail, to in [("sleep_with_grandchild", "time.sleep(300)\n", 10), ("exit_leaving_grandchild", "print('done')\n", 60)]:
    d = proj(name, "import subprocess, sys, time, xv\n"
                   "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])\n"
                   f"open({str(work / name / 'pid.txt')!r}, 'w').write(str(p.pid))\n" + tail)
    t = time.monotonic()
    v = verify(str(d / "proj" / "entry.py"), python_path=str(d / "sp"), packages=["xv"], submodules=[], nofollow=[],
               run_entry=True, timeout=to)
    dt = time.monotonic() - t
    time.sleep(1)
    pid = int((work / name / "pid.txt").read_text())
    res[name] = {"timeout": to, "elapsed_s": round(dt, 1), "grandchild_alive": alive(pid),
                 "baseline_failed": (v.get("baseline_failed") or {}).get("reason")}
for name, src in [("os_exit", "import os, xv\nprint('x', flush=True)\nos._exit(3)\n"),
                  ("exception", "import xv\nraise RuntimeError('boom')\n")]:
    d = proj(name, src)
    verify(str(d / "proj" / "entry.py"), python_path=str(d / "sp"), packages=["xv"], submodules=[], nofollow=[],
           run_entry=True, record=True)
res["temp_left"] = sorted(temps() - before)
out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(res, ensure_ascii=False))
