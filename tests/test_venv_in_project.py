"""venv がプロジェクトのフォルダの中にある配置（myapp/main.py と myapp/.venv/Lib/site-packages）。

- generate / generate_submodule / verify が拒否されず、analyze の結果が venv を外に置いた場合と同じになる
- 守りたかった配置（entry が site-packages の中、python_path がプロジェクトを含む、プロジェクトのモジュール）は拒否のまま
- 出す復元・検証のコマンドは対象環境の python を明示し、素の python / pip を使わない
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mcp_pystub.analyzer import analyze
from mcp_pystub.stub_generator import generate_stubs, generate_submodule_stubs
from mcp_pystub.verifier import verify
from tests.test_conditional import EXT, _write

SP = {
    "cx/__init__.py": "def native():\n    return 1\n", f"cx/_core{EXT}": b"fake",
    "pk/__init__.py": "def f():\n    return 'f'\n", "pk/big.py": "X = 1\n",
    "pk/io/__init__.py": "",
    "pk/io/x.py": "def g():\n    import cx\n    return cx.native()\n",
    "pk-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: pk\nVersion: 1.0\n",
    "pk-1.0.dist-info/RECORD": "pk/__init__.py,,\n",
}
MAIN = "import pk, pk.io.x\nimport helper\nprint('ok', pk.f(), helper.H)\n"


def _venv(venv: Path) -> Path:
    """venv の見た目（pyvenv.cfg・Scripts/python.exe・Lib/site-packages）を作り、site-packages を返す。"""
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = x\n")
    _write(venv, {"Scripts/python.exe": b"", "bin/python": b""})
    sp = venv / "Lib" / "site-packages"
    _write(sp, SP)
    return sp


def _layout(base: Path, venv_inside: bool) -> tuple[str, str]:
    proj = base / "proj"
    _write(proj, {"main.py": MAIN, "helper.py": "H = 1\n", "pyproject.toml": ""})
    sp = _venv(proj / ".venv" if venv_inside else base / "venv")
    return str(proj / "main.py"), str(sp)


def _norm(res: dict, base: Path, sp: str) -> str:
    """実行ごとに変わる値と、置き場所の違う絶対パスを取り除いて比べられる文字列にする。"""
    res = {k: v for k, v in res.items() if k != "analysis_time_ms"}
    s = json.dumps(res, ensure_ascii=False, sort_keys=True, default=str)
    for raw, tag in ((sp, "<SP>"), (str(base / "proj"), "<P>")):
        for form in (raw, raw.replace("\\", "/"), raw.replace("\\", "\\\\"), raw.replace("\\", "\\" * 4)):
            s = s.replace(form, tag)
    return s


class TestVenvInsideProject:
    def test_analyze_same_as_venv_outside(self, tmp_path):
        ein, sin = _layout(tmp_path / "a", True)
        eout, sout = _layout(tmp_path / "b", False)
        rin, rout = analyze(ein, sin), analyze(eout, sout)
        assert "error" not in rin, rin
        assert _norm(rin, tmp_path / "a", sin) == _norm(rout, tmp_path / "b", sout)

    def test_venv_files_are_not_local(self, tmp_path):
        from mcp_pystub.import_graph import build_import_graph
        from mcp_pystub.module_resolver import ModuleResolver
        ein, sin = _layout(tmp_path, True)
        proj = Path(ein).parent
        g = build_import_graph(ein, str(proj), ModuleResolver(str(proj), sin), 10)
        for name, node in g.nodes.items():
            if node.file_path and ".venv" in Path(node.file_path).parts:
                assert node.classification == "third_party", (name, node.classification)
        assert g.nodes["helper"].classification == "local"

    def test_generate_stubs_not_rejected(self, tmp_path):
        ein, sin = _layout(tmp_path, True)
        g = generate_stubs(ein, "pk", python_path=sin)
        assert "error" not in g, g

    def test_generate_submodule_not_rejected(self, tmp_path):
        ein, sin = _layout(tmp_path, True)
        g = generate_submodule_stubs(ein, "pk", "pk.io.x", python_path=sin)
        assert "error" not in g, g

    def test_verify_import_not_rejected(self, tmp_path, monkeypatch):
        import mcp_pystub.verifier as V
        monkeypatch.setattr(V, "_target_python", lambda p: None)  # 空の python.exe ではなく試験の python で動かす
        ein, sin = _layout(tmp_path, True)
        r = verify(ein, sin, packages=["pk"], submodules=[], nofollow=[])
        assert "error" not in r, r
        assert "pk" in r["passed"]["packages"], r

    def test_project_module_still_rejected(self, tmp_path):
        ein, sin = _layout(tmp_path, True)
        g = generate_stubs(ein, "helper", python_path=sin)
        assert "error" in g

    def test_entry_inside_site_packages_still_rejected(self, tmp_path):
        _, sin = _layout(tmp_path, True)
        entry = Path(sin) / "pk" / "big.py"
        g = generate_stubs(str(entry), "pk", python_path=sin)
        assert "error" in g

    def test_non_venv_site_packages_in_project_still_rejected(self, tmp_path):
        """pyvenv.cfg が無い（venv と言えない）site-packages がプロジェクトの下にある場合は今までどおり拒否。"""
        ein, sin = _layout(tmp_path, True)
        (Path(sin).parents[1] / "pyvenv.cfg").unlink()
        assert "error" in generate_stubs(ein, "pk", python_path=sin)

    def test_odd_named_dir_in_venv_still_rejected(self, tmp_path):
        """名前が site-packages / dist-packages でない場所（親フォルダ等）がプロジェクトを含む配置は拒否のまま。"""
        ein, _ = _layout(tmp_path, True)
        assert "error" in generate_stubs(ein, "pk", python_path=str(tmp_path))
        assert "error" in generate_stubs(ein, "pk", python_path=str(Path(ein).parent))


class TestTargetPythonInCommands:
    BARE = re.compile(r"(?:^|&&|;|\||then|do)\s*(?:python3?|pip3?)(?:\.exe)?\s")

    def _cmds(self, bi: dict) -> list[str]:
        out = []
        for k in ("backup_commands", "install_commands", "uninstall_commands", "verify_commands", "hook_disable"):
            out += bi[k]
        return [c for c in out if not c.lstrip().startswith("#")]

    @pytest.mark.parametrize("sub", [False, True])
    def test_commands_name_target_python(self, tmp_path, sub):
        ein, sin = _layout(tmp_path, True)
        g = (generate_submodule_stubs(ein, "pk", "pk.io.x", python_path=sin) if sub
             else generate_stubs(ein, "pk", python_path=sin))
        assert "error" not in g, g
        bi = g["build_instructions"]
        cmds = self._cmds(bi)
        py = (Path(sin).parents[1] / "Scripts" / "python.exe").as_posix()
        for c in cmds:
            assert not self.BARE.search(c), c
        assert any("-m pip install" in c and py in c for c in cmds), cmds
        assert any(" -c " in c and py in c for c in cmds)
        assert all(" -c " not in c or py in c for c in cmds)

    def test_no_target_python_omits_pip_and_explains(self, tmp_path):
        sp = tmp_path / "sp"
        _write(sp, SP)
        _write(tmp_path / "proj", {"main.py": MAIN, "helper.py": "H = 1\n", "pyproject.toml": ""})
        g = generate_stubs(str(tmp_path / "proj" / "main.py"), "pk", python_path=str(sp))
        assert "error" not in g, g
        bi = g["build_instructions"]
        cmds = self._cmds(bi)
        assert not any("pip install" in c for c in cmds)
        assert any("方法 2" in n and "出していません" in n for n in bi["notes"]), bi["notes"]
        for c in cmds:
            assert not self.BARE.search(c), c
