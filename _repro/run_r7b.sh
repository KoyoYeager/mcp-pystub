#!/bin/bash
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=/c/tmp/pystub-e2e/after_round7
[ -d $O/12_asammdf ] && mv $O/12_asammdf $O/12_asammdf_failed_assert
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
wait_mem; echo "verdicts $(date +%T)"; python -u _repro/verdicts.py $O/verdicts15b.json > $O/verdicts15b.log 2>&1
for s in 11_sklearn 12_asammdf; do wait_mem; echo "verify $s $(date +%T)"; python -u _repro/verify10.py $O/verify10 $s >> $O/verify10b.log 2>&1; done
wait_mem; echo "build12 $(date +%T)"; E2E_OUT=after_round7 python -u _repro/driver4.py 12_asammdf > $O/driver_12b.log 2>&1
echo ALLDONE
