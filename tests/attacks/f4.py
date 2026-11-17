"""F4: エントリーを実行しないはずの呼び出しで、エントリー（直下 / __main__ の中）が動くか。目印のファイルで確かめる。"""
import json, shutil, sys
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from mcp_pystub.analyzer import analyze, check_usage, inspect_graph  # noqa
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa
from mcp_pystub.verifier import verify  # noqa

ROOT = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "f4"
LIB = {"heavy/__init__.py": "from heavy import gui\ndef f():\n    return 1\n",
       "heavy/gui.py": "import cext\ndef show():\n    return 2\n",
       "cext/__init__.py": "", "cext/_x.pyd": "",
       "unused/__init__.py": "def g():\n    return 3\n"}


def write(base, files):
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")


def mk(name, entry_src, extra=None):
    d = ROOT / name
    shutil.rmtree(d, ignore_errors=True)
    write(d / "sp", LIB)
    m_top, m_main = d / "MARK_TOP", d / "MARK_MAIN"
    src = entry_src.replace("@TOP@", repr(str(m_top))).replace("@MAIN@", repr(str(m_main)))
    write(d / "proj", {"entry.py": src, "pyproject.toml": "", **(extra or {})})
    return d, d / "proj" / "entry.py", d / "sp", m_top, m_main


ENTRY = ("open(@TOP@, 'a').write('top\\n')\nimport heavy, unused\nimport helper\n"
         "if __name__ == '__main__':\n    open(@MAIN@, 'a').write('main\\n')\n    print(heavy.f())\n")
CASES = {
    # 普通の形（ローカルの helper はエントリーを import しない）
    "plain": (ENTRY, {"helper.py": "X = 1\n"}),
    # ローカルのモジュールがエントリーを import する（循環。Flask の `from app import app` の形）
    "circular": (ENTRY, {"helper.py": "def h():\n    import entry\n    return entry\n"}),
    "circular_top": (ENTRY, {"helper.py": "import entry\n"}),
}
res = {}
for name, (src, extra) in CASES.items():
    calls = {
        "analyze": lambda e, sp: analyze(str(e), python_path=str(sp)),
        "graph": lambda e, sp: inspect_graph(str(e), str(sp), 5),
        "check": lambda e, sp: check_usage(str(e), "heavy", str(sp), 5),
        "generate": lambda e, sp: generate_stubs(str(e), "unused", str(sp)),
        "generate_submodule": lambda e, sp: generate_submodule_stubs(str(e), "heavy", "heavy.gui", str(sp)),
        "verify_default": lambda e, sp: verify(str(e), python_path=str(sp)),
        "verify_record_only": lambda e, sp: verify(str(e), python_path=str(sp), record=True),
        "verify_explicit_lists": lambda e, sp: verify(str(e), python_path=str(sp), packages=["unused"], submodules=[],
                                                      nofollow=[]),
    }
    for cname, fn in calls.items():
        d, e, sp, mt, mm = mk(name + "_" + cname, src, extra)
        try:
            out = fn(e, sp)
            err = out.get("error") if isinstance(out, dict) else None
        except Exception as ex:  # noqa
            err = repr(ex)
        res[f"{name}/{cname}"] = {"top": mt.exists(), "main": mm.exists(), "err": err}
        print(name, cname, res[f"{name}/{cname}"], flush=True)
print("SUMMARY", json.dumps(res, ensure_ascii=False))
