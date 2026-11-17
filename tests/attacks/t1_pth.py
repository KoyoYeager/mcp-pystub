"""T1: site-packages の .pth が起動時に import するパッケージ。記録は開始前の import を見ず（import_only）、verify の子プロセスでは
起動時に本物が読み込まれるのでスタブが使われない。実際に適用（venv の中で置き換え）すると変わるか。適用後は必ず戻す。"""
import json, os, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from mcp_pystub.verifier import verify  # noqa
from mcp_pystub.analyzer import analyze  # noqa
from mcp_pystub.stub_generator import generate_stubs  # noqa
from mcp_pystub import trace as tr  # noqa

REPO = str(__import__("pathlib").Path(__file__).resolve().parents[2])
D = (__import__("pathlib").Path(__import__("os").environ.get("PYSTUB_ATTACK_RUNS") or (__import__("pathlib").Path(__file__).resolve().parent / "runs"))) / "t1_pth"
V = D / "venv"
if not V.exists():
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(V)], check=True)
PY = V / "Scripts" / "python.exe"
SP = V / "Lib" / "site-packages"
(SP / "pthlib").mkdir(exist_ok=True)
(SP / "pthlib" / "__init__.py").write_text(
    "import os\ndef install():\n    os.environ['PTH_GREETING'] = 'hello'\n", encoding="utf-8")
(SP / "zz_pthlib.pth").write_text("import pthlib; pthlib.install()\n", encoding="utf-8")
(D / "proj").mkdir(parents=True, exist_ok=True)
(D / "proj" / "pyproject.toml").write_text("", encoding="utf-8")
E = D / "proj" / "entry.py"
E.write_text("import os, pthlib\nprint(os.environ.get('PTH_GREETING', 'none'))\n", encoding="utf-8")
env = dict(os.environ, PYTHONIOENCODING="utf-8")
env.pop("PYTHONPATH", None)


def run():
    r = subprocess.run([str(PY), str(E)], cwd=str(E.parent), capture_output=True, env=env)
    return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")[-400:]


base = run()
tf = D / "trace.json"
subprocess.run([str(PY), "-m", "mcp_pystub.trace", "--out", str(tf), str(E)], cwd=str(E.parent),
               env=dict(env, PYTHONPATH=REPO), check=True)
obs = tr.observe(tr.load(str(tf)), "pthlib", [str(SP)])["state"]
a = analyze(str(E), python_path=str(SP), trace_files=[str(tf)])
static = {x["package_name"]: k for k in ("stubbable", "conditional", "nofollow") for x in a.get(k, [])}
v = verify(str(E), python_path=str(SP), packages=["pthlib"], submodules=[], nofollow=[], run_entry=True, record=True)
res = {"base": base, "observed": obs, "static": static, "passed": v["passed"]["packages"],
       "status": v["status_summary"], "rejected": v["rejected"]}
if "pthlib" in v["status_summary"]["確認済み（実行）"]:
    g = generate_stubs(str(E), "pthlib", str(SP))
    bak = D / "bak_pthlib"
    shutil.rmtree(bak, ignore_errors=True)
    shutil.move(str(SP / "pthlib"), str(bak))
    try:
        for rel, code in g["files"].items():
            f = SP / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(code, encoding="utf-8")
        res["applied"] = run()
        res["applied_python_c"] = subprocess.run([str(PY), "-c", "print('python starts')"], capture_output=True,
                                                 env=env).returncode
    finally:
        shutil.rmtree(SP / "pthlib", ignore_errors=True)
        shutil.move(str(bak), str(SP / "pthlib"))
    res["restored"] = run()
    res["F3"] = res["applied"][:2] != base[:2]
print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
