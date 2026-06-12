#!/bin/bash

set -e

BASE="/home/cc/netprompt-milestone-II"

SCENARIOS=(
  "low_latency"
  "baseline"
  "congestion"
  "battery_depletion"
  "relay_failure"
  "ddil"
)

for SCENARIO in "${SCENARIOS[@]}"; do
    echo "=========================================="
    echo "Scenario: $SCENARIO"
    echo "=========================================="

    echo "[1/3] Static baseline"
    sudo mn -c
    sudo pkill -9 simple_switch 2>/dev/null || true
    sudo python3 "$BASE/baselines/baseline_static_experiment.py" --scenario "$SCENARIO"

    echo "[2/3] Rule-based baseline"
    sudo mn -c
    sudo pkill -9 simple_switch 2>/dev/null || true
    sudo python3 "$BASE/baselines/baseline_rule_based_experiment.py" --scenario "$SCENARIO"

    echo "[3/3] Proposed NetPrompt"
    sudo mn -c
    sudo pkill -9 simple_switch 2>/dev/null || true
    "$BASE/experiments/timed_netprompt_runner.sh" "$SCENARIO"
done

echo "[DONE] All comparative experiments finished."
