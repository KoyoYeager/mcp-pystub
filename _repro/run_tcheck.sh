#!/bin/bash
# 第 7 回 第 2 段 受け入れ 2: 全攻撃シナリオで、壊す項目の確認の状態を出す
cd "$(dirname "$0")/../tests/attacks"
O=/c/tmp/pystub-e2e/after_trace/attacks
mkdir -p $O
export PYSTUB_TRACE_CHECK=1 PYTHONIOENCODING=utf-8 PYSTUB_ATTACK_RUNS=C:/tmp/pystub-e2e/after_trace/attack_runs
for f in a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 a11 a12 a13 a14 a15 b1 b2 c1 c1b c2 d_overlap e1 e4 f1 f2 f3 g_high n1; do
  python -u $f.py > $O/$f.log 2>&1
  echo "$f rc=$? RESULT=$(grep -c '^RESULT' $O/$f.log) TCHECK=$(grep -c '^TCHECK' $O/$f.log)"
done
python -u ../../_check6/real_acc.py > $O/real_acc.log 2>&1; echo "real_acc rc=$?"
echo ALLDONE
