#!/usr/bin/env bash
# E2b — live readiness: M5 + M6 integration on the fabric.
#
# Stands up a resident low_latency BMv2 fabric (3 switches, thrift 9090/9091/9092),
# waits for it to come up, runs the node-gated M5/M6 suites against it, then tears it
# down — all in one process (the doc's two-shell flow, automated). Needs the Mininet/
# BMv2 testbed and passwordless sudo. Expect 6/6 passed.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

teardown() {
  sudo mn -c >/dev/null 2>&1 || true
  sudo pkill -9 -x simple_switch 2>/dev/null || true
  sudo pkill -9 -x iperf 2>/dev/null || true
  for p in $(pgrep -f '[r]untime.tools.launch_network' 2>/dev/null || true); do
    sudo kill -9 "$p" 2>/dev/null || true
  done
}
trap teardown EXIT

sudo -v
"$PY" -m runtime.tools.seed_kg
teardown                                 # clean slate before launching

echo ">> launching low_latency fabric (log: /tmp/e2b_fabric.log) ..."
sudo -E "$SYS_PY" -m runtime.tools.launch_network \
  --p4-json "$NETPROMPT_ROOT/compiled_p4/low_latency.json" \
  --rules-dir "$NETPROMPT_ROOT/p4_multihop_rules" \
  --sfc low_latency --scenario low_latency >/tmp/e2b_fabric.log 2>&1 &

echo ">> waiting for thrift readiness (s1=9090) ..."
ready=0
for _ in $(seq 1 45); do                 # up to ~90 s
  if echo "table_dump forward_table" | simple_switch_CLI --thrift-port 9090 2>/dev/null \
       | grep -q "Dumping entry"; then ready=1; break; fi
  sleep 2
done
[ "$ready" = 1 ] || { echo "fabric not ready; see /tmp/e2b_fabric.log"; exit 1; }

echo ">> running M5/M6 node-gated suites ..."
sudo -E env NETPROMPT_TREE_ROOT="$NETPROMPT_ROOT" "$PY" -m pytest \
  tests/integration/test_m5_monitor_node.py tests/integration/test_m6_acceptance_node.py -v
