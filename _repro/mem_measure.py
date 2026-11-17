"""analyze 単体のピークメモリ（プロセスの PeakWorkingSetSize）を測る。

python mem_measure.py <new|old> <entry> [python_path]
  new: 作業ツリーのコード / old: cbac611（_repro/old）
子プロセスで 1 回だけ analyze して、所要時間とピークを出す。
"""
import ctypes, ctypes.wintypes, sys, time
from pathlib import Path

which, entry = sys.argv[1], sys.argv[2]
sp = sys.argv[3] if len(sys.argv) > 3 else ""
D = Path(__file__).resolve().parent
root = D / "old" if which == "old" else D.parent
sys.path.insert(0, str(root))
from mcp_pystub.analyzer import analyze  # noqa: E402


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


def peak_mb() -> tuple[float, float]:
    pmc = PMC()
    pmc.cb = ctypes.sizeof(PMC)
    h = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb)
    return pmc.PeakWorkingSetSize / 1048576, pmc.PeakPagefileUsage / 1048576


base = peak_mb()
t = time.time()
r = analyze(entry, python_path=sp)
dt = time.time() - t
ws, pf = peak_mb()
print(f"{which} entry={entry} total={r.get('total_packages_analyzed')} stubbable={len(r.get('stubbable', []))} "
      f"conditional={len(r.get('conditional', []) or [])} time={dt:.1f}s peak_ws={ws:.0f}MB peak_commit={pf:.0f}MB "
      f"(before={base[0]:.0f}MB)", flush=True)
