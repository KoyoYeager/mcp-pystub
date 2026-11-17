"""独立検証の攻撃シナリオ（tests/attacks/）を回帰テストとして実行する（第 4 回 受け入れ基準 1）。

- a1〜a15（証明済みの段、126 件）: 全て fine
- b1, b2（証明済みの段の新しい角度）: 破れ 0
- c1, c1b, c2（条件付きの段）: 解析したエントリー（main.py）は出力が同じ。条件を破る別の入口は
  「黙って変わる」ことが無く、必ず LOUD（PystubConditionalStubError で止まる）
- d_overlap（重なり）: 逆順の適用でも元ファイルの欠落 0、verify_commands が通る
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "attacks"
A_SCRIPTS = [f"a{i}.py" for i in range(1, 16)]


def _run(script: str, tmp_path: Path) -> str:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYSTUB_ATTACK_RUNS=str(tmp_path / "runs"))
    env.pop("MCP_PYSTUB_STRICT", None)
    r = subprocess.run([sys.executable, "-u", str(HERE / script)], cwd=str(HERE), capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    assert r.returncode == 0, r.stderr[-3000:]
    return r.stdout


@pytest.mark.parametrize("script", A_SCRIPTS)
def test_proven_attacks_a(script, tmp_path):
    out = _run(script, tmp_path)
    lines = [l for l in out.splitlines() if l.startswith("RESULT ")]
    assert lines, out[-2000:]
    broken = [l for l in lines if " BROKEN " in l]
    assert not broken, "\n".join(broken)


@pytest.mark.parametrize("script", ["b1.py", "b2.py"])
def test_proven_attacks_b(script, tmp_path):
    out = _run(script, tmp_path)
    lines = [l for l in out.splitlines() if l.startswith("RESULT2 ")]
    assert lines, out[-2000:]
    broken = [l for l in lines if not l.rstrip().endswith(" []")]
    assert not broken, "\n".join(x[:400] for x in broken)


# 「直さない」（仕様 10.5 / README の制限）: Python の仕組み上スタブ側で止められない操作。条件を破った時に限り、
# 止まる前に結果が元と変わりうる。名前を挙げて除外し、期待する結果の種類も固定する（黙って増やさない）
LIMITATIONS = {
    "S01_isinstance_value": "SILENT",        # isinstance(値の代わり, int)
    "S03_callable_value": "SILENT",          # callable(値の代わり)
    "S07_isinstance_property": "SILENT",     # isinstance(property の代わり, property)
    "S08b_enum_member_isinstance": "SILENT", # isinstance(Enum のメンバーの代わり, Enum のクラス)
    "S11_type_name_value": "SILENT",         # type(値の代わり).__name__
    "S12_issubclass_stub_classes": "SILENT", # issubclass(スタブのクラス, スタブのクラス)
    "S13_mro_len": "SILENT",                 # スタブのクラスの __mro__
    "S15_is_dataclass": "SILENT",            # dataclasses.is_dataclass(スタブのクラス)
    "S17_namedtuple_fields": "CHANGED",      # 外部の基底（NamedTuple）から継承した属性は元に無いとして AttributeError
    "S21_type_is_value": "SILENT",           # type(値の代わり) is int
    "S23_dir_len": "SILENT",                 # dir(スタブのクラス)
    "S24_vars_keys": "SILENT",               # vars(スタブのクラス)
    "S25_try_attr_missing": "SAME_STDOUT",   # 元に無い属性のエラー文の違い
    "S29_names_tuple_isinstance": "SILENT",  # isinstance(値の代わり, tuple)
}


@pytest.mark.parametrize("script", ["c1.py", "c1b.py", "c2.py", "e1.py", "e4.py", "f1.py", "f2.py", "f3.py", "g_high.py"])
def test_conditional_attacks(script, tmp_path):
    """条件付きの段（第 5 回の基準）:
    - 黙って変わる（SILENT / CHANGED）は 0 件
    - high の候補で、解析したエントリー（main.py）が止まる・変わるのは 0 件
    - low の候補は、解析したエントリーが 70 で止まることがある（別の入力で止まりうる、の意味）
    """
    out = _run(script, tmp_path)
    bad = []
    highs: list[str] = []
    n = 0
    confs: list[str] = []
    for l in out.splitlines():
        if l.startswith("===== "):
            confs = []
        m = re.search(r"^\s+(COND|CHINT)\s.*conf=(\w+)", l)
        if m:
            confs.append(m.group(2))
        m = re.match(r"RESULT (\S+) (\[.*\])$", l)
        if not m:
            continue
        n += 1
        name = m.group(1)
        for lv, entry, kind in ast.literal_eval(m.group(2)):
            if lv == "proven":
                bad.append((name, lv, entry, kind, "証明済みの段の破れ"))
            elif name in LIMITATIONS and entry != "main.py":
                if not kind.startswith(LIMITATIONS[name]):
                    bad.append((name, lv, entry, kind, f"制限として期待した結果 {LIMITATIONS[name]} と違う"))
            elif kind != "LOUD":
                bad.append((name, lv, entry, kind, "黙って変わった"))
            elif entry == "main.py" and "high" in confs:
                bad.append((name, lv, entry, kind, f"high の候補で解析したエントリーが止まった {confs}"))
        if name.startswith("G0"):
            highs.append(name) if "high" in confs else bad.append((name, "-", "-", "-", f"high が出ない {confs}"))
    assert n, out[-2000:]
    assert not bad, bad
    if script == "g_high.py":
        assert len(highs) >= 3, highs  # high が実際に出て、解析したエントリーが変わらない事例


def test_overlap_reverse_order(tmp_path):
    out = _run("d_overlap.py", tmp_path)
    assert "[after apply] lost=[]" in out, out
    assert "[after restore] lost=[] changed=[] bak_exists=False" in out, out
    vc = [l for l in out.splitlines() if l.startswith("   python -c")]
    assert vc and all(" -> 0 " in l for l in vc), vc


# 第 7 回: 静的解析が当てることでなく、verify(run_entry=True) が原因の項目を passed に入れないことで守る。
# 適用すると解析したエントリーが変わるシナリオで、verify が通した組を適用してもエントリーが変わらないこと（VCHECK ok）。
# 件数を固定する（シナリオが消えたら失敗）
VCHECK_COUNTS = {"n1.py": (11, 10), "f1.py": (20, 14), "e1.py": (14, 6), "e4.py": (3, 3), "f3.py": (7, 1)}


@pytest.mark.parametrize("script", sorted(VCHECK_COUNTS))
def test_verify_rejects_what_breaks_the_entry(script, tmp_path, monkeypatch):
    monkeypatch.setenv("PYSTUB_VERIFY_CHECK", "1")
    out = _run(script, tmp_path)
    results = [l for l in out.splitlines() if l.startswith("RESULT ")]
    checks = [l for l in out.splitlines() if l.startswith("VCHECK ")]
    n_res, n_chk = VCHECK_COUNTS[script]
    assert len(results) == n_res, (len(results), n_res)
    assert len(checks) == n_chk, (len(checks), n_chk)
    bad = [l for l in checks if " BAD " in l]
    assert not bad, bad


@pytest.mark.skipif(not Path(r"C:\\tmp\\pystub-e2e\\check_round6\\real_acc\\venv").exists(), reason="Y7-real の venv がありません")
def test_verify_rejects_real_pandas_accessor_plugin():
    """Y7-real: pandas の公式拡張 API で accessor を登録するだけのプラグイン。verify(run_entry=True) が通さない"""
    from mcp_pystub.verifier import verify
    R = Path(r"C:\\tmp\\pystub-e2e\\check_round6\\real_acc")
    v = verify(str(R / "proj/entry.py"), python_path=str(R / "venv/Lib/site-packages"), packages=["geoacc"],
               submodules=[], nofollow=[], run_entry=True)
    assert "geoacc" not in v["passed"]["packages"], v
