#!/bin/bash
# 仕上げの回の測定を直列で（空きメモリが 4 GB 未満なら待つ）。出力は after_final だけ。目印で再開できる
cd "$(dirname "$0")/.."
export UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=${FINAL_OUT:-/c/tmp/pystub-e2e/after_final}
case "$O" in */after_final|*/after_final/v2|*/after_final2) ;; *) echo "bad out $O"; exit 1;; esac
mkdir -p $O
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
step() { [ -f "$O/.done_$1" ]; }
mark() { touch "$O/.done_$1"; }
for s in 15_pillow 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn; do
  step "apply_$s" || { wait_mem; echo "apply $s $(date +%T)"; python _repro/apply_run.py "$(cygpath -w $O)\apply" $s >> $O/apply.log 2>&1 || { echo "STOP apply $s"; exit 3; }; mark "apply_$s"; }
done
step tcheck || { wait_mem; echo "tcheck $(date +%T)"; PYTHONIOENCODING=utf-8 TCHECK_OUT=$O/attacks TCHECK_RUNS=$(cygpath -m $O)/tcheck_runs bash _repro/run_tcheck.sh > $O/tcheck.log 2>&1; mark tcheck; }
for s in 01_tqdm 02_openpyxl 03_networkx 04_plotly 07_pandas 08_matplotlib 11_sklearn 12_asammdf 13_seaborn 15_pillow real_acc; do
  step "real_$s" || { wait_mem; echo "trace $s $(date +%T)"; python -u _repro/trace10.py $O/real $s >> $O/real_trace10.log 2>&1; mark "real_$s"; }
done
step cand03 || { wait_mem; echo "cand03 $(date +%T)"; python _repro/verify_cand.py $O/cand03.json 03_networkx scipy,fontTools,pandas > $O/cand03.log 2>&1; mark cand03; }
step build15 || { wait_mem; echo "build15 $(date +%T)"; PYTHONIOENCODING=utf-8 E2E_OUT=${E2E_SUB:-after_final}/build15 python -u _repro/driver4.py 15_pillow > $O/driver_15.log 2>&1; mark build15; }
step build01 || { wait_mem; echo "build01 $(date +%T)"; PYTHONIOENCODING=utf-8 E2E_OUT=${E2E_SUB:-after_final}/build01 PYSTUB_EXTRA_PKGS=PIL python -u _repro/driver4.py 01_tqdm > $O/driver_01.log 2>&1; mark build01; }
step build12 || { wait_mem; echo "build12 $(date +%T)"; PYTHONIOENCODING=utf-8 E2E_OUT=${E2E_SUB:-after_final}/build12 python -u _repro/driver4.py 12_asammdf > $O/driver_12.log 2>&1; mark build12; }
step designed || { wait_mem; echo "designed $(date +%T)"; DESIGNED_OUT="$(cygpath -w $O)\designed" python _repro/designed_run.py --build > $O/designed_build.log 2>&1; mark designed; }
echo ALLDONE
