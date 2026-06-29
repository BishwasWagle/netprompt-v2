#!/usr/bin/env bash
# Build #1 — KRONOS Table VIII: control-plane timing breakdown, re-measured on our system.
#
# Loads the promoted planner adapter and times each control-plane stage across the four
# set-A probes, decoding --repeat-decision times per probe (run 0 cold incl. CUDA warmup,
# runs 1.. warm steady-state) so the warm SFC-selection cost is reported apart from warmup.
# Also times the deterministic fallback once for the rule-based ~us contrast. Then aggregates
# the per-probe timing sidecars into docs/experiments/repeat-results/table_viii_{timing,summary}.csv.
#
# Needs the venv, a seeded Neo4j, and the GPU. Usage: repro/table8_timing.sh [repeats]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

REPEATS="${1:-6}"
rm -f /tmp/t8_*_timings.json /tmp/t8_*.json

"$PY" -m runtime.tools.seed_kg

# id/mission bandwidth delay loss battery  (set A — the known taxonomy)
PROBES=(
  "emergency_alert_relay 20 25 2 80"
  "bulk_data_transfer 80 40 1 90"
  "real_time_pest_detection 40 6 1 80"
  "long_term_soil_monitoring 15 50 1 25"
)

cd "$NETPROMPT_ROOT"

# Rule-based contrast (deterministic fallback, no model load) — the "~us" baseline.
"$PY" -m llm_orchestrator.orchestrate --mission emergency_alert_relay \
  --bandwidth 20 --delay 25 --loss 2 --battery 80 \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --fallback-only --skip-artifact-check \
  --output /tmp/t8_rule_fallback.json --save-timings >/dev/null

for probe in "${PROBES[@]}"; do
  # shellcheck disable=SC2086
  set -- $probe
  mission=$1
  echo "[table8] $mission  (repeat-decision=$REPEATS)"
  "$PY" -m llm_orchestrator.orchestrate --mission "$mission" --bandwidth "$2" --delay "$3" \
    --loss "$4" --battery "$5" --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
    --device-map cuda:0 --no-4bit --skip-artifact-check \
    --repeat-decision "$REPEATS" --save-timings \
    --output "/tmp/t8_$mission.json" >/dev/null
done

cd "$REPO"
"$PY" -m runtime.tools.table8_to_csv --indir /tmp --outdir docs/experiments/repeat-results
