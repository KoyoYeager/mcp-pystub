"""条件付き候補（docs/STUB_SAFETY_SPEC.md 9 節）のテスト

- 条件の機能を使わないエントリーでは、差し替えて出力が元と同じ
- 条件の機能を使うエントリーでは、「mcp-pystub のスタブです」と分かる例外で落ちる（黙って変わらない）
- conditional は stubbable / nofollow / submodule_stub_hints に混ざらない
"""

from __future__ import annotations

import importlib.machinery
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from mcp_pystub.analyzer import analyze
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs

EXT = importlib.machinery.EXTENSION_SUFFIXES[0]


def _write(root: Path, files: dict) -> None:
    for rel, code in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(code, bytes):
            p.write_bytes(code)
        else:
            p.write_text(textwrap.dedent(code), encoding="utf-8")


def _run(proj: Path, site: Path, entry: Path) -> tuple[int, str]:
    code = ("import sys; sys.path[:0]=%r; import runpy; runpy.run_path(%r, run_name='__main__')"
            % ([str(proj), str(site)], str(entry)))
    import os
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, timeout=60,
                       encoding="utf-8", errors="replace", env=env)
    return r.returncode, (r.stdout + r.stderr).strip()


def _rm(site: Path, name: str) -> None:
    base = site.joinpath(*name.split("."))
    for t in [base, base.with_name(base.name + ".py"), base.with_name(base.name + EXT)]:
        if t.is_dir():
            shutil.rmtree(t)
        elif t.exists():
            t.unlink()


def _put(site: Path, files: dict[str, str]) -> None:
    for rel, code in files.items():
        compile(code, rel, "exec")
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(code, encoding="utf-8")


class Case:
    def __init__(self, tmp: Path, sp_files: dict, ok_main: str, violate_main: str) -> None:
        self.sp, self.proj = tmp / "sp", tmp / "proj"
        _write(self.sp, sp_files)
        _write(self.proj, {"main.py": ok_main, "violate.py": violate_main, "pyproject.toml": ""})
        self.tmp = tmp
        self.res = analyze(str(self.proj / "main.py"), python_path=str(self.sp), include_small_conditional=True)

    def names(self, key: str) -> set[str]:
        return {p["package_name"] for p in self.res[key]}

    def apply(self, packages=(), hints=()) -> Path:
        site = self.tmp / "applied"
        if site.exists():
            shutil.rmtree(site)
        shutil.copytree(self.sp, site)
        for p in packages:
            g = generate_stubs(str(self.proj / "main.py"), p, python_path=str(self.sp))
            assert "error" not in g, g
            _rm(site, p)
            _put(site, g["files"])
        for sub, target in hints:
            g = generate_submodule_stubs(str(self.proj / "main.py"), sub.split(".")[0], sub, python_path=str(self.sp))
            assert "error" not in g, g
            _rm(site, sub)
            _put(site, g["files"])
            _rm(site, target)
        return site

    def check(self, site: Path) -> None:
        for entry, must_fail in (("main.py", False), ("violate.py", True)):
            rc0, out0 = _run(self.proj, self.sp, self.proj / entry)
            assert rc0 == 0, out0
            rc, out = _run(self.proj, site, self.proj / entry)
            if must_fail:
                assert rc != 0 and "mcp-pystub のスタブです" in out, out
                assert "PystubConditionalStubError" in out
            else:
                assert (rc, out) == (rc0, out0)


def test_name_dispatch_conditional_package(tmp_path):
    """asammdf Signal.plot 型: 名前だけの振り分けで届く機能だけが使う → conditional"""
    sp = {
        "gx/__init__.py": "def draw(v):\n    return 'drawn %s' % v\n",
        "sig/__init__.py": "class Signal:\n    def __init__(self, v):\n        self.v = v\n    def value(self):\n        return self.v\n    def plot(self):\n        import gx\n        return gx.draw(self.v)\n",
        "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
    }
    c = Case(tmp_path, sp, "import sig, other\nclass P:\n    def plot(self):\n        return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n",
             "import sig\nprint('ok', sig.Signal(3).plot())\n")
    assert "gx" in c.names("conditional")
    assert "gx" not in c.names("stubbable") | c.names("nofollow")
    cond = [x for x in c.res["conditional"] if x["package_name"] == "gx"][0]
    assert any("plot" in g["entry"] for g in cond["conditions"])
    assert cond["how_to_check"].startswith("verify(")
    c.check(c.apply(packages=["gx"]))


