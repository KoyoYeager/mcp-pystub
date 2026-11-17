"""条件付き候補の confidence・要約・握りつぶし・重なった適用（仕様 7・9 節）"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from mcp_pystub.analyzer import analyze
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
from tests.test_conditional import EXT, Case, _run, _write

SIG = {
    "gx/__init__.py": "def draw(v):\n    return 'drawn %s' % v\n",
    "sig/__init__.py": "class Signal:\n    def __init__(self, v):\n        self.v = v\n    def value(self):\n        return self.v\n    def plot(self):\n        import gx\n        return gx.draw(self.v)\n",
    "other/__init__.py": "def show(obj):\n    return obj.plot()\n",
}
OK_MAIN = "import sig, other\nclass P:\n    def plot(self):\n        return 'p'\nprint('ok', sig.Signal(3).value(), other.show(P()))\n"


def test_confidence_high_when_project_does_not_use_entry(tmp_path):
    c = Case(tmp_path, SIG, OK_MAIN.replace("def plot", "def draw_it").replace("other.show(P())", "1"),
             "import sig\nprint(sig.Signal(1).plot())\n")
    # 名前の振り分けを起こすのはライブラリ内（other.show）だけにし、プロジェクトは other.show を呼ばない
    # （第 5 回: 入口へ入る呼び出しのある関数をプロジェクトが直接呼ぶなら low）
    # 名前の振り分け（o.plot()）は、ライブラリの import 時のコードだけが行う。プロジェクトは、入口に届く公開関数を
    # 1 つも呼ばない（import するだけ）→ high（第 6 回: high は「入口に届く公開関数を呼んでいない」事実の表示）
    _write(c.sp, {"lib2/__init__.py": "def _d(objs):\n    return [o.plot() for o in objs]\nWARM = _d([])\n"})
    _write(c.proj, {"main.py": "import sig, lib2\nprint('ok', sig.Signal(3).value(), lib2.WARM)\n"})
    res = analyze(str(c.proj / "main.py"), python_path=str(c.sp), include_small_conditional=True)
    cond = {p["package_name"]: p for p in res["conditional"]}
    assert "gx" in cond, res["conditional"]  # 候補が出ないなら失敗（黙って通さない）
    g = cond["gx"]
    assert g["confidence"] == "high", g
    for x in g["conditions"]:
        assert x["project_uses_count"] == 0 and "project_note" in x


def test_confidence_low_when_project_uses_entry_name(tmp_path):
    """プロジェクト自身に受け手の分からない同名の `o.plot()` がある → 解決できない呼び出しなので列挙は完了せず low"""
    main = ("import sig, other\nclass P:\n    def plot(self):\n        return 'p'\n"
            "def call(o):\n    return o.plot()\nprint('ok', sig.Signal(3).value(), call(P()))\n")
    c = Case(tmp_path, SIG, main, "import sig\nprint(sig.Signal(1).plot())\n")
    g = [p for p in c.res["conditional"] if p["package_name"] == "gx"][0]
    assert g["confidence"] == "low"
    cond = g["conditions"][0]
    assert cond["project_uses_count"] == 0  # 解決した参照は無い
    assert cond["project_unresolved_calls"] and "main.py" in cond["project_unresolved_calls"][0]


def test_confidence_low_for_branch(tmp_path):
    sp = {"hv/__init__.py": "def compute():\n    return 42\n",
          "lib/__init__.py": "import hv\ndef run(fast):\n    if fast:\n        return hv.compute()\n    return 0\n"}
    c = Case(tmp_path, sp, "import lib\nprint('ok', lib.run(False))\n", "import lib\nprint('ok', lib.run(True))\n")
    g = [p for p in c.res["conditional"] if p["package_name"] == "hv"][0]
    assert g["confidence"] == "low"


def test_summary_is_first_and_lists_high_first(tmp_path):
    sp = dict(SIG)
    sp["lib2/__init__.py"] = "def _d(objs):\n    return [o.plot() for o in objs]\nWARM = _d([])\n"
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": "import sig, lib2\nprint('ok', sig.Signal(3).value(), lib2.WARM)\n", "pyproject.toml": ""})
    res = analyze(str(tmp_path / "proj" / "main.py"), python_path=str(tmp_path / "sp"), include_small_conditional=True)
    assert list(res)[0] == "summary"
    s = res["summary"]
    assert "proven_savings_mb" in s and "conditional_high_savings_mb" in s and s["next_steps"]
    cond = {p["package_name"]: p for p in res["conditional"]}
    assert cond["gx"]["confidence"] == "high"
    assert cond["gx"]["conditions"][0]["project_uses_count"] == 0
    assert "直接使っていません" in cond["gx"]["conditions"][0]["project_note"]
    assert s["recommendations"][0].startswith("[high] gx")


def test_swallowed_stub_error_is_still_reported_on_stderr(tmp_path):
    """呼び出し元が素の except で握りつぶしても、標準エラーに「mcp-pystub のスタブです」が出る（asammdf Signal.plot 型）"""
    sp = dict(SIG)
    sp["sig/__init__.py"] = ("class Signal:\n    def __init__(self, v):\n        self.v = v\n    def value(self):\n        return self.v\n"
                             "    def plot(self):\n        try:\n            import gx\n            return gx.draw(self.v)\n"
                             "        except:\n            return 'fallback'\n")
    c = Case(tmp_path, sp, OK_MAIN, "import sig\nprint(sig.Signal(1).plot())\n")
    g = [p for p in c.res["conditional"] if p["package_name"] == "gx"][0]
    assert g["conditions"][0]["may_be_swallowed"] is True
    site = c.apply(packages=["gx"])
    # 既定: 握りつぶせない（標準エラーに原因を書いて終了コード 70）
    rc, out = _run(c.proj, site, c.proj / "violate.py")
    assert rc == 70 and "PystubConditionalStubError" in out and "mcp-pystub のスタブです" in out
    assert "fallback" not in out
    # MCP_PYSTUB_STRICT=0: 例外を投げる（握りつぶされても標準エラーには出る）
    rc, out = _run(c.proj, site, c.proj / "violate.py", strict="0")
    assert "fallback" in out and "mcp-pystub のスタブです" in out


def test_overlapping_package_and_submodule_stub_restore(tmp_path):
    """パッケージのスタブとその内側のサブモジュールのスタブを重ねて適用・復元しても、元のファイルが失われない。"""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash がありません")
    sp = {
        "cx/__init__.py": "def native():\n    return 1\n", f"cx/_core{EXT}": b"fake",
        "pk/__init__.py": "def f():\n    return 'f'\n",
        "pk/io/__init__.py": "",
        "pk/io/x.py": "def g():\n    import cx\n    return cx.native()\n",
    }
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": "import pk, pk.io.x\nprint('ok')\n", "pyproject.toml": ""})
    entry = str(tmp_path / "proj" / "main.py")
    site = tmp_path / "sp"

    def tree():
        return {str(f.relative_to(site)): f.read_bytes() for f in sorted(site.rglob("*"))
                if f.is_file() and "__pycache__" not in f.parts}

    before = tree()
    gp = generate_stubs(entry, "pk", python_path=str(site))
    gs = generate_submodule_stubs(entry, "pk", "pk.io.x", python_path=str(site))
    assert "error" not in gp and "error" not in gs
    stage = tmp_path / "stage"
    for g in (gp, gs):
        for rel, code in g["files"].items():
            f = stage / "_stubs" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(code, encoding="utf-8")

    def sh(lines):
        r = subprocess.run([bash, "-c", "\n".join(lines)], cwd=stage, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr

    def method1(bi):
        out = []
        for line in bi["uninstall_commands"]:
            if "方法2" in line:
                break
            out.append(line)
        return out

    for g in (gp, gs):  # パッケージ → 内側の順に適用
        sh(g["build_instructions"]["backup_commands"])
        sh(g["build_instructions"]["install_commands"])
    for g in (gs, gp):  # 逆順に復元
        sh(method1(g["build_instructions"]))
    assert tree() == before
    # 内側のサブモジュールだけを適用したときは、スタブが実際に置かれる（自分のバックアップで
    # 親ディレクトリができたことを「親パッケージのスタブ」と取り違えて skip しない）
    sh(gs["build_instructions"]["backup_commands"])
    sh(gs["build_instructions"]["install_commands"])
    stubbed = tree()
    xk = str(Path("pk/io/x.py"))
    assert xk in stubbed, "スタブが置かれていない（元ファイルだけ退避された）"
    assert stubbed[xk] != before[xk]
    sh(method1(gs["build_instructions"]))
    assert tree() == before
    assert not (site.parent / "_pystub_bak").exists()


def test_low_when_project_calls_function_that_dispatches(tmp_path):
    """プロジェクトが、入口へ入る呼び出しのある関数（other.show）を直接呼ぶ → low（理由つき）"""
    c = Case(tmp_path, SIG, OK_MAIN, "import sig, other\nprint(other.show(sig.Signal(1)))\n")
    g = [p for p in c.res["conditional"] if p["package_name"] == "gx"][0]
    assert g["confidence"] == "low"
    assert "other.show()" in g["confidence_reason"]


def test_low_when_project_calls_public_function_transitively(tmp_path):
    """プロジェクトが、入口に届く公開関数（lib3.top → lib2.run → _dispatch の o.plot()）を呼ぶ → low（推移的: 第 6 回 K01）。
    空のリストを渡して実際には止まらない事例でも、「呼んでいる」事実で low にする"""
    sp = dict(SIG)
    sp["lib2/__init__.py"] = "def _dispatch(objs):\n    return [o.plot() for o in objs]\ndef run(objs):\n    return _dispatch(objs)\n"
    sp["lib3/__init__.py"] = "import lib2\ndef top(objs):\n    return lib2.run(objs)\n"
    _write(tmp_path / "sp", sp)
    _write(tmp_path / "proj", {"main.py": "import sig, lib3\nprint('ok', sig.Signal(3).value(), lib3.top([]))\n", "pyproject.toml": ""})
    res = analyze(str(tmp_path / "proj" / "main.py"), python_path=str(tmp_path / "sp"), include_small_conditional=True)
    g = {p["package_name"]: p for p in res["conditional"]}["gx"]
    assert g["confidence"] == "low"
    assert "lib3.top()" in g["confidence_reason"], g["confidence_reason"]
