"""第 4 回の修正の回帰テスト（重なりの順序・二重適用・中断、verify_commands の名前空間検出）。"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
from tests.test_conditional import EXT, _write

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash がありません")

SP = {
    "cx/__init__.py": "def native():\n    return 1\n", f"cx/_core{EXT}": b"fake",
    "pk/__init__.py": "def f():\n    return 'f'\n", "pk/big.py": "X = 1\n",
    "pk/io/__init__.py": "",
    "pk/io/x.py": "def g():\n    import cx\n    return cx.native()\n",
}


class Env:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.site = tmp_path / "sp"
        _write(self.site, SP)
        _write(tmp_path / "proj", {"main.py": "import pk, pk.io.x\nprint('ok')\n", "pyproject.toml": ""})
        self.entry = str(tmp_path / "proj" / "main.py")
        self.before = self.tree()
        self.gp = generate_stubs(self.entry, "pk", python_path=str(self.site))
        self.gs = generate_submodule_stubs(self.entry, "pk", "pk.io.x", python_path=str(self.site))
        assert "error" not in self.gp and "error" not in self.gs
        self.stage = tmp_path / "stage"
        for g in (self.gp, self.gs):
            for rel, code in g["files"].items():
                f = self.stage / "_stubs" / rel
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(code, encoding="utf-8")

    def tree(self) -> dict:
        return {str(f.relative_to(self.site)): hashlib.md5(f.read_bytes()).hexdigest()
                for f in sorted(self.site.rglob("*")) if f.is_file() and "__pycache__" not in f.parts}

    def sh(self, lines: list[str]) -> int:
        r = subprocess.run([BASH, "-c", "\n".join(lines)], cwd=self.stage, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode

    def apply(self, g) -> int:
        bi = g["build_instructions"]
        rc = self.sh(bi["backup_commands"])
        return rc or self.sh(bi["install_commands"])

    @staticmethod
    def m1(g) -> list[str]:
        out = []
        for line in g["build_instructions"]["uninstall_commands"]:
            if "方法2" in line:
                break
            out.append(line)
        return out

    def restore(self, g) -> int:
        return self.sh(self.m1(g))

    def lost(self) -> list[str]:
        t = self.tree()
        return sorted(k for k in self.before if t.get(k) != self.before[k])

    def assert_clean(self):
        assert self.tree() == self.before
        assert not (self.root / "_pystub_bak").exists()


ORDERS = [
    ("sub", "pkg"), ("pkg", "sub"),
]


@pytest.mark.parametrize("first,second", ORDERS)
@pytest.mark.parametrize("restore_order", ["same", "reverse"])
def test_overlap_any_order_never_loses_files(tmp_path, first, second, restore_order):
    e = Env(tmp_path)
    g = {"sub": e.gs, "pkg": e.gp}
    e.apply(g[first])
    rc2 = e.apply(g[second])
    assert rc2 in (0, 1)
    if first == "sub":
        assert rc2 == 1, "内側が適用中なら外側の適用は断る"
    order = [first, second] if restore_order == "same" else [second, first]
    for k in order:
        assert e.restore(g[k]) == 0
    e.assert_clean()


@pytest.mark.parametrize("which", ["sub", "pkg"])
def test_double_apply_and_restore(tmp_path, which):
    e = Env(tmp_path)
    g = e.gs if which == "sub" else e.gp
    assert e.apply(g) == 0
    stubbed = e.tree()
    assert e.apply(g) == 0
    assert e.tree() == stubbed
    assert e.restore(g) == 0
    assert e.restore(g) == 0
    e.assert_clean()


@pytest.mark.parametrize("which", ["sub", "pkg"])
@pytest.mark.parametrize("cut", range(1, 6))
def test_interrupted_backup_then_retry_or_restore(tmp_path, which, cut):
    """バックアップ・配置の途中（先頭 cut 行まで）で止まっても、やり直し・復元で元に戻る。"""
    e = Env(tmp_path)
    g = e.gs if which == "sub" else e.gp
    bi = g["build_instructions"]
    lines = bi["backup_commands"] + bi["install_commands"]
    e.sh(lines[:cut])
    # 途中で止まった直後に復元
    assert e.restore(g) == 0
    e.assert_clean()
    # 途中で止まった後にやり直して、復元
    e.sh(lines[:cut])
    assert e.apply(g) == 0
    assert e.restore(g) == 0
    e.assert_clean()


@pytest.mark.parametrize("cut", range(1, 4))
def test_interrupted_restore_then_retry(tmp_path, cut):
    e = Env(tmp_path)
    assert e.apply(e.gp) == 0
    m1 = e.m1(e.gp)
    e.sh(m1[:cut])
    assert e.restore(e.gp) == 0
    e.assert_clean()


def test_verify_commands_detect_emptied_package(tmp_path):
    """中身の消えたパッケージ（名前空間パッケージとして import できてしまう）を verify_commands が検出する。"""
    e = Env(tmp_path)
    cmds = e.gp["build_instructions"]["verify_commands"]
    py = sys.executable.replace("\\", "/")

    def run_all() -> list[int]:
        out = []
        for c in cmds:
            c2 = c.replace("python -c", f'"{py}" -c', 1)
            r = subprocess.run([BASH, "-c", c2], cwd=str(e.site), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            out.append(r.returncode)
        return out

    assert all(rc == 0 for rc in run_all())
    # 中身を消して空ディレクトリだけ残す
    for f in (e.site / "pk").rglob("*"):
        if f.is_file():
            f.unlink()
    assert any(rc != 0 for rc in run_all()), "空になった pk を OK と報告してはならない"


# ---------------------------------------------------------------------------
# 第 4 回: 解析・生成・verify の決定（仕様 10 節）
# ---------------------------------------------------------------------------

from mcp_pystub.analyzer import analyze  # noqa: E402
from mcp_pystub.size_estimator import estimate_package_size  # noqa: E402
from mcp_pystub.verifier import verify  # noqa: E402
from tests.test_conditional import Case, _run  # noqa: E402

SIG4 = {
    "gx/__init__.py": "def draw(v):\n    return 'drawn %s' % v\nLEVEL = int('3')\nTABLE = dict(a=1)\n",
    "gx/extra/__init__.py": "def more():\n    return 1\n",
    "gx/extra/deep.py": "X = 1\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v):\n        self.v = v\n    def value(self):\n        return self.v\n"
                       "    def plot(self):\n        import gx\n        return gx.draw(self.v)\n",
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}
MAIN4 = "import sig, other\nclass P:\n    def plot(self):\n        return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n"


def _analyze(tmp_path, sp, main):
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": main, "pyproject.toml": ""})
    return analyze(str(tmp_path / "proj" / "main.py"), python_path=str(tmp_path / "sp"), include_small_conditional=True)


def test_dynamic_import_premise_is_in_warnings(tmp_path):
    """定数でない import_module は解決できない: 前提として warnings にモジュールと行が出る（方針 3）"""
    sp = {"hv/__init__.py": "def compute():\n    return 1\n",
          "lib/__init__.py": "import importlib\ndef load(n):\n    return importlib.import_module('lib.' + n)\n"}
    res = _analyze(tmp_path, sp, "import lib\nprint('ok', lib.load)\n")
    assert any("動的" in w or "定数でない" in w or "dynamic import" in w for w in res["warnings"]), res["warnings"]


def test_pkgutil_scan_reaches_plugins(tmp_path):
    """pkgutil.iter_modules(pkg.__path__) で列挙したプラグインが使う hv は stubbable にならない（N10）"""
    sp = {"hv/__init__.py": "def compute():\n    return 42\n",
          "lib/__init__.py": "import importlib, pkgutil\nfrom lib import plugins\ndef run():\n    out = []\n"
                             "    for m in pkgutil.iter_modules(plugins.__path__):\n"
                             "        mod = importlib.import_module('lib.plugins.' + m.name)\n        out.append(mod.go())\n    return out\n",
          "lib/plugins/__init__.py": "", "lib/plugins/p1.py": "import hv\ndef go():\n    return hv.compute()\n"}
    res = _analyze(tmp_path, sp, "import lib\nprint('plugins', lib.run())\n")
    assert "hv" not in {p["package_name"] for p in res["stubbable"]}
    assert any("定数でない import_module" in w for w in res["warnings"]), res["warnings"]


def test_no_whole_and_submodule_proposal_together(tmp_path):
    """同じパッケージに全体とサブモジュールの両方を提案しない（全体が出るならサブは出さない）"""
    sp = {"cx/__init__.py": "def native():\n    return 1\n", f"cx/_core{EXT}": b"fake",
          "pk/__init__.py": "def f():\n    return 'f'\n", "pk/io/__init__.py": "",
          "pk/io/x.py": "def g():\n    import cx\n    return cx.native()\n"}
    res = _analyze(tmp_path, sp, "import pk, pk.io.x\nprint('ok')\n")
    whole = {p["package_name"] for p in res["stubbable"]} | {p["package_name"] for p in res["conditional"]}
    for h in res["submodule_stub_hints"] + res["conditional_submodule_hints"]:
        assert not any(h["submodule"] == w or h["submodule"].startswith(w + ".") for w in whole), (h, whole)


def test_conditional_hints_have_conditions(tmp_path):
    res = _analyze(tmp_path, SIG4, MAIN4)
    for h in res["conditional_submodule_hints"]:
        assert h["conditions"], h


def test_conditional_stub_stops_on_any_operation(tmp_path):
    """条件付きスタブの代わりのオブジェクトは、呼び出し以外の操作でも止まる（反復で無限ループしない）"""
    c = Case(tmp_path, SIG4, MAIN4, "import gx\nprint('r', gx.draw(1))\n")
    assert "gx" in {p["package_name"] for p in c.res["conditional"]}, c.res["conditional"]
    site = c.apply(packages=["gx"])
    rc, out = _run(c.proj, site, c.proj / "main.py")
    rc0, out0 = _run(c.proj, c.sp, c.proj / "main.py")
    assert (rc, out) == (rc0, out0)
    for expr in ("[x for x in gx.LEVEL]", "gx.LEVEL > 0", "'%s' % (gx.LEVEL,)", "gx.TABLE['a']", "bool(gx.LEVEL)",
                 "str(gx.LEVEL)", "gx.LEVEL + 1", "len(gx.TABLE)", "gx.TABLE.keys()", "f'{gx.LEVEL}'"):
        (c.proj / "op.py").write_text(f"import gx\nprint('r', {expr})\n", encoding="utf-8")
        rc, out = _run(c.proj, site, c.proj / "op.py")
        assert rc == 70 and "PystubConditionalStubError" in out, (expr, rc, out)


def test_conditional_stub_has_all_submodules(tmp_path):
    """条件付きスタブでは元にある全てのサブモジュールを import でき、使った時点で止まる（fontTools.subset 型）"""
    c = Case(tmp_path, SIG4, MAIN4, "from gx.extra import deep\nprint(deep.X)\n")
    site = c.apply(packages=["gx"])
    (c.proj / "imp.py").write_text("import gx.extra.deep\nfrom gx.extra import more\nprint('imported')\n", encoding="utf-8")
    rc, out = _run(c.proj, site, c.proj / "imp.py")
    assert rc == 0 and "imported" in out, out
    rc, out = _run(c.proj, site, c.proj / "violate.py")
    assert rc == 70 and "PystubConditionalStubError" in out, out


def test_size_estimate_ignores_pycache_counts_libs(tmp_path):
    sp = tmp_path / "sp"
    _write(sp, {"pk/__init__.py": "x" * 1000, "pk/__pycache__/a.pyc": b"y" * 5_000_000,
                "pk.libs/dep.dll": b"z" * 2_000_000, f"single{EXT}": b"w" * 1_000_000})
    mb = estimate_package_size("pk", [str(sp)])
    assert 1.9 < mb < 2.1, mb  # __pycache__ の 5 MB は数えず、pk.libs の 2 MB は数える
    assert 0.9 < estimate_package_size("single", [str(sp)]) < 1.1


def test_verify_does_not_blame_unrelated_item_for_silent_output_change(tmp_path):
    """例外を伴わない出力の不一致で、無関係な項目を犯人にしない（犯人は外し、無関係なものは通す）"""
    sp = tmp_path / "sp"
    _write(sp, {"hv1/__init__.py": "X = int('1')\n", "hv2/__init__.py": "class C:\n    pass\n",
                "lib/__init__.py": "import hv1, hv2\ndef f(v):\n    return isinstance(v, hv2.C)\n"})
    _write(tmp_path / "proj", {"main.py": "import lib, hv1\nprint('ok', lib.f(1), type(hv1.X).__name__)\n",
                               "pyproject.toml": ""})
    r = verify(str(tmp_path / "proj" / "main.py"), str(sp), packages=["hv2", "hv1"], submodules=[], nofollow=[],
               run_entry=True)
    assert "hv2" in r["passed"]["packages"], r
    assert "hv1" not in r["passed"]["packages"], r


def test_verify_same_submodule_two_targets_and_entry_note(tmp_path):
    sp = tmp_path / "sp"
    _write(sp, {"cx/__init__.py": "def n():\n    return 1\n", f"cx/_c{EXT}": b"fake",
                "cy/__init__.py": "def n():\n    return 2\n", f"cy/_c{EXT}": b"fake",
                "pk/__init__.py": "def f():\n    return 'f'\n",
                "pk/gui.py": "def g():\n    import cx, cy\n    return cx.n() + cy.n()\n"})
    _write(tmp_path / "proj", {"main.py": "import pk, pk.gui\nprint('ok', pk.f())\n", "pyproject.toml": ""})
    r = verify(str(tmp_path / "proj" / "main.py"), str(sp), packages=[], submodules=["pk.gui=>cx", "pk.gui=>cy"],
               nofollow=[], run_entry=False)
    assert set(r["passed"]["submodules"]) == {"pk.gui=>cx", "pk.gui=>cy"}, r
    assert "run_entry=False" in r["entry_note"]
