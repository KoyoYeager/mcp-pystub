"""F3 攻撃の追加: python g8b.py [名前...]"""
import json, os, sys
sys.stdout.reconfigure(encoding="utf-8")  # 出力だけ UTF-8（環境変数は変えない）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import h8  # noqa
from h8 import case  # noqa

XV = {"xv/__init__.py": "VALUE = 'real-value'\ndef f():\n    return 'real-f'\n"}
C = {}
# 子に PYTHONPATH を「上書き」で渡す（よくある書き方。sitecustomize のフォルダが消える）
C["G4b_child_pythonpath_overridden"] = (XV, (
    "import os, subprocess, sys\n"
    "env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(__file__)))\n"
    "r = subprocess.run([sys.executable, '-c', 'import xv; print(xv.VALUE)'], env=env, capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"], {})
# 利用者の環境の PYTHONPATH（verify は落とす）で分岐する
C["G13_user_pythonpath_dropped"] = (XV, (
    "try:\n    import fastpath\nexcept ImportError:\n    fastpath = None\n"
    "if fastpath is not None:\n    import xv\n    print('fast', xv.f())\nelse:\n    print('slow')\n"), ["xv"], {"PYTHONPATH": "@USERLIB@"})
# パッケージが import 時に atexit で標準出力へ書く（記録は終了前に止まる・終了時の標準出力は比べない）
C["G14_atexit_stdout"] = ({"xat/__init__.py": "import atexit\ndef _bye():\n    print('bye from xat')\natexit.register(_bye)\n"},
    "import xat\nprint('main')\n", ["xat"], {})

if __name__ == "__main__":
    names = sys.argv[1:] or list(C)
    out = []
    for n in names:
        f, src, pk, envx = C[n]
        saved = os.environ.get("PYTHONPATH")
        if envx.get("PYTHONPATH") == "@USERLIB@":
            lib = h8.ROOT / (n + "_userlib")
            h8.write(lib, {"fastpath.py": "X = 1\n"})
            envx = {"PYTHONPATH": str(lib)}
            os.environ["PYTHONPATH"] = str(lib)  # verify を呼ぶ側（MCP サーバー）の環境にもある
        try:
            r = case(n, f, src, pk, env_extra=envx)
        finally:
            if saved is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = saved
        out.append((n, r["statuses"], r["confirmed_run"], r["base"], r.get("applied"), r["F3"], r.get("restored_ok")))
    print("SUMMARY " + json.dumps(out, ensure_ascii=False))
