"""重なりの実環境確認（基準 5）: 12_asammdf の venv で pandas 全体と pandas.io.html のスタブを両方の順序で適用・復元する。

各順序のあとで、RECORD の sha256 照合・pip freeze・バックアップ領域の残り・エントリーの出力を確かめる。
結果は C:\\tmp\\pystub-e2e\\after_round4\\overlap12\\result.json
"""
import base64, hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa: E402

S = Path(r"C:\tmp\pystub-e2e\12_asammdf")
V = S / "venv"
SP = V / "Lib" / "site-packages"
PY = V / "Scripts" / "python.exe"
BASH = r"C:\Program Files\Git\bin\bash.exe"
OUT = Path(r"C:\tmp\pystub-e2e\after_round4\overlap12")
OUT.mkdir(parents=True, exist_ok=True)
entry = str(S / "proj" / "entry.py")
expected = (S / "expected.txt").read_text(encoding="utf-8").replace("\r\n", "\n").strip()
freeze0 = (S / "freeze_orig.txt").read_text(encoding="utf-8").replace("\r", "")


def record_bad():
    bad = []
    for rec in SP.glob("*.dist-info/RECORD"):
        for line in rec.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            pth, h, _ = line.rsplit(",", 2)
            if not h.startswith("sha256="):
                continue
            f = SP / pth
            if not f.exists():
                bad.append("MISSING " + pth)
                continue
            d = base64.urlsafe_b64encode(hashlib.sha256(f.read_bytes()).digest()).rstrip(b"=").decode()
            if "sha256=" + d != h:
                bad.append("DIFF " + pth)
    return bad


def state():
    fr = subprocess.run([str(PY), "-m", "pip", "freeze"], capture_output=True, text=True).stdout.replace("\r", "")
    r = subprocess.run([str(PY), entry], capture_output=True, text=True)
    bak = V / "Lib" / "_pystub_bak"
    return {"freeze_equal": fr == freeze0, "output_equal": r.stdout.replace("\r\n", "\n").strip() == expected,
            "bak_left": bak.exists(), "record_bad": record_bad()[:20]}


def sh(lines, cwd):
    r = subprocess.run([BASH, "-c", "\n".join(lines)], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return {"rc": r.returncode, "out": (r.stdout + r.stderr)[-400:]}


def m1(g):
    out = []
    for line in g["build_instructions"]["uninstall_commands"]:
        if "方法2" in line:
            break
        out.append(line)
    return out


gp = generate_stubs(entry, "pandas", str(SP))
gs = generate_submodule_stubs(entry, "pandas", "pandas.io.html", str(SP))
assert "error" not in gp and "error" not in gs, (gp.get("error"), gs.get("error"))
st = OUT / "stage"
shutil.rmtree(st, ignore_errors=True)
for g in (gp, gs):
    for rel, code in g["files"].items():
        f = st / "_stubs" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(code, encoding="utf-8")
res = {"before": state()}
G = {"pkg": gp, "sub": gs}
for order in (("sub", "pkg"), ("pkg", "sub")):
    tag = "_then_".join(order)
    steps = {}
    for k in order:
        bi = G[k]["build_instructions"]
        steps[k + "_backup"] = sh(bi["backup_commands"], st)
        steps[k + "_install"] = sh(bi["install_commands"], st)
    steps["applied_bak_dirs"] = sorted(p.name for p in (V / "Lib" / "_pystub_bak").iterdir()) \
        if (V / "Lib" / "_pystub_bak").exists() else []
    for k in reversed(order):
        steps[k + "_restore"] = sh(m1(G[k]), st)
    res[tag] = {"steps": steps, "after_restore": state()}
    print(tag, json.dumps(res[tag]["after_restore"], ensure_ascii=False),
          {k: v["rc"] for k, v in steps.items() if isinstance(v, dict)}, steps["applied_bak_dirs"], flush=True)
(OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
