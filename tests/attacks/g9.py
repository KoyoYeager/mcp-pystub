"""第 9 回の F3 攻撃。python g9.py [名前...]（普段の実行は既定で標準出力をファイル。H9_STDOUT=pipe でパイプ）"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")  # 出力だけ UTF-8（環境変数は変えない）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from h9 import case  # noqa

XV = {"xv/__init__.py": "VALUE = 'real-value'\ndef f():\n    return 'real-f'\n"}
C = {}
# A1: プロセスが Job に入っているか（verify の子は Job Object の中、普段は入っていない）
C["A1_in_job_branch"] = (XV, (
    "import ctypes\nfrom ctypes import wintypes\n"
    "k = ctypes.WinDLL('kernel32')\n"
    "k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]\n"
    "k.GetCurrentProcess.restype = wintypes.HANDLE\n"
    "r = wintypes.BOOL(); k.IsProcessInJob(k.GetCurrentProcess(), None, ctypes.byref(r))\n"
    "if not r.value:\n    import xv\n    print('free', xv.f())\nelse:\n    print('in job')\n"), ["xv"])
# A2: 起動用の sitecustomize が import できる（普段は ImportError）
C["A2_sitecustomize_branch"] = (XV, (
    "try:\n    import sitecustomize\n    has = True\nexcept ImportError:\n    has = False\n"
    "if not has:\n    import xv\n    print('plain', xv.f())\nelse:\n    print('site')\n"), ["xv"])
# A2b: atexit の登録数
C["A2b_atexit_count_branch"] = (XV, (
    "import atexit\n"
    "if atexit._ncallbacks() == 0:\n    import xv\n    print('plain', xv.f())\nelse:\n    print('hooked')\n"), ["xv"])
# A3: -I の子（数えられる・登録されない）が、普通の python の孫を起動する（登録される）→ 数が合う
_A3 = "import subprocess, sys; subprocess.run([sys.executable, '-c', 'pass']); import xv; print(xv.VALUE)"
C["A3_I_child_spawns_grandchild"] = (XV, (
    "import subprocess, sys\n"
    f"code = {_A3!r}\n"
    "r = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["A3b_E_child_spawns_grandchild"] = (XV, (
    "import subprocess, sys\n"
    f"code = {_A3!r}\n"
    "r = subprocess.run([sys.executable, '-E', '-c', code], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
# A4: cmd /c で 2 つの python（普通 + -I）。cmd は数えられ、普通の python が登録されて数が合う
C["A4_cmd_two_pythons"] = (XV, (
    "import subprocess, sys\n"
    "py = sys.executable\n"
    "line = '\"' + py + '\" -c \"pass\" & \"' + py + '\" -I -c \"import xv; print(xv.VALUE)\"'\n"
    "r = subprocess.run('cmd /d /s /c \"' + line + '\"', capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
# A5: ctypes で CreateProcessW を直接（監査フックで数えられない）、-I の子
_A5_CHILD = "import xv, sys; open(sys.argv[1], 'w').write(xv.VALUE)"
C["A5_ctypes_createprocess_I"] = (XV, (
    "import ctypes, os, sys, tempfile\nfrom ctypes import wintypes\n"
    "out = os.path.join(tempfile.gettempdir(), 'a5_%d.txt' % os.getpid())\n"
    f"child = {_A5_CHILD!r}\n"
    "cmd = '\"' + sys.executable + '\" -I -c \"' + child + '\" \"' + out + '\"'\n"
    "class SI(ctypes.Structure):\n"
    "    _fields_ = [('cb', wintypes.DWORD), ('r', wintypes.LPWSTR), ('d', wintypes.LPWSTR), ('t', wintypes.LPWSTR)] + [(n, wintypes.DWORD) for n in 'abcdefgh'] + [('w1', wintypes.WORD), ('w2', wintypes.WORD), ('p', ctypes.c_void_p), ('h1', wintypes.HANDLE), ('h2', wintypes.HANDLE), ('h3', wintypes.HANDLE)]\n"
    "class PI(ctypes.Structure):\n"
    "    _fields_ = [('hp', wintypes.HANDLE), ('ht', wintypes.HANDLE), ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]\n"
    "k = ctypes.WinDLL('kernel32', use_last_error=True)\n"
    "k.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p]\n"
    "si = SI(); si.cb = ctypes.sizeof(SI); pi = PI()\n"
    "buf = ctypes.create_unicode_buffer(cmd)\n"
    "ok = k.CreateProcessW(None, buf, None, None, False, 0x08000000, None, None, ctypes.byref(si), ctypes.byref(pi))\n"
    "k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]\n"
    "k.WaitForSingleObject(pi.hp, 60000)\n"
    "try:\n    v = open(out).read(); os.remove(out)\nexcept OSError as e:\n    v = 'ERR ' + type(e).__name__\n"
    "print('child:', bool(ok), v)\n"), ["xv"])
# A6: 適用でパッケージのフォルダが移るので、import せずにパッケージ内のファイルを読む（verify は本物のファイルが残る）
XVD = dict(XV)
XVD["xv/data.txt"] = "payload-42"
XVD["xv-1.0.dist-info/METADATA"] = "Metadata-Version: 2.1\nName: xv\nVersion: 1.0\n"
XVD["xv-1.0.dist-info/RECORD"] = "xv/__init__.py,,\nxv/data.txt,,\nxv-1.0.dist-info/METADATA,,\nxv-1.0.dist-info/RECORD,,\n"
XVD["xv-1.0.dist-info/top_level.txt"] = "xv\n"
C["A6_metadata_file_read"] = (XVD, (
    "from importlib.metadata import files\n"
    "p = [x for x in files('xv') if x.name == 'data.txt'][0]\n"
    "try:\n    print('data', p.read_text())\nexcept OSError as e:\n    print('data missing', type(e).__name__)\n"), ["xv"])
C["A6b_sitepackages_listdir"] = (XV, (
    "import os, sysconfig\n"
    "d = os.path.join(sysconfig.get_paths()['purelib'], 'xv')\n"
    "print(sorted(x for x in os.listdir(d) if x != '__pycache__') if os.path.isdir(d) else 'none')\n"), ["xv"])
# A7: 普段の標準出力がパイプ（保証の範囲「パイプ・ファイル」）、verify はファイル
C["A7_stdout_seekable_branch"] = (XV, (
    "import sys\n"
    "if not sys.stdout.seekable():\n    import xv\n    print('pipe', xv.f())\nelse:\n    print('file')\n"), ["xv"])
C["A7b_stdout_fifo_branch"] = (XV, (
    "import os, stat\n"
    "if stat.S_ISFIFO(os.fstat(1).st_mode):\n    import xv\n    print('pipe', xv.f())\nelse:\n    print('file')\n"), ["xv"])
# A8: サブインタープリター（3.13 の _interpreters）。起動用の sitecustomize が中で動くか・子孫に数えられるか
C["A8_subinterp_plus_I_child"] = (XV, (
    "import subprocess, sys\n"
    "try:\n    import _interpreters as I\nexcept ImportError:\n    import _xxsubinterpreters as I\n"
    "i = I.create()\nI.run_string(i, 'x = 1')\nI.destroy(i)\n"
    "r = subprocess.run([sys.executable, '-I', '-c', 'import xv; print(xv.VALUE)'], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["A8b_subinterp_uses_xv"] = (XV, (
    "try:\n    import _interpreters as I\nexcept ImportError:\n    import _xxsubinterpreters as I\n"
    "i = I.create()\nI.run_string(i, 'import xv, os; os.write(1, (xv.VALUE + chr(10)).encode())')\nI.destroy(i)\n"
    "print('done')\n"), ["xv"])
# 対照: 数え漏れの無い -I の子（未確認・import 確認済みになるはず）
C["Z1_control_I_child"] = (XV, (
    "import subprocess, sys\n"
    "r = subprocess.run([sys.executable, '-I', '-c', 'import xv; print(xv.VALUE)'], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["Z2_asyncio_child_uses"] = (XV, (
    "import asyncio, sys\n"
    "async def m():\n    p = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import xv; print(xv.VALUE)', stdout=asyncio.subprocess.PIPE)\n"
    "    o, _ = await p.communicate(); print('child:', o.decode().strip())\n"
    "asyncio.run(m())\n"), ["xv"])
C["Z3_ppe_child_uses"] = (XV, (
    "from concurrent.futures import ProcessPoolExecutor\n"
    "def w(_):\n    import xv\n    return xv.VALUE\n"
    "if __name__ == '__main__':\n    with ProcessPoolExecutor(2) as ex:\n        print(list(ex.map(w, range(2))))\n"), ["xv"])
C["Z5_asyncio_I_child"] = (XV, (
    "import asyncio, sys\n"
    "async def m():\n    p = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', 'import xv; print(xv.VALUE)', stdout=asyncio.subprocess.PIPE)\n"
    "    o, _ = await p.communicate(); print('child:', o.decode().strip())\n"
    "asyncio.run(m())\n"), ["xv"])
C["Z6_py_launcher_child"] = (XV, (
    "import subprocess, sys\n"
    "r = subprocess.run(['py', '-3.13', '-I', '-c', 'import sys; print(sys.version_info[:2])'], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])

if __name__ == "__main__":
    names = sys.argv[1:] or list(C)
    out = []
    for n in names:
        f, src, pk = C[n]
        try:
            r = case(n, f, src, pk)
            out.append((n, r["confirmed_run"], r["F3"], r.get("restored_ok"), r.get("verify_changed_venv")))
        except Exception as e:  # noqa
            import traceback; traceback.print_exc()
            out.append((n, "ERROR", str(e)[:200], None, None))
    print("SUMMARY " + json.dumps(out, ensure_ascii=False))
