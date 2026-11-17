"""独立検証の攻撃シナリオ（tests/attacks/）を回帰テストとして実行する（第 4 回 受け入れ基準 1）。

- a1〜a15（証明済みの段、126 件）: 全て fine
- b1, b2（証明済みの段の新しい角度）: 破れ 0
- c1, c1b, c2（条件付きの段）: 解析したエントリー（main.py）は出力が同じ。条件を破る別の入口は
  「黙って変わる」ことが無く、必ず LOUD（PystubConditionalStubError で止まる）
- d_overlap（重なり）: 逆順の適用でも元ファイルの欠落 0、verify_commands が通る
"""
from __future__ import annotations

import json

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


# 第 7 回 第 2 段: 1 項目ずつ適用して解析したエントリーを壊す項目は、「確認済み（実行）」（記録で未実行 + run_entry=True の
# 差し替え実行を通過）にならない。件数を固定する
TCHECK_COUNTS = {"n1.py": 18, "f1.py": 14, "e1.py": 6, "e4.py": 3, "f3.py": 1}


@pytest.mark.parametrize("script", sorted(TCHECK_COUNTS))
def test_breaking_items_never_confirmed_run(script, tmp_path, monkeypatch):
    monkeypatch.setenv("PYSTUB_TRACE_CHECK", "1")
    out = _run(script, tmp_path)
    checks = [l.split() for l in out.splitlines() if l.startswith("TCHECK ")]
    assert len(checks) == TCHECK_COUNTS[script], len(checks)
    bad = [c for c in checks if c[3] != "SAME" and c[4].startswith("rec=confirmed_run")]
    assert not bad, bad


# 第 8 回: 動的な確認の穴（_check7 の F3 / N の写し）。どれも確認済み（実行）にならず、適用して壊れる（F3）組が 0 件
F3_CASES = ["L1_stdout_tail_20000", "L1b_short_control", "P1_preimport_masking_tracefiles", "P1b_same_record_mode",
            "S1_subset_of_passed", "N1_dataclass_generated_methods", "N2_exec_defined_function", "N3_namedtuple",
            "N8_runtime_lazy_submodule_import", "N10_multiprocessing_worker"]
F3_EXECUTED = {"N1_dataclass_generated_methods": "xdc", "N2_exec_defined_function": "xex", "N3_namedtuple": "xnt",
               "N8_runtime_lazy_submodule_import": "xlz", "N10_multiprocessing_worker": "xmp"}


def _run_args(script, tmp_path, args, timeout=1800, plain_env=False):
    env = dict(os.environ, PYSTUB_ATTACK_RUNS=str(tmp_path), PYTHONIOENCODING="utf-8")
    if plain_env:
        # verify は呼ぶ側の環境変数をそのまま使う。ハーネスの「普段の実行」と同じ環境にする（PYTHONIOENCODING を付けない）
        for k in ("PYTHONIOENCODING", "PYTHONUTF8"):
            env.pop(k, None)
    r = subprocess.run([sys.executable, "-u", str(HERE / script), *args], cwd=str(HERE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=timeout)
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout


def test_round8_dynamic_holes(tmp_path):
    out = _run_args("f3_attacks.py", tmp_path, F3_CASES)
    line = [l for l in out.splitlines() if l.startswith("SUMMARY ")]
    assert line, out[-2000:]
    rows = json.loads(line[0][len("SUMMARY "):])
    assert [r[0] for r in rows] == F3_CASES
    for name, observed, confirmed, f3 in rows:
        assert confirmed == [], (name, confirmed)
        assert f3 is False, name
        if name in F3_EXECUTED:
            assert observed[F3_EXECUTED[name]] == "executed", (name, observed)


def test_round8_pth_startup(tmp_path):
    """T1: .pth で起動時に読み込まれるパッケージは確認済み（実行）にならない"""
    out = _run_args("t1_pth.py", tmp_path, [])
    res = json.loads(out[out.index("{"):])
    assert res["observed"] == "loaded_at_startup"
    assert res["status"]["確認済み（実行）"] == []
    assert "F3" not in res


def test_round8_import_only_never_runs_entry(tmp_path):
    """F4: エントリーを動かさない呼び出し（既定の verify を含む）で、エントリーのスクリプトが動かない（24 件）"""
    out = _run_args("f4.py", tmp_path, [])
    line = [l for l in out.splitlines() if l.startswith("SUMMARY ")][0]
    res = json.loads(line[len("SUMMARY "):])
    assert len(res) == 24
    bad = {k: v for k, v in res.items() if v["top"] or v["main"]}
    assert not bad, bad


# 第 9 回: verify / 記録の子プロセスが普段の実行と違う条件で動く穴（_check8 の写し）。確認済み（実行）の組を適用して、
# 普段の実行（パイプにつないだ python entry.py）と標準出力・終了コードが変わるものが 0 件。件数を固定する
def _summary_rows(out):
    line = [l for l in out.splitlines() if l.startswith("SUMMARY ")]
    assert line, out[-3000:]
    return json.loads(line[0][len("SUMMARY "):])


def test_round9_g_cases(tmp_path):
    rows = _summary_rows(_run_args("g8.py", tmp_path, [], plain_env=True))
    assert len(rows) == 14, len(rows)
    assert all(r[1] != "ERROR" for r in rows), rows
    bad = [r for r in rows if r[2] is True]
    assert not bad, bad
    # 子プロセスの記録・差し込みが効かない形（G1〜G4）と、普段と違う条件で分かれる形（G5〜G9）は確認済み（実行）にならない
    for r in rows:
        if r[0].split("_")[0] in ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9"):
            assert r[1] == [], r


def test_round9_g_cases_b(tmp_path):
    rows = _summary_rows(_run_args("g8b.py", tmp_path, [], plain_env=True))
    assert [r[0] for r in rows] == ["G4b_child_pythonpath_overridden", "G13_user_pythonpath_dropped", "G14_atexit_stdout"]
    for r in rows:
        assert r[2] == [], r  # 確認済み（実行）なし
        assert r[5] is False, r  # 適用して変わるもの無し


def test_round9_n_cases(tmp_path):
    rows = _summary_rows(_run_args("n8.py", tmp_path, [], plain_env=True))
    assert len(rows) == 5
    got = {r[0]: r for r in rows}
    for r in rows:
        assert r[2] == [] and r[3] is False, r
    assert got["N8b_lazy_import_from_other_pkg"][1]["xlz"] == "executed"
    assert got["N8c_lazy_import_from_local_helper"][1]["xlz"] == "executed"
    assert got["N14_cross_pkg_import_time_call"][1]["xv"] == "executed"


def test_round9_import_only_never_runs_entry_90(tmp_path):
    out = _run_args("f4b.py", tmp_path, [])
    line = [l for l in out.splitlines() if l.startswith("SUMMARY ")][0]
    n, bad = line.split(" ", 2)[1], line.split(" bad ", 1)[1]
    assert n == "90"
    assert json.loads(bad) == {}, bad
