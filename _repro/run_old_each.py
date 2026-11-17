"""修正前コード（_repro/old）で回帰テストを 1 件ずつ（各 60 秒上限）実行する。"""
import subprocess, sys
from pathlib import Path

OLD = Path(__file__).resolve().parent / "old"
ids = subprocess.run(
    [sys.executable, "-m", "pytest", "--rootdir", ".", "--collect-only", "-q", "-p", "no:cacheprovider", "tests/test_regressions.py"],
    cwd=OLD, capture_output=True, text=True,
).stdout.splitlines()
ids = [i for i in ids if "::" in i]
res = {"passed": [], "failed": [], "timeout": []}
for tid in ids:
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "--rootdir", ".", "-q", "-p", "no:cacheprovider", tid],
                           cwd=OLD, capture_output=True, text=True, timeout=60)
        res["passed" if r.returncode == 0 else "failed"].append(tid)
    except subprocess.TimeoutExpired:
        res["timeout"].append(tid)
    print(tid.split("::", 1)[1], "->", [k for k, v in res.items() if tid in v][0], flush=True)
print({k: len(v) for k, v in res.items()})