def test_branch_conditional_package(tmp_path):
    """入力次第の分岐だけで使う → conditional。条件を破ると分かる例外"""
    sp = {
        "hv/__init__.py": "def compute():\n    return 42\n",
        "lib/__init__.py": "import hv\ndef run(fast):\n    if fast:\n        return hv.compute()\n    return 0\n",
    }
    c = Case(tmp_path, sp, "import lib\nprint('ok', lib.run(False))\n", "import lib\nprint('ok', lib.run(True))\n")
    assert "hv" in c.names("conditional")
    cond = [x for x in c.res["conditional"] if x["package_name"] == "hv"][0]
    assert cond["conditions"][0]["public_api"] == "lib.run()"
    c.check(c.apply(packages=["hv"]))


def test_conditional_submodule_hint(tmp_path):
    """openpyxl.drawing.image → PIL 型: 入力次第の分岐で届くサブモジュールだけが C 拡張を import"""
    sp = {
        "cx/__init__.py": "def native():\n    return 'native'\n", f"cx/_core{EXT}": b"fake",
        "book/__init__.py": "from .reader import load\n",
        "book/image.py": "try:\n    import cx\nexcept ImportError:\n    cx = False\nclass Image:\n    def __init__(self, data):\n        self.data = cx.native() + data\n",
        "book/reader.py": "from book.image import Image, cx\ndef load(parts):\n    out = []\n    if not cx:\n        return out\n    for p in parts:\n        if p.startswith('img:'):\n            out.append(Image(p).data)\n        else:\n            out.append(p)\n    return out\n",
    }
    c = Case(tmp_path, sp, "import book\nprint('ok', book.load(['a', 'b']))\n",
             "import book\nprint('ok', book.load(['img:x']))\n")
    hints = {(h["submodule"], h["target_package"]) for h in c.res["conditional_submodule_hints"]}
    assert ("book.image", "cx") in hints
    assert not c.res["submodule_stub_hints"]
    c.check(c.apply(hints=[("book.image", "cx")]))


def test_import_time_use_is_not_conditional(tmp_path):
    """import 時に必ず実行される使用（cycler 型）は required のまま"""
    sp = {
        "cyc/__init__.py": "def cycler(*a):\n    return list(a)\n",
        "mpl/__init__.py": "import cyc\nclass Params(dict):\n    def __init__(self):\n        super().__init__()\n        self.update_all()\n    def update_all(self):\n        self['c'] = cyc.cycler('a')\nDEFAULT = Params()\n",
    }
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": "import mpl\nprint('ok')\n", "pyproject.toml": ""})
    res = analyze(str(tmp_path / "proj" / "main.py"), python_path=str(tmp_path / "sp"), include_small_conditional=True)
    assert "cyc" in {p["package_name"] for p in res["required"]}
    assert "cyc" not in {p["package_name"] for p in res["conditional"]}


def test_small_conditional_omitted_by_default(tmp_path):
    sp = {
        "hv/__init__.py": "def compute():\n    return 42\n",
        "lib/__init__.py": "import hv\ndef run(fast):\n    if fast:\n        return hv.compute()\n    return 0\n",
    }
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": "import lib\nprint(lib.run(False))\n", "pyproject.toml": ""})
    res = analyze(str(tmp_path / "proj" / "main.py"), python_path=str(tmp_path / "sp"))
    assert res["conditional"] == [] and res["conditional_omitted_small"] >= 1
    assert "hv" not in {p["package_name"] for p in res["stubbable"]}
