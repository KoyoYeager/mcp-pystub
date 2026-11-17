#!/bin/bash
# 第 3 回の残りの測定を 1 本ずつ直列で回す
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
L=_repro/runs/r3_chain.log
: > $L
for s in 01_tqdm 02_openpyxl 03_networkx 08_matplotlib 09_pydantic 12_asammdf 13_seaborn 15_pillow; do
  for m in i ii iii; do
    timeout 1800 python -u _repro/e2e_verify.py $s $m >> $L 2>&1 || echo "VERIFY_FAIL $s $m rc=$?" >> $L
  done
done
echo "=== times" >> $L
python -u _repro/e2e_times.py >> $L 2>&1
echo "=== mem" >> $L
for s in 07_pandas 11_sklearn 12_asammdf; do
  for w in new old; do
    python -u _repro/mem_measure.py $w C:/tmp/pystub-e2e/$s/proj/entry.py C:/tmp/pystub-e2e/$s/venv/Lib/site-packages >> $L 2>&1
  done
done
for w in new old; do python -u _repro/mem_measure.py $w "$(pwd)/_check/real/s5/main.py" >> $L 2>&1; done
echo "=== real_cmp" >> $L
(cd _check && python -u real_cmp.py new s5) >> $L 2>&1
echo CHAIN_DONE >> $L
