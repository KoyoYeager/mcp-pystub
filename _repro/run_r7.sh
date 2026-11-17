#!/bin/bash
# 第 7 回 第 1 段の測定を直列で（空きメモリが 4 GB 未満なら待つ）
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=/c/tmp/pystub-e2e/after_round7
mkdir -p $O
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
wait_mem; echo "verdicts $(date +%T)"; python -u _repro/verdicts.py $O/verdicts15.json > $O/verdicts15.log 2>&1
for s in 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn 15_pillow; do
  wait_mem; echo "verify $s $(date +%T)"; python -u _repro/verify10.py $O/verify10 $s >> $O/verify10.log 2>&1
done
wait_mem; echo "designed $(date +%T)"; python _repro/designed_run.py > $O/designed.log 2>&1
wait_mem; echo "build12 $(date +%T)"; E2E_OUT=after_round7 python -u _repro/driver4.py 12_asammdf > $O/driver_12.log 2>&1
echo ALLDONE
