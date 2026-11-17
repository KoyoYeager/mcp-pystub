"""第 9 回: 子プロセスの木の後始末（timeout・孫・一時フォルダ）。"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _proj(tmp_path, entry_src):
    sp = tmp_path / "sp"
    (sp / "xv").mkdir(parents=True)
    (sp / "xv" / "__init__.py").write_text("V = 1\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    ep = proj / "entry.py"
    ep.write_text(entry_src, encoding="utf-8")
    return sp, ep


def _temps():
    t = Path(tempfile.gettempdir())
    return {p.name for p in t.iterdir() if p.name.startswith(("pystub_verify_", "pystub_boot_"))}


GRAND = ("import subprocess, sys, time\n"
         "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
         "open(@PID@, 'w').write(str(p.pid))\n")


def test_timeout_kills_tree(tmp_path):
    from mcp_pystub.verifier import verify
    pidf = tmp_path / "pid.txt"
    sp, ep = _proj(tmp_path, "import xv\n" + GRAND.replace("@PID@", repr(str(pidf))) + "time.sleep(120)\n")
    before = _temps()
    t = time.monotonic()
    v = verify(str(ep), python_path=str(sp), packages=["xv"], submodules=[], nofollow=[], run_entry=True, timeout=8)
    dt = time.monotonic() - t
    assert dt < 8 * 2 + 5 + 10, dt  # 基準の import の子 + エントリー（timeout）+ 5 秒
    assert v["baseline_failed"] and "timeout" in v["baseline_failed"]["reason"]
    pid = int(pidf.read_text())
    time.sleep(1)
    assert not _alive(pid), "孫プロセスが残った"
    assert _temps() <= before, _temps() - before


def test_grandchild_left_after_exit_is_killed(tmp_path):
    from mcp_pystub.verifier import verify
    pidf = tmp_path / "pid.txt"
    sp, ep = _proj(tmp_path, "import xv\n" + GRAND.replace("@PID@", repr(str(pidf))) + "print('done')\n")
    before = _temps()
    t = time.monotonic()
    verify(str(ep), python_path=str(sp), packages=["xv"], submodules=[], nofollow=[], run_entry=True, timeout=30)
    assert time.monotonic() - t < 60
    time.sleep(1)
    assert not _alive(int(pidf.read_text())), "孫プロセスが残った"
    assert _temps() <= before


def test_no_temp_left_after_os_exit_and_exception(tmp_path):
    from mcp_pystub.verifier import verify
    before = _temps()
    sp, ep = _proj(tmp_path, "import os, xv\nprint('x', flush=True)\nos._exit(3)\n")
    verify(str(ep), python_path=str(sp), packages=["xv"], submodules=[], nofollow=[], run_entry=True, record=True)
    sp2, ep2 = _proj(tmp_path / "b", "import xv\nraise RuntimeError('boom')\n")
    verify(str(ep2), python_path=str(sp2), packages=["xv"], submodules=[], nofollow=[], run_entry=True)
    assert _temps() <= before, _temps() - before


def test_trace_cli_survives_os_exit(tmp_path):
    """エントリーが os._exit しても記録が残る"""
    from mcp_pystub import trace as tr
    sp = tmp_path / "sp"
    (sp / "xw").mkdir(parents=True)
    (sp / "xw" / "__init__.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    ep = tmp_path / "e.py"
    ep.write_text("import os, xw\nxw.f()\nos._exit(4)\n", encoding="utf-8")
    out = tmp_path / "t.json"
    before = _temps()
    env = dict(os.environ, PYTHONPATH=str(sp) + os.pathsep + str(ROOT))
    r = subprocess.run([sys.executable, "-m", "mcp_pystub.trace", "--out", str(out), str(ep)], env=env,
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 4, r.stderr
    t = tr.load(str(out))
    assert tr.observe(t, "xw", [str(sp)])["state"] == "executed"
    assert _temps() <= before


def test_entry_sees_normal_conditions(tmp_path):
    """エントリーから見える条件が普段の python entry.py（標準出力をファイルに）と同じ（起動用の 2 点を除く）"""
    from mcp_pystub.verifier import verify
    probe = ("import os, sys, locale\n"
             "print(sys.stdout.fileno() >= 0, sys.stdout.encoding, sys.flags.utf8_mode, locale.getpreferredencoding(False))\n"
             "print(sys.argv[0], __name__, os.path.isabs(__file__), sys.path[0] == os.path.dirname(os.path.abspath(__file__)))\n"
             "print(os.environ.get('PYTHONIOENCODING'), os.environ.get('MPLBACKEND'), os.environ.get('QT_QPA_PLATFORM'))\n"
             "print(sorted(k for k in os.environ if k.startswith('MCP_PYSTUB')))\n"
             "import xv\n")
    sp, ep = _proj(tmp_path, probe)
    env = dict(os.environ)
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "MPLBACKEND", "QT_QPA_PLATFORM"):
        env.pop(k, None)
    env["PYTHONPATH"] = str(sp)
    plain = subprocess.run([sys.executable, str(ep)], cwd=str(ep.parent), env=env, capture_output=True)
    assert plain.returncode == 0, plain.stderr
    old = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        v = verify(str(ep), python_path=str(sp), packages=["xv"], submodules=[], nofollow=[], run_entry=True)
    finally:
        os.environ.clear()
        os.environ.update(old)
    lines = plain.stdout.decode("utf-8", "replace").splitlines()
    assert lines[3] == "[]"  # 普段は無い
    assert v["entry_matched"] is True, v
    assert v["entry_conditions"]["stdout_head"].splitlines()[:3] == lines[:3], (v["entry_conditions"], lines)


def test_stale_temp_cleanup(tmp_path, monkeypatch):
    """1 日以上前の pystub_verify_* / pystub_boot_* を次の開始時に消す（新しいものは消さない）"""
    from mcp_pystub import proc
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    old = tmp_path / "pystub_verify_old"
    new = tmp_path / "pystub_boot_new"
    old.mkdir()
    new.mkdir()
    assert proc.cleanup_stale_temp(max_age_s=86400.0) == 0
    assert proc.cleanup_stale_temp(max_age_s=-1) == 2
    assert not old.exists() and not new.exists()


def test_summarize_procs_parent_child():
    from mcp_pystub.proc import summarize_procs
    root = {"pid": 1, "ppid": 0, "role": "root", "launched": 1, "trace": {"executed": []}}
    ok = [root, {"pid": 2, "ppid": 1, "role": "desc", "launched": 0, "trace": {"executed": []}}]
    assert summarize_procs(ok, True)["untraced_child_processes"] == 0
    # cmd（登録されない）の下の python: 親が登録されていない + root の起動の数が合わない
    cmd = [dict(root), {"pid": 3, "ppid": 99, "role": "desc", "launched": 0, "trace": {"executed": []}}]
    assert summarize_procs(cmd, True)["untraced_child_processes"] == 2
    # -I の子（登録されない）が普通の孫を起動: 合計では合うが、親子の対応では合わない
    i_child = [{"pid": 1, "ppid": 0, "role": "root", "launched": 1, "trace": {"executed": []}},
               {"pid": 5, "ppid": 4, "role": "desc", "launched": 0, "trace": {"executed": []}}]
    assert summarize_procs(i_child, True)["untraced_child_processes"] >= 1
