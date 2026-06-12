#!/bin/bash
set -e

P4_DIR="/home/cc/netprompt-milestone-II/p4_programs"
OUT_DIR="/home/cc/netprompt-milestone-II/compiled_p4"

mkdir -p "$OUT_DIR"

p4c-bm2-ss --p4v 16 -o "$OUT_DIR/low_latency.json" "$P4_DIR/low_latency.p4"
p4c-bm2-ss --p4v 16 -o "$OUT_DIR/reliable_relay.json" "$P4_DIR/reliable_relay.p4"
p4c-bm2-ss --p4v 16 -o "$OUT_DIR/energy_aware.json" "$P4_DIR/energy_aware.p4"
p4c-bm2-ss --p4v 16 -o "$OUT_DIR/bandwidth_optimized.json" "$P4_DIR/bandwidth_optimized.p4"

echo "Compiled all SFC-specific P4 programs."
ls -lh "$OUT_DIR"
