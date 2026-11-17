#!/bin/bash
# 第 10 回の測定を直列で（空きメモリが 4 GB 未満なら待つ）。出力は after_round10 だけ。目印で再開できる
cd "$(dirname "$0")/.."
export UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=/c/tmp/pystub-e2e/after_round10
case "$O" in */after_round10) ;; *) echo "bad out $O"; exit 1;; esac
mkdir -p $O/cand
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
step() { [ -f "$O/.done_$1" ]; }
mark() { touch "$O/.done_$1"; }
step tcheck || { wait_mem; echo "tcheck $(date +%T)"; PYTHONIOENCODING=utf-8 TCHECK_OUT=$O/attacks TCHECK_RUNS=C:/tmp/pystub-e2e/after_round10/tcheck_runs bash _repro/run_tcheck.sh > $O/tcheck.log 2>&1; mark tcheck; }
for s in 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn 15_pillow real_acc; do
  step "real_$s" || { wait_mem; echo "trace $s $(date +%T)"; python -u _repro/trace10.py $O/real $s >> $O/real_trace10.log 2>&1; mark "real_$s"; }
done
step cand03 || { wait_mem; echo "cand03 $(date +%T)"; python _repro/verify_cand.py $O/cand/03_scipy_verify.json 03_networkx scipy,fontTools,pandas > $O/cand/03s.log 2>&1; mark cand03; }
step build12 || { wait_mem; echo "build12 $(date +%T)"; PYTHONIOENCODING=utf-8 E2E_OUT=after_round10/build_only PYSTUB_ONLY=asammdf.gui python -u _repro/driver4.py 12_asammdf > $O/driver_12_only.log 2>&1; mark build12; }
echo ALLDONE
