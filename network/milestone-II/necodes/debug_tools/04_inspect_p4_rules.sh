#!/bin/bash

BASE="/home/cc/netprompt-milestone-II"
OUT="$BASE/debug_results/04_p4_rule_inspection.log"

rm -f "$OUT"

echo "=== P4 multihop rules inspection ===" | tee -a "$OUT"

for f in "$BASE"/p4_multihop_rules/*.txt; do
    echo "" | tee -a "$OUT"
    echo "########################################################" | tee -a "$OUT"
    echo "FILE: $f" | tee -a "$OUT"
    echo "########################################################" | tee -a "$OUT"

    echo "--- table_add lines ---" | tee -a "$OUT"
    grep -n "table_add" "$f" | tee -a "$OUT" || true

    echo "--- forward entries ---" | tee -a "$OUT"
    grep -n "forward" "$f" | tee -a "$OUT" || true

    echo "--- drop entries ---" | tee -a "$OUT"
    grep -n "drop" "$f" | tee -a "$OUT" || true

    echo "--- protocol-specific fields? ---" | tee -a "$OUT"
    grep -n "tcp\|udp\|icmp\|protocol" "$f" | tee -a "$OUT" || true
done

echo ""
echo "[OK] Saved $OUT"
