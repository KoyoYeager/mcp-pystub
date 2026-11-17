"""F4 の新しい形: 既定の verify（import だけ）で、ローカルの helper が import 時に別の読み方でエントリーを読んで動かす。
目印のファイル MARK_TOP ができたらエントリーのコードが動いた。"""
import json, os, shutil, sys
sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
from mcp_pystub.verifier import verify  # noqa

ROOT = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "f4c"
LIB = {"heavy/__init__.py": "def f():\n    return 1\n", "unused/__init__.py": "def g():\n    return 3\n"}
EN = "my_long_entry_name.py"
MARK = "open(@TOP@, 'a').write('top')\n"
MAIN = "if __name__ == '__main__':\n    open(@MAIN@, 'a').write('main')\n    print(heavy.f())\n"
PRE = ("import os, sys, tempfile\nE = os.path.join(os.path.dirname(os.path.abspath(__file__)), " + repr(EN) + ")\n"
       "T = os.path.join(tempfile.mkdtemp(prefix='f4c_'), 'copy_entry.py')\n"
       "def run(src):\n    exec(compile(src, 'x', 'exec'), {'__name__': 'x'})\n")
H = {
    "mmap": "import mmap\nfd = os.open(E, os.O_RDONLY | getattr(os, 'O_BINARY', 0))\nm = mmap.mmap(fd, 0, access=mmap.ACCESS_READ)\nrun(m[:])\n",
    "open_code": "import io\nrun(io.open_code(E).read())\n",
    "pathlib": "from pathlib import Path\nrun(Path(E).read_text())\n",
    "linecache": "import linecache\nrun(''.join(linecache.getlines(E)))\n",
    "tokenize": "import tokenize\nrun(tokenize.open(E).read())\n",
    "copy2": "import shutil\nshutil.copy2(E, T)\nrun(open(T).read())\n",
    "copyfile": "import shutil\nshutil.copyfile(E, T)\nrun(open(T).read())\n",
    "hardlink": "os.link(E, T)\nrun(open(T).read())\n",
    "symlink": "os.symlink(E, T)\nrun(open(T).read())\n",
    "short83": ("import ctypes\nb = ctypes.create_unicode_buffer(1024)\nctypes.windll.kernel32.GetShortPathNameW(E, b, 1024)\n"
                "S = b.value\nopen(T + '.short', 'w').write(S)\nrun(open(S).read())\n"),
    "unc_admin": ("p = E.replace('C:', r'\\\\localhost\\C$', 1) if E[:2].upper() == 'C:' else E\nopen(T + '.unc', 'w').write(p)\n"
                  "run(open(p).read())\n"),
    "unc_ext": "p = '\\\\\\\\?\\\\' + E\nrun(open(p).read())\n",
    "upper_case": "run(open(E.upper()).read())\n",
    "ctypes_createfile": ("import ctypes\nfrom ctypes import wintypes\nk = ctypes.WinDLL('kernel32')\n"
                          "k.CreateFileW.restype = wintypes.HANDLE\n"
                          "k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]\n"
                          "h = k.CreateFileW(E, 0x80000000, 1, None, 3, 0x80, None)\n"
                          "buf = ctypes.create_string_buffer(1 << 16); n = wintypes.DWORD()\n"
                          "k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]\n"
                          "k.ReadFile(h, buf, 1 << 16, ctypes.byref(n), None)\nk.CloseHandle(h)\nrun(buf.raw[:n.value])\n"),
    "cmd_type": "import subprocess\nrun(subprocess.check_output(['cmd', '/c', 'type', E]))\n",
    "py_child_read": "import subprocess\nrun(subprocess.check_output([sys.executable, '-c', 'import sys; sys.stdout.write(open(sys.argv[1]).read())', E]))\n",
    "zipimport": ("import shutil, zipfile\nshutil.copy2(E, T)\nz = T + '.zip'\nzf = zipfile.ZipFile(z, 'w'); zf.write(T, 'zentry.py'); zf.close()\n"
                  "sys.path.insert(0, z)\nimport zentry\n"),
}


def write(base, files):
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


res = {}
names = sys.argv[1:] or list(H)
for name in names:
    for cname, kw in (("verify_default", {}), ("verify_record_only", {"record": True})):
        d = ROOT / f"{name}_{cname}"
        shutil.rmtree(d, ignore_errors=True)
        write(d / "sp", LIB)
        mt, mm = d / "MARK_TOP", d / "MARK_MAIN"
        ent = (MARK + "import heavy, unused, helper\n" + MAIN).replace("@TOP@", repr(str(mt))).replace("@MAIN@", repr(str(mm)))
        write(d / "proj", {EN: ent, "helper.py": PRE + H[name], "pyproject.toml": ""})
        e = d / "proj" / EN
        note = None
        try:
            out = verify(str(e), python_path=str(d / "sp"), **kw)
            note = {"blocked": out.get("blocked_entry_open"), "passed": out.get("passed"), "note": (out.get("note") or "")[:160],
                    "imported": out.get("imported_modules"), "bf": (out.get("baseline_failed") or {}).get("reason")}
            err = out.get("error")
        except Exception as ex:  # noqa
            err = repr(ex)[:300]
        res[f"{name}/{cname}"] = {"top": mt.exists(), "main": mm.exists(), "err": err, "note": note}
        print(name, cname, json.dumps(res[f"{name}/{cname}"], ensure_ascii=False), flush=True)
bad = {k: v for k, v in res.items() if v["top"] or v["main"]}
print("SUMMARY", len(res), "bad", len(bad), json.dumps(sorted(bad), ensure_ascii=False))
