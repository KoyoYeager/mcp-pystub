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
    """起動用の仕組みが書いたプロセスごとの記録（root と子孫）。"""
    out = []
    if proc_dir.is_dir():
        for f in sorted(proc_dir.glob("proc_*.json")):
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                out.append({"pid": f.stem, "role": "desc", "trace": None, "startup_loaded": [], "launched": None,
                            "finished": False})
    return out


def summarize_procs(procs: list[dict], recording: bool) -> dict:
    """子孫のプロセスの数と、記録・差し込みの効かなかった（数の合わない）子の数。

    launched = 監査フックで数えたプロセスの起動（subprocess / os.spawn* / os.exec* / os.system / CreateProcess 等）。
    起動した数より、起動用の仕組みで登録された子孫が少なければ、その差は記録・差し込みの効かなかった子
    （環境を消した・-E / -I / -S・PYTHONPATH の上書き・python 以外のプログラム）。
    """
    desc = [p for p in procs if p.get("role") != "root"]
    launched = 0
    unknown = 0
    for p in procs:
        if p.get("launched") is None:
            unknown += 1  # 終わり方が分からない（止められた等）: その子の起動の数は分からない
        else:
            launched += int(p["launched"])
    untraced = max(0, launched - len(desc)) + unknown
    if recording:
        untraced += sum(1 for p in desc if p.get("launched") is not None and not p.get("trace"))
    return {"child_processes": len(desc), "launched": launched, "untraced_child_processes": untraced,
            "root_seen": any(p.get("role") == "root" for p in procs)}


def rmtree_retry(p: Path) -> None:
    for _ in range(5):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(0.2)
