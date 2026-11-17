"""N: 記録が executed を import_only / not_imported に取りこぼす例（verify が却下できるか）。python n8.py [名前...]"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")  # 出力だけ UTF-8（環境変数は変えない）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from h8 import case  # noqa

XLZ = {"xlz/__init__.py": "def compute():\n    return 41\n",
       "xlz/sub.py": "from xlz import compute\nVALUE = compute() + 1\n"}
C = {}
# 別のパッケージの関数が実行時に xlz.sub を遅れて import（N8 の修正はエントリーの関数からの遅れた import だけ）
C["N8b_lazy_import_from_other_pkg"] = (dict(XLZ, **{"other/__init__.py": "def run():\n    import xlz.sub\n    return xlz.sub.VALUE\n"}),
    "import other\nprint(other.run())\n", ["xlz"])
# ローカルの helper モジュールの関数が遅れて import
C["N8c_lazy_import_from_local_helper"] = (XLZ, "import helper\nprint(helper.go())\n", ["xlz"],
    {"helper.py": "def go():\n    import xlz.sub\n    return xlz.sub.VALUE\n"})
# 生成した関数（self なし、globals の __name__ が実在しない）: 所属が分からない
C["N12_generated_func_no_self"] = ({"xgen/__init__.py": (
    "ns = {}\nexec(compile('def triple(x):\\n    return x * 3\\n', '<xgen generated>', 'exec'), {'__name__': 'xgen_gen'}, ns)\n"
    "triple = ns['triple']\n")}, "import xgen\nprint(xgen.triple(2))\n", ["xgen"])
# ファイル名が実在しないパス風（テンプレートの compile 等）
C["N13_compiled_virtual_filename"] = ({"xtpl/__init__.py": (
    "ns = {}\nexec(compile('def render(x):\\n    return \"<\" + str(x) + \">\"\\n', r'C:\\\\virtual\\\\xtpl_template.py', 'exec'), ns)\n"
    "render = ns['render']\n")}, "import xtpl\nprint(xtpl.render(5))\n", ["xtpl"])
# 別のパッケージの import の途中で xv の関数が呼ばれる（executed に数えるはず: 対照）
C["N14_cross_pkg_import_time_call"] = ({"xv/__init__.py": "def setup():\n    return 'S'\n",
                                        "libb/__init__.py": "import xv\nMODE = xv.setup()\n"},
    "import libb\nprint(libb.MODE)\n", ["xv"])

if __name__ == "__main__":
    names = sys.argv[1:] or list(C)
    out = []
    for n in names:
        spec = C[n]
        f, src, pk = spec[:3]
        extra = spec[3] if len(spec) > 3 else None
        r = case(n, f, src, pk, extra_proj=extra)
        out.append((n, {k: v[1] for k, v in r["statuses"].items()}, r["confirmed_run"], r["F3"]))
    print("SUMMARY " + json.dumps(out, ensure_ascii=False))
