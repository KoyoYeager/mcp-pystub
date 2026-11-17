"""F4 の別の形: 既定の verify・analyze・generate_* でエントリーのコードが動かないか（目印のファイル）。"""
import json, os, shutil, sys
from pathlib import Path
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
from mcp_pystub.analyzer import analyze, check_usage, inspect_graph  # noqa
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa
from mcp_pystub.verifier import verify  # noqa

ROOT = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "f4b"
LIB = {"heavy/__init__.py": "from heavy import gui\ndef f():\n    return 1\n",
       "heavy/gui.py": "import cext\ndef show():\n    return 2\n",
       "cext/__init__.py": "", "cext/_x.pyd": "",
       "unused/__init__.py": "def g():\n    return 3\n"}
MARK = "open(@TOP@, 'a').write('top')\n"
MAIN = "if __name__ == '__main__':\n    open(@MAIN@, 'a').write('main')\n    print(heavy.f())\n"


def write(base, files):
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")

# (エントリーの相対パス, {ファイル: 中身})
CASES = {
    # パッケージの __main__.py がエントリー。ローカルの helper が app.__main__ を import
    "pkg_main": ("app/__main__.py", {"app/__init__.py": "",
                 "app/__main__.py": MARK + "import heavy, unused\nfrom app import helper\n" + MAIN,
                 "app/helper.py": "def h():\n    return 1\nimport app.__main__\n"}),
    # ローカルのモジュールが importlib.import_module でエントリーを読む（静的なグラフに辺が無い）
    "importlib_dyn": ("entry.py", {"entry.py": MARK + "import heavy, unused, helper\n" + MAIN,
                      "helper.py": "import importlib\nNAME = 'ent' + 'ry'\nimportlib.import_module(NAME)\n"}),
    # ローカルのプラグインローダが import 時に同じフォルダの *.py をパスで読む（spec_from_file_location）
    "path_loader": ("entry.py", {"entry.py": MARK + "import heavy, unused, plugins\n" + MAIN,
                    "plugins.py": ("import glob, importlib.util, os\nLOADED = []\n"
                                   "for f in sorted(glob.glob(os.path.join(os.path.dirname(__file__), '*.py'))):\n"
                                   "    n = os.path.splitext(os.path.basename(f))[0]\n"
                                   "    if n in ('plugins',):\n        continue\n"
                                   "    s = importlib.util.spec_from_file_location('plug_' + n, f)\n"
                                   "    m = importlib.util.module_from_spec(s)\n    s.loader.exec_module(m)\n    LOADED.append(n)\n")}),
    # ローカルの helper が import 時に runpy.run_path でエントリーを動かす
    "runpy_path": ("entry.py", {"entry.py": MARK + "import heavy, unused, helper\n" + MAIN,
                   "helper.py": "import os, runpy\nif os.environ.get('NEVER_SET_X') is None:\n    pass\ndef go():\n    return 1\n"
                                "runpy.run_path(os.path.join(os.path.dirname(__file__), 'entry.py'), run_name='side')\n"}),
    # runpy.run_module（finder を通る。止まるはず）
    "runpy_module": ("entry.py", {"entry.py": MARK + "import heavy, unused, helper\n" + MAIN,
                     "helper.py": "import runpy\nrunpy.run_module('entry', run_name='side')\n"}),
    # エントリーと同名の別モジュール（sub/entry.py）。こちらは import されてよい（目印は別）
    "same_name_other": ("entry.py", {"entry.py": MARK + "import heavy, unused\nfrom sub import entry as other\n" + MAIN,
                        "sub/__init__.py": "", "sub/entry.py": "open(@OTHER@, 'a').write('other')\nX = 1\n"}),
    # python -m pkg.cli の形（相対 import、core が cli を import する循環）
    "m_form_cycle": ("pkg/cli.py", {"pkg/__init__.py": "",
                     "pkg/cli.py": MARK + "import heavy, unused\nfrom pkg import core\n" + MAIN,
                     "pkg/core.py": "def c():\n    from pkg import cli\n    return cli\nfrom . import cli as _cli\n"}),
    # テスト用のモジュール（tests/util.py）がエントリーを import する
    "tests_import_entry": ("entry.py", {"entry.py": MARK + "import heavy, unused\nimport tests.util\n" + MAIN,
                           "tests/__init__.py": "", "tests/util.py": "import entry\n",
                           "conftest.py": "import entry\n"}),
    # エントリーの .pyw（Windows の SOURCE_SUFFIXES に .pyw）
    "pyw_entry": ("gui.pyw", {"gui.pyw": MARK + "import heavy, unused, helper\n" + MAIN, "helper.py": "import gui\n"}),
    # exec(open(entry).read()) を import 時に行う
    "exec_open": ("entry.py", {"entry.py": MARK + "import heavy, unused, helper\n" + MAIN,
                  "helper.py": "import os\nexec(open(os.path.join(os.path.dirname(__file__), 'entry.py')).read(), {'__name__': 'x'})\n"}),
}
res = {}
for name, (erel, files) in CASES.items():
    calls = {
        "analyze": lambda e, sp: analyze(str(e), python_path=str(sp)),
        "graph": lambda e, sp: inspect_graph(str(e), str(sp), 5),
        "check": lambda e, sp: check_usage(str(e), "heavy", str(sp), 5),
        "generate": lambda e, sp: generate_stubs(str(e), "unused", str(sp)),
        "generate_submodule": lambda e, sp: generate_submodule_stubs(str(e), "heavy", "heavy.gui", str(sp)),
        "verify_default": lambda e, sp: verify(str(e), python_path=str(sp)),
        "verify_record_only": lambda e, sp: verify(str(e), python_path=str(sp), record=True),
        "verify_explicit": lambda e, sp: verify(str(e), python_path=str(sp), packages=["unused"], submodules=[], nofollow=[]),
        "CONTROL_run_entry": lambda e, sp: verify(str(e), python_path=str(sp), packages=["unused"], submodules=[], nofollow=[], run_entry=True),
    }
    for cname, fn in calls.items():
        d = ROOT / f"{name}_{cname}"
        shutil.rmtree(d, ignore_errors=True)
        write(d / "sp", LIB)
        mt, mm, mo = d / "MARK_TOP", d / "MARK_MAIN", d / "MARK_OTHER"
        fs = {k: v.replace("@TOP@", repr(str(mt))).replace("@MAIN@", repr(str(mm))).replace("@OTHER@", repr(str(mo)))
              for k, v in files.items()}
        write(d / "proj", dict(fs, **{"pyproject.toml": ""}))
        e = d / "proj" / erel
        note = None
        try:
            out = fn(e, d / "sp")
            err = out.get("error") if isinstance(out, dict) else None
            if isinstance(out, dict) and "excluded_from_import" in out:
                note = {"excl": out.get("excluded_from_import"), "passed": out.get("passed"),
                        "bf": (out.get("baseline_failed") or {}).get("reason") if out.get("baseline_failed") else None}
        except Exception as ex:  # noqa
            err = repr(ex)[:300]
        res[f"{name}/{cname}"] = {"top": mt.exists(), "main": mm.exists(), "other": mo.exists(), "err": err, "note": note}
        print(name, cname, json.dumps(res[f"{name}/{cname}"], ensure_ascii=False), flush=True)
bad = {k: v for k, v in res.items() if (v["top"] or v["main"]) and "CONTROL" not in k}
ctl = {k: (v["top"], v["main"]) for k, v in res.items() if "CONTROL" in k}
print("CONTROL", json.dumps(ctl))
print("SUMMARY", len(res), "bad", json.dumps(bad, ensure_ascii=False))
