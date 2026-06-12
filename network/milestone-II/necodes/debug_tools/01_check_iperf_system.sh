#!/bin/bash

set -e

BASE="/home/cc/netprompt-milestone-II"
OUT="$BASE/debug_results/01_check_iperf_system.log"

mkdir -p "$BASE/debug_results"
rm -f "$OUT"

echo "=== iperf binary ===" | tee -a "$OUT"
which iperf | tee -a "$OUT" || true
iperf -v 2>&1 | tee -a "$OUT" || true

echo "" | tee -a "$OUT"
echo "=== existing iperf processes ===" | tee -a "$OUT"
ps aux | grep '[i]perf' | tee -a "$OUT" || true

echo "" | tee -a "$OUT"
echo "=== host port 5001 listeners ===" | tee -a "$OUT"
ss -ltnp | grep 5001 | tee -a "$OUT" || true

echo "" | tee -a "$OUT"
echo "=== previous result file iperf errors ===" | tee -a "$OUT"
grep -R "tcp connect failed\|Connection timed out\|Mbits/sec\|Kbits/sec\|Gbits/sec" "$BASE/results" | tee -a "$OUT" || true

echo ""
echo "[OK] Saved $OUT"
