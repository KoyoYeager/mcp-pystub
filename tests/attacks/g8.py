"""F3 攻撃（第 8 回の新しい仕組みの穴）。python g8.py [名前...]"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")  # 出力だけ UTF-8（環境変数は変えない）
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from h8 import case  # noqa

XV = {"xv/__init__.py": "VALUE = 'real-value'\ndef f():\n    return 'real-f'\n"}
CHILD = "import xv; print(xv.VALUE)"
C = {}
# 子孫のプロセス: 環境変数を消して起動（sitecustomize が効かない・proc_dir に登録されない）
C["G1_child_env_cleared"] = (XV, (
    "import os, subprocess, sys\n"
    "env = {k: os.environ[k] for k in ('SYSTEMROOT', 'PATH') if k in os.environ}\n"
    f"r = subprocess.run([sys.executable, '-c', {CHILD!r}], env=env, capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["G2_child_E"] = (XV, (
    "import subprocess, sys\n"
    f"r = subprocess.run([sys.executable, '-E', '-c', {CHILD!r}], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["G3_child_I"] = (XV, (
    "import subprocess, sys\n"
    f"r = subprocess.run([sys.executable, '-I', '-c', {CHILD!r}], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
C["G4_child_pythonpath_popped"] = (XV, (
    "import os, subprocess, sys\n"
    "env = dict(os.environ); env.pop('PYTHONPATH', None)\n"
    f"r = subprocess.run([sys.executable, '-c', {CHILD!r}], env=env, capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
# 記録の取れる子だが、子が親の標準出力（fd 1）に直接書く（親の sys.stdout を通らない）
C["G5_child_direct_stdout"] = (XV, (
    "import subprocess, sys\n"
    f"subprocess.run([sys.executable, '-c', {CHILD!r}])\n"
    "print('parent done')\n"), ["xv"])
# 子の終了コードを見る（対照: 却下されるはず）
C["G5b_child_check_control"] = (XV, (
    "import subprocess, sys\n"
    f"r = subprocess.run([sys.executable, '-c', {CHILD!r}], capture_output=True, text=True)\n"
    "print('child:', r.stdout.strip(), r.returncode)\n"), ["xv"])
# verify の中の sys.stdout は StringIO（fileno / buffer / encoding が普段と違う）
C["G6_stdout_fileno_branch"] = (XV, (
    "import sys\n"
    "try:\n    fd = sys.stdout.fileno()\nexcept Exception:\n    fd = None\n"
    "if fd is not None:\n    import xv\n    print('console', xv.f())\nelse:\n    print('console none')\n"), ["xv"])
C["G7_utf8_mode_branch"] = (XV, (
    "import locale\n"
    "enc = locale.getpreferredencoding(False).lower()\n"
    "if enc not in ('utf-8', 'utf8'):\n    import xv\n    print('legacy', xv.f())\nelse:\n    print('utf8')\n"), ["xv"])
C["G8_stdout_encoding_branch"] = (XV, (
    "import sys\n"
    "if getattr(sys.stdout, 'encoding', None):\n    import xv\n    print('enc', xv.f())\nelse:\n    print('noenc')\n"), ["xv"])
C["G9_pythonioencoding_branch"] = (XV, (
    "import os\n"
    "if not os.environ.get('PYTHONIOENCODING'):\n    import xv\n    print('plain', xv.f())\nelse:\n    print('ioenc')\n"), ["xv"])
C["G10_reconfigure_usability"] = (XV, (
    "import sys\nsys.stdout.reconfigure(encoding='utf-8')\nimport xv\nprint('ok')\n"), ["xv"])
# 3 つ以上の候補: 確認済み（実行）だけの組の確かめ直しで見逃す組み合わせを狙う
REG3 = {"reg/__init__.py": "FORMAT = None\nN = []\n",
        "aplug/__init__.py": "import reg\nreg.N.append('a')\n",
        "bplug/__init__.py": "import reg\nreg.N.append('b')\n",
        "cnat/__init__.py": "import reg\nif 'a' not in reg.N and 'b' not in reg.N:\n    reg.N.append('a'); reg.N.append('b')\n",
        "cnat/_x.pyd": ""}
C["G11_three_cands_compensate"] = (REG3, "import reg, aplug, bplug, cnat\nprint(sorted(set(reg.N)))\n", ["aplug", "bplug", "cnat"])
# 子プロセスの出力（記録の取れる子）が終了コードを変えずに入る（multiprocessing の spawn、子が print）
C["G12_mp_child_prints"] = (XV, (
    "import multiprocessing as mp\n"
    "def w():\n    import xv\n    print('worker', xv.VALUE, flush=True)\n"
    "if __name__ == '__main__':\n    p = mp.Process(target=w); p.start(); p.join()\n    print('parent', p.exitcode)\n"), ["xv"])
C["G12b_mp_child_prints_noexit"] = (XV, (
    "import multiprocessing as mp\n"
    "def w():\n    import xv\n    print('worker', xv.VALUE, flush=True)\n"
    "if __name__ == '__main__':\n    p = mp.Process(target=w); p.start(); p.join()\n    print('parent')\n"), ["xv"])

if __name__ == "__main__":
    names = sys.argv[1:] or list(C)
    out = []
    for n in names:
        f, src, pk = C[n]
        try:
            r = case(n, f, src, pk)
            out.append((n, r["confirmed_run"], r["F3"], r.get("restored_ok")))
        except Exception as e:  # noqa
            import traceback; traceback.print_exc()
            out.append((n, "ERROR", str(e)[:200], None))
    print("SUMMARY " + json.dumps(out, ensure_ascii=False))
