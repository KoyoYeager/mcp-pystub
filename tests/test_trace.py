"""実行の記録（第 7 回 第 2 段）のテスト。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_pystub import trace as tr  # noqa: E402


def _make(tmp_path: Path) -> tuple[Path, Path]:
    sp = tmp_path / "sp"
    for name, body in {
        "usedpkg/__init__.py": "def work(x):\n    return x * 2\n",
        "imponly/__init__.py": "def never():\n    return 1\nclass K:\n    def m(self):\n        return 2\n",
        "selfinit/__init__.py": "def _setup():\n    return 3\nV = _setup()\n",
        "otherinit/__init__.py": "def setup():\n    return 4\n",
        "caller/__init__.py": "import otherinit\notherinit.setup()\n",
        "nativepkg/__init__.py": "X = 1\n",
        "nativepkg/_speed.pyd": "",
        "absent/__init__.py": "def f():\n    return 5\n",
        "toolmod/__init__.py": "",
        "toolmod/__main__.py": "import usedpkg\nprint(usedpkg.work(21))\n",
    }.items():
        f = sp / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    ep = proj / "entry.py"
    ep.write_text("import sys\nimport usedpkg, imponly, selfinit, caller, nativepkg\n"
                  "if len(sys.argv) > 1 and sys.argv[1] == 'more':\n    import absent\n    absent.f()\n"
                  "print(usedpkg.work(3))\n", encoding="utf-8")
    return sp, ep


def _trace(tmp_path, sp, args, out, append=False):
    env = {"PYTHONPATH": str(sp) + ";" + str(ROOT) if sys.platform == "win32" else str(sp) + ":" + str(ROOT),
           "PYTHONIOENCODING": "utf-8", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    cmd = [sys.executable, "-m", "mcp_pystub.trace", "--out", str(out)] + (["--append"] if append else []) + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(tmp_path))


def test_cli_categories(tmp_path):
    sp, ep = _make(tmp_path)
    out = tmp_path / "t.json"
    r = _trace(tmp_path, sp, [str(ep)], out)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "6"
    t = tr.load(str(out))
    st = {p: tr.observe(t, p, [str(sp)])["state"] for p in
          ("usedpkg", "imponly", "selfinit", "otherinit", "caller", "nativepkg", "absent")}
    assert st == {"usedpkg": "executed", "imponly": "import_only", "selfinit": "import_only",
                  "otherinit": "executed", "caller": "import_only", "nativepkg": "unknown_native",
                  "absent": "not_imported"}, st
    o = tr.observe(t, "usedpkg", [str(sp)])
    assert o["functions_executed"] == 1 and o["top_functions"] == ["work"]


def test_append_merges_runs(tmp_path):
    sp, ep = _make(tmp_path)
    out = tmp_path / "t.json"
    assert _trace(tmp_path, sp, [str(ep)], out).returncode == 0
    assert _trace(tmp_path, sp, [str(ep), "more"], out, append=True).returncode == 0
    t = tr.load(str(out))
    assert t["runs"] == 2
    assert tr.observe(t, "absent", [str(sp)])["state"] == "executed"


def test_load_merges_files(tmp_path):
    sp, ep = _make(tmp_path)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert _trace(tmp_path, sp, [str(ep)], a).returncode == 0
    assert _trace(tmp_path, sp, [str(ep), "more"], b).returncode == 0
    t = tr.load([str(a), str(b)])
    assert t["runs"] == 2 and tr.observe(t, "absent", [str(sp)])["state"] == "executed"
    assert tr.observe(tr.load(str(a)), "absent", [str(sp)])["state"] == "not_imported"


def test_module_form(tmp_path):
    """-m 形式の起動（pytest などエントリー以外の起動方法）でも記録できる"""
    sp, _ep = _make(tmp_path)
    out = tmp_path / "t.json"
    r = _trace(tmp_path, sp, ["-m", "toolmod"], out)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "42"
    t = tr.load(str(out))
    assert tr.observe(t, "usedpkg", [str(sp)])["state"] == "executed"
    assert tr.observe(t, "imponly", [str(sp)])["state"] == "not_imported"


def test_exit_code_kept(tmp_path):
    sp, _ep = _make(tmp_path)
    ep = tmp_path / "proj" / "boom.py"
    ep.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    out = tmp_path / "t.json"
    r = _trace(tmp_path, sp, [str(ep)], out)
    assert r.returncode == 3
    assert json.loads(out.read_text(encoding="utf-8"))["rc"] == [3]


def test_verify_record_statuses(tmp_path):
    """verify(run_entry=True, record=True): 基準の実行で記録し、各項目に状態を付ける"""
    from mcp_pystub.verifier import verify
    sp, ep = _make(tmp_path)
    v = verify(str(ep), python_path=str(sp), packages=["imponly", "usedpkg"], submodules=[], nofollow=[],
               run_entry=True, record=True)
    assert v["traced"] is True
    s = v["statuses"]
    assert s["imponly"]["status"] == "confirmed_run", s["imponly"]
    assert s["imponly"]["observed"]["state"] == "import_only"
    assert s["usedpkg"]["status"] == "unconfirmed", s["usedpkg"]
    assert s["usedpkg"]["observed"]["state"] == "executed"
    assert v["status_summary"]["確認済み（実行）"] == ["imponly"]


def test_verify_without_record_never_confirmed_run(tmp_path):
    """記録が無い・import だけの verify では「確認済み（実行）」にならない"""
    from mcp_pystub.verifier import verify
    sp, ep = _make(tmp_path)
    v = verify(str(ep), python_path=str(sp), packages=["imponly"], submodules=[], nofollow=[])
    assert v["traced"] is False and v["run_entry"] is False
    assert v["statuses"]["imponly"]["status"] == "confirmed_import"
    v2 = verify(str(ep), python_path=str(sp), packages=["imponly"], submodules=[], nofollow=[], run_entry=True)
    assert v2["statuses"]["imponly"]["status"] == "confirmed_import"


def test_verify_record_needs_run_entry(tmp_path):
    """record=True でも run_entry=False ならエントリーを動かさない（記録も取らない）"""
    from mcp_pystub.verifier import verify
    sp, ep = _make(tmp_path)
    marker = tmp_path / "ran.txt"
    ep.write_text(ep.read_text(encoding="utf-8") + f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    v = verify(str(ep), python_path=str(sp), packages=["imponly"], submodules=[], nofollow=[], record=True)
    assert v["traced"] is False
    assert not marker.exists()


def test_verify_trace_files(tmp_path):
    """利用者が自分で作った記録ファイルを verify に渡せる"""
    from mcp_pystub.verifier import verify
    sp, ep = _make(tmp_path)
    out = tmp_path / "t.json"
    assert _trace(tmp_path, sp, [str(ep)], out).returncode == 0
    v = verify(str(ep), python_path=str(sp), packages=["imponly", "usedpkg"], submodules=[], nofollow=[],
               run_entry=True, trace_files=[str(out)])
    assert v["statuses"]["imponly"]["status"] == "confirmed_run"
    assert v["statuses"]["usedpkg"]["status"] == "unconfirmed"


def test_analyze_trace_conflict_and_candidates(tmp_path):
    from mcp_pystub.analyzer import _apply_trace
    sp, ep = _make(tmp_path)
    out = tmp_path / "t.json"
    assert _trace(tmp_path, sp, [str(ep)], out).returncode == 0

    class A:
        def __init__(self, n):
            self.package_name, self.estimated_size_mb, self.reason = n, 1.0, "使用あり"
    rd = {"stubbable": [{"package_name": "usedpkg", "estimated_size_mb": 1.0}], "nofollow": [], "conditional": [],
          "submodule_stub_hints": [], "conditional_submodule_hints": []}
    _apply_trace(rd, [str(out)], [str(sp)], [A("imponly"), A("caller"), A("otherinit")], str(ep), "")
    assert rd["stubbable"][0]["trace_conflict"] is True
    assert [c["item"] for c in rd["trace_conflicts"]] == ["usedpkg"]
    assert sorted(c["package_name"] for c in rd["trace_candidates"]) == ["caller", "imponly"]
    from mcp_pystub.analyzer import _summary
    s = _summary(rd, str(ep), "", [])
    assert not any("usedpkg" in r for r in s["recommendations"])
    assert s["trace_conflicts"] and len(s["trace_candidates"]) == 2


def test_analyze_does_not_run_entry(tmp_path):
    """analyze はエントリーを実行しない（記録ファイルを渡しても読むだけ）"""
    from mcp_pystub.analyzer import analyze
    sp, ep = _make(tmp_path)
    out = tmp_path / "t.json"
    assert _trace(tmp_path, sp, [str(ep)], out).returncode == 0
    marker = tmp_path / "ran.txt"
    ep.write_text(ep.read_text(encoding="utf-8") + f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    r = analyze(str(ep), python_path=str(sp), trace_files=[str(out)])
    assert not marker.exists()
    assert "trace" in r and "trace_candidates" in r


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring は 3.12 以降")
def test_monitoring_tool_released():
    rec = tr.start("")
    tr.stop(rec)
    rec2 = tr.start("")  # 同じ tool id がまた使える
    tr.stop(rec2)


def test_setprofile_path(monkeypatch):
    """3.12 より前の経路（sys.setprofile）"""
    monkeypatch.setattr(tr.sys, "version_info", (3, 11, 0))

    def f():
        return 1
    rec = tr.start("")
    f()
    d = tr.stop(rec)
    assert any(x[1].endswith("f") for x in d["executed"])


def test_generate_required_is_loud(tmp_path):
    """静的解析で required のパッケージ（記録からの候補）のスタブは、使われたら止まるスタブになる"""
    from mcp_pystub.stub_generator import generate_stubs
    sp, ep = _make(tmp_path)
    g = generate_stubs(str(ep), "otherinit", python_path=str(sp))
    assert g["conditional"] is True
    assert any("PystubConditionalStubError" in c for c in g["files"].values())


def test_verify_blames_loud_stub_that_exits(tmp_path):
    """止まるスタブが import の途中で子プロセスを終わらせても、原因の項目を特定して落とす（他の項目は通す）"""
    from mcp_pystub.verifier import verify
    sp, ep = _make(tmp_path)
    v = verify(str(ep), python_path=str(sp), packages=["otherinit", "imponly"], submodules=[], nofollow=[],
               run_entry=True, record=True)
    assert [r["item"] for r in v["rejected"]] == ["otherinit"], v["rejected"]
    assert "終了コード 70" in (v["rejected"][0]["error"] or "")
    assert v["status_summary"]["確認済み（実行）"] == ["imponly"]
