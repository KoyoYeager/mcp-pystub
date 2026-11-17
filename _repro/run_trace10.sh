#!/bin/bash
# 第 7 回 第 2 段 受け入れ 3・4 を直列で（空きメモリが 4 GB 未満なら待つ）
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8
O=/c/tmp/pystub-e2e/after_trace/real
mkdir -p $O
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
for s in 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn 15_pillow real_acc; do
  wait_mem; echo "trace $s $(date +%T)"; python -u _repro/trace10.py $O $s >> $O/trace10.log 2>&1
done
echo ALLDONE
