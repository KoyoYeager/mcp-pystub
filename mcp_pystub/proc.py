"""子プロセスの起動と後始末（第 9 回）。

verify と実行の記録は、対象のプログラムを「標準出力をファイル・パイプにつないだ普段の `python entry.py 引数…`」と
区別がつかない条件で動かす。普段と違うのは次の 2 つだけ:

- PYTHONPATH の先頭に起動用のフォルダ（sitecustomize.py）を足す（利用者の PYTHONPATH は後ろに残す）
- 起動用の環境変数 MCP_PYSTUB_BOOT（設定ファイルのパス）

プロセスの木（孫を含む）は Windows では Job Object、それ以外ではプロセスグループで、終わった時・timeout の時に
まとめて止める（verify の後に孫を残さない）。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

BOOT_ENV = "MCP_PYSTUB_BOOT"


def boot_env(boot_dir: Path, boot_cfg_path: Path, base: dict | None = None) -> dict:
    """利用者の環境変数をそのまま使い、起動用の 2 点だけを足した環境。"""
    env = dict(os.environ if base is None else base)
    env.pop(BOOT_ENV, None)
    old = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(boot_dir) + (os.pathsep + old if old else "")
    env[BOOT_ENV] = str(boot_cfg_path)
    return env


def write_boot(boot_dir: Path) -> None:
    from mcp_pystub.trace import BOOT
    boot_dir.mkdir(parents=True, exist_ok=True)
    f = boot_dir / "sitecustomize.py"
    if not f.exists():
        f.write_text(BOOT, encoding="utf-8")


class _Job:
    """Windows の Job Object（KILL_ON_JOB_CLOSE）。孫を含むプロセスの木をまとめて止める。"""

    def __init__(self) -> None:
        self.h = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IOC(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]

            class EXT(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IOC), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = EXT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(h)
                return
            self.k32, self.h = k32, h
        except Exception:  # noqa: BLE001
            self.h = None

    def add(self, p: subprocess.Popen) -> bool:
        if self.h is None:
            return False
        return bool(self.k32.AssignProcessToJobObject(self.h, int(p._handle)))  # type: ignore[attr-defined]

    def kill(self) -> None:
        if self.h is not None:
            self.k32.TerminateJobObject(self.h, 1)
            self.k32.CloseHandle(self.h)
            self.h = None


def run_tree(cmd: list[str], cwd: str, env: dict, timeout: float | None, stdout=None, stderr=None,
             stdin=subprocess.DEVNULL) -> tuple[int | None, bool]:
    """プロセスの木を動かし、終わったら（timeout なら途中で）孫まで止める。(終了コード, timeout したか)"""
    job = _Job()
    kw: dict = {}
    if sys.platform != "win32":
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr, **kw)
    in_job = job.add(p)
    timed_out = False
    try:
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        rc = None
    except BaseException:
        _kill(p, job, in_job)
        raise
    _kill(p, job, in_job)
    return rc, timed_out


def _kill(p: subprocess.Popen, job: _Job, in_job: bool) -> None:
    if sys.platform == "win32":
        if in_job:
            job.kill()
        else:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            job.kill()
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()


def read_procs(proc_dir: Path) -> list[dict]:
    """起動用の仕組みが書いたプロセスごとの記録（root と子孫）。サブインタープリターの起動の数は親のプロセスに足す。"""
    out = []
    sub: dict[str, int] = {}
    if proc_dir.is_dir():
        for f in sorted(proc_dir.glob("sublaunch_*.txt")):
            try:
                sub[f.stem.split("_", 1)[1]] = len(f.read_text(encoding="utf-8").split())
            except OSError:
                pass
        for f in sorted(proc_dir.glob("proc_*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rec = {"pid": f.stem.split("_")[1] if "_" in f.stem else f.stem, "role": "desc", "trace": None,
                       "startup_loaded": [], "launched": None, "finished": False}
            n = sub.get(str(rec.get("pid")), 0)
            if n and rec.get("launched") is not None:
                rec["launched"] = int(rec["launched"]) + n
            out.append(rec)
    return out


def summarize_procs(procs: list[dict], recording: bool) -> dict:
    """子孫のプロセスの数と、記録・差し込みの効かなかった（親子の対応が合わない）プロセスの数。

    各プロセスは、自分の pid・親の pid・自分が起動した子の数（監査フックで数えた subprocess / os.spawn* / os.exec* /
    os.system / CreateProcess 等）を書く。プロセスごとに「起動した数 = 自分を親とする登録済みの子の数」を確かめ、
    合わないプロセスを数える（python 以外の子・環境を消した子・-E / -I / -S の子は登録されないので合わない。
    その先の孫の python が登録されていても、親は登録されていないプロセスなので相殺しない）。
    """
    pids = {str(p.get("pid")) for p in procs}
    kids: dict[str, int] = {}
    orphans = 0
    for p in procs:
        if p.get("role") == "root":
            continue
        pp = str(p.get("ppid"))
        if pp in pids:
            kids[pp] = kids.get(pp, 0) + 1
        else:
            orphans += 1  # 親が登録されていない（python 以外・効かなかった python の子）
    mismatched = 0
    for p in procs:
        if p.get("launched") is None:
            mismatched += 1  # 終わり方が分からない（止められた等）: 起動の数が分からない
        elif int(p["launched"]) != kids.get(str(p.get("pid")), 0):
            mismatched += 1
    untraced = mismatched + orphans
    if recording:
        untraced += sum(1 for p in procs if p.get("role") != "root" and p.get("launched") is not None
                        and not p.get("trace"))
    desc = [p for p in procs if p.get("role") != "root"]
    return {"child_processes": len(desc), "launched": sum(int(p.get("launched") or 0) for p in procs),
            "untraced_child_processes": untraced, "mismatched_processes": mismatched, "orphan_processes": orphans,
            "root_seen": any(p.get("role") == "root" for p in procs)}


def cleanup_stale_temp(max_age_s: float = 86400.0) -> int:
    """呼び出し元が強制終了されて残った一時フォルダ（作成から 1 日以上たった pystub_verify_* / pystub_boot_*）を消す。"""
    import tempfile
    n = 0
    now = time.time()
    try:
        cands = list(Path(tempfile.gettempdir()).glob("pystub_verify_*")) + \
            list(Path(tempfile.gettempdir()).glob("pystub_boot_*"))
    except OSError:
        return 0
    for d in cands:
        try:
            st = d.stat()
            born = getattr(st, "st_birthtime", None) or (st.st_ctime if sys.platform == "win32" else st.st_mtime)
            if d.is_dir() and now - born >= max_age_s:
                shutil.rmtree(d, ignore_errors=True)
                n += 0 if d.exists() else 1
        except OSError:
            pass
    return n


def rmtree_retry(p: Path) -> None:
    """一時フォルダを消す。Windows でファイルが掴まれていて消せない時は数秒やり直し、だめならプロセスの終了時にもう一度消す。"""
    for _ in range(20):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.25)
    import atexit
    atexit.register(shutil.rmtree, p, True)
