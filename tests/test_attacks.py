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


@pytest.mark.parametrize("script", ["c1.py", "c1b.py", "c2.py"])
def test_conditional_attacks(script, tmp_path):
    out = _run(script, tmp_path)
    bad = []
    n = 0
    for l in out.splitlines():
        m = re.match(r"RESULT (\S+) (\[.*\])$", l)
        if not m:
            continue
        n += 1
        for lv, entry, kind in ast.literal_eval(m.group(2)):
            # 解析したエントリーは同じ出力でなければならない。別の入口は止まる（LOUD）なら可
            if entry == "main.py" or kind != "LOUD":
                bad.append((m.group(1), lv, entry, kind))
    assert n, out[-2000:]
    assert not bad, bad


def test_overlap_reverse_order(tmp_path):
    out = _run("d_overlap.py", tmp_path)
    assert "[after apply] lost=[]" in out, out
    assert "[after restore] lost=[] changed=[] bak_exists=False" in out, out
    vc = [l for l in out.splitlines() if l.startswith("   python -c")]
    assert vc and all(" -> 0 " in l for l in vc), vc
