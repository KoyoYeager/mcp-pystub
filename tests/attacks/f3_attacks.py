"""F3 / N の攻撃（合成）。python f3_attacks.py [名前...]"""
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from f3h import case  # noqa

REG = {"reg/__init__.py": "NAMES = set()\ndef add(n):\n    NAMES.add(n)\n",
       "xplug/__init__.py": "import reg\nreg.add('xplug')\n"}

CASES = {}

# L1: 比べる標準出力は末尾 20000 文字だけ（verifier.py run_entry: buf.getvalue()[-20000:]）
CASES["L1_stdout_tail_20000"] = (REG, (
    "import reg, xplug\nprint('plugins:', sorted(reg.NAMES))\n"
    "for i in range(2000):\n    print('row', i, i * i)\n"), ["xplug"], "record")
# L1b: 対照（短い出力）: verify が却下するはず
CASES["L1b_short_control"] = (REG, "import reg, xplug\nprint('plugins:', sorted(reg.NAMES))\n", ["xplug"], "record")

# P1: trace_files（record なし）の verify はグラフ上のモジュールを先に全部 import する → 実行時に import されない
#     zcompat が同じ登録をして、xplug を外した差を隠す
CASES["P1_preimport_masking_tracefiles"] = (dict(REG, **{"zcompat/__init__.py": "import reg\nreg.add('xplug')\n"}), (
    "import reg, xplug\ndef legacy():\n    import zcompat\nprint(sorted(reg.NAMES))\n"), ["xplug"], "tracefiles")
CASES["P1b_same_record_mode"] = (dict(REG, **{"zcompat/__init__.py": "import reg\nreg.add('xplug')\n"}), (
    "import reg, xplug\ndef legacy():\n    import zcompat\nprint(sorted(reg.NAMES))\n"), ["xplug"], "record")

# S1: passed の組は一緒に確かめるが、状態は項目ごと。確認済み（実行）だけを適用すると変わる
#     （bfall は拡張モジュールを含む → unknown_native → import 確認済み止まり）
CASES["S1_subset_of_passed"] = ({
    "reg/__init__.py": "FORMAT = None\n",
    "aplug/__init__.py": "import reg\nreg.FORMAT = 'plain'\n",
    "bfall/__init__.py": "import reg\nif reg.FORMAT is None:\n    reg.FORMAT = 'fancy'\n",
    "bfall/_speed.pyd": "",
}, "import reg, aplug, bfall\nprint(reg.FORMAT or 'plain')\n", ["aplug", "bfall"], "record")

# N 系: 記録が「未実行」と誤る例（verify が却下できるか）
CASES["N1_dataclass_generated_methods"] = ({
    "xdc/__init__.py": "from dataclasses import dataclass\n@dataclass\nclass P:\n    x: int\n    y: int = 2\n"},
    "import xdc\np = xdc.P(1)\nprint(p.x + p.y, p)\n", ["xdc"], "record")
CASES["N2_exec_defined_function"] = ({
    "xex/__init__.py": "exec('def triple(x):\\n    return x * 3\\n', globals())\n"},
    "import xex\nprint(xex.triple(2))\n", ["xex"], "record")
CASES["N3_namedtuple"] = ({
    "xnt/__init__.py": "from collections import namedtuple\nPt = namedtuple('Pt', 'a b')\n"},
    "import xnt\np = xnt.Pt(1, 2)\nprint(p, p._replace(a=5))\n", ["xnt"], "record")
CASES["N4_c_callback_key"] = ({
    "xkey/__init__.py": "def k(v):\n    return -v\n"},
    "import xkey\nprint(sorted([3, 1, 2], key=xkey.k))\n", ["xkey"], "record")
CASES["N5_thread"] = ({
    "xth/__init__.py": "def work(out):\n    out.append(7)\n"},
    "import threading, xth\no = []\nt = threading.Thread(target=xth.work, args=(o,))\nt.start(); t.join()\nprint(o)\n",
    ["xth"], "record")
CASES["N6_async"] = ({
    "xas/__init__.py": "async def f():\n    return 9\n"},
    "import asyncio, xas\nprint(asyncio.run(xas.f()))\n", ["xas"], "record")
CASES["N7_low_level_thread"] = ({
    "xlt/__init__.py": "def work(out, lock):\n    out.append(8)\n    lock.release()\n"},
    "import _thread, xlt\no = []\nl = _thread.allocate_lock(); l.acquire()\n_thread.start_new_thread(xlt.work, (o, l))\nl.acquire()\nprint(o)\n",
    ["xlt"], "record")
CASES["N8_runtime_lazy_submodule_import"] = ({
    "xlz/__init__.py": "def compute():\n    return 41\n",
    "xlz/sub.py": "from xlz import compute\nVALUE = compute() + 1\n"},
    "import xlz\ndef go():\n    import xlz.sub\n    return xlz.sub.VALUE\nprint(go())\n", ["xlz"], "record")
CASES["N9_pth_path_outside_site"] = ({
    "extra.pth": "",  # 下で書き換え
}, "import xout\nprint(xout.f())\n", ["xout"], "record")

def _main():
    names = sys.argv[1:] or list(CASES)
    out = []
    for n in names:
        files, src, pk, mode = CASES[n]
        if n == "N9_pth_path_outside_site":
            # パッケージは site の外（.pth で sys.path に足す形。editable install と同じ置き場）。合成では PYTHONPATH に足す
            from pathlib import Path
            from f3h import ROOT, write
            ext = ROOT / "N9_ext"
            write(ext, {"xout/__init__.py": "def f():\n    return 'used'\n"})
            files = {"keep/__init__.py": ""}
            import os
            os.environ["PYTHONPATH_EXTRA"] = str(ext)
        out.append(case(n, files, src, pk, mode))
    print("SUMMARY", json.dumps([(r["case"], r["observed"], r["confirmed_run"], r["F3"]) for r in out], ensure_ascii=False))

CASES["N10_multiprocessing_worker"] = ({
    "xmp/__init__.py": "def sq(x):\n    return x * x\n"},
    "import concurrent.futures as cf, xmp\nif __name__ == '__main__':\n"
    "    with cf.ProcessPoolExecutor(2) as ex:\n        print(list(ex.map(xmp.sq, [1, 2, 3])))\n", ["xmp"], "record")
CASES["N11_dunder_file_in_trace"] = ({"xk/__init__.py": "K = 1\n"},
    "import os, xk\nprint(os.path.isabs(__file__))\n", ["xk"], "record")
if __name__ == "__main__" and False:
    pass
if __name__ == "__main__":
    _main()
