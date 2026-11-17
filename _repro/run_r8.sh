#!/bin/bash
# 第 8 回の測定を直列で（空きメモリが 4 GB 未満なら待つ）
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=/c/tmp/pystub-e2e/after_round8
mkdir -p $O/cand
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
wait_mem; echo "tcheck $(date +%T)"; TCHECK_OUT=$O/attacks TCHECK_RUNS=C:/tmp/pystub-e2e/after_round8/tcheck_runs bash _repro/run_tcheck.sh > $O/tcheck.log 2>&1
for s in 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn 15_pillow real_acc; do
  wait_mem; echo "trace $s $(date +%T)"; python -u _repro/trace10.py $O/real $s >> $O/real_trace10.log 2>&1
done
wait_mem; echo "cand01 $(date +%T)"; python _repro/verify_cand.py $O/cand/01_cand_verify.json 01_tqdm numpy,matplotlib,PIL,pandas,fontTools > $O/cand/01.log 2>&1
wait_mem; echo "cand03 $(date +%T)"; python _repro/verify_cand.py $O/cand/03_cand_verify.json 03_networkx scipy,numpy,matplotlib,PIL,fontTools,pandas > $O/cand/03.log 2>&1
wait_mem; echo "cand03_scipy $(date +%T)"; python _repro/verify_cand.py $O/cand/03_scipy_verify.json 03_networkx scipy > $O/cand/03s.log 2>&1
wait_mem; echo "build12 $(date +%T)"; E2E_OUT=after_round8/build_only PYSTUB_ONLY=asammdf.gui python -u _repro/driver4.py 12_asammdf > $O/driver_12_only.log 2>&1
echo ALLDONE
