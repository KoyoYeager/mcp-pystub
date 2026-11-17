#!/bin/bash
# 第 7 回 第 2 段 受け入れ 5: 確認済み（実行）だけを適用した 12 と、記録からの候補（PIL）を足した 01 の実ビルド
cd "$(dirname "$0")/.."
export PYTHONIOENCODING=utf-8 UV_LINK_MODE=copy UV_CACHE_DIR=C:/tmp/pystub-e2e/uvcache
O=/c/tmp/pystub-e2e/after_trace
wait_mem() { while [ "$(powershell -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" | tr -d '\r')" -lt 4096 ]; do sleep 30; done; }
wait_mem; echo "build12 $(date +%T)"; E2E_OUT=after_trace/build_only PYSTUB_ONLY=asammdf.gui python -u _repro/driver4.py 12_asammdf > $O/driver_12_only.log 2>&1
wait_mem; echo "build01 $(date +%T)"; E2E_OUT=after_trace/build_extra PYSTUB_EXTRA_PKGS=PIL python -u _repro/driver4.py 01_tqdm > $O/driver_01_extra.log 2>&1
echo ALLDONE
