"""仕上げの回 B3: 静的解析の候補のスタブで、モジュールの __getattr__ が遅れて作るクラスの代わりに元に無い属性を聞くと
AttributeError（246d7d8 と同じ）。元にある属性は値の代わり（使えば止まる）。"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs  # noqa: E402

D = Path(r"C:\tmp\pystub-e2e\designed")
T01 = Path(r"C:\tmp\pystub-e2e\01_tqdm")


def _write(base: Path, files: dict) -> None:
    for rel, code in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(code, encoding="utf-8")


def _run(py, stub_dir, code):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "MCP_PYSTUB_STRICT")}
    env["PYTHONPATH"] = str(stub_dir)
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip(), r.stderr.strip()[-300:]


def test_lazy_class_unknown_attr_synthetic(tmp_path):
    sp = tmp_path / "sp"
    _write(sp, {"hv/__init__.py": "class Frame:\n    total = 1\n    def show(self):\n        return 2\n"
                                  "class Other:\n    x = 1\n",
                "lib/__init__.py": "import hv\n"})
    proj = tmp_path / "proj"
    _write(proj, {"entry.py": "import lib\nprint('ok')\n", "pyproject.toml": ""})
    g = generate_stubs(str(proj / "entry.py"), "hv", str(sp))
    assert "error" not in g, g
    out = tmp_path / "stub"
    _write(out, g["files"])
    rc, o, e = _run(sys.executable, out, "import hv\nprint(hasattr(hv.Frame, 'nope_xyz'), hasattr(hv.Frame, 'total'), "
                                         "getattr(hv.Other, 'nope_xyz', None) is None, hasattr(hv.Other, 'x'))")
    assert (rc, o) == (0, "False True True True"), (rc, o, e)


@pytest.mark.skipif(not (D / "venv").exists(), reason="設計検証の venv がありません")
def test_lazy_class_unknown_attr_designed(tmp_path):
    E, SP, PY = D / "proj" / "entry.py", D / "venv" / "Lib" / "site-packages", D / "venv" / "Scripts" / "python.exe"
    # stubbable
    g = generate_stubs(str(E), "heavyframe", str(SP))
    o1 = tmp_path / "hf"
    _write(o1, g["files"])
    rc, o, e = _run(PY, o1, "from heavyframe import Frame\nprint(hasattr(Frame, 'nope_xyz'), getattr(Frame, 'nope_xyz', None) is None)")
    assert (rc, o) == (0, "False True"), (rc, o, e)
    # サブモジュールのヒント
    g = generate_submodule_stubs(str(E), "libconv", "libconv.gui", str(SP))
    o2 = tmp_path / "lg"
    shutil.copytree(SP / "libconv", o2 / "libconv", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(o2 / "libconv" / "gui")
    _write(o2, g["files"])
    rc, o, e = _run(PY, o2, "import sys, libconv.gui\nW = sys.modules['libconv.gui.plot'].Window\n"
                            "print(hasattr(W, 'nope_xyz'), getattr(W, 'nope_xyz', None) is None)")
    assert (rc, o) == (0, "False True"), (rc, o, e)


@pytest.mark.skipif(not (T01 / "venv").exists(), reason="01_tqdm の venv がありません")
def test_lazy_class_unknown_attr_pandas(tmp_path):
    E, SP, PY = T01 / "proj" / "entry.py", T01 / "venv" / "Lib" / "site-packages", T01 / "venv" / "Scripts" / "python.exe"
    g = generate_stubs(str(E), "pandas", str(SP))
    o1 = tmp_path / "pd"
    _write(o1, g["files"])
    rc, o, e = _run(PY, o1, "import pandas.core.algorithms as a\nprint(hasattr(a.CategoricalDtype, 'nope_xyz'))")
    assert (rc, o) == (0, "False"), (rc, o, e)
