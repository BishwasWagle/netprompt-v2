#!/usr/bin/env bash
# Build #2 — KRONOS Table IV: KG-driven decision provenance + KG query latency, re-measured.
#
# Runs the four set-A probes (the known taxonomy the promoted adapter gets right) through the
# real LLM planner, capturing decision provenance (selected SFC / path / policy / relay) in the
# --output config and per-query KG latency (sfc_query_ms / path_query_ms, warm via
# --repeat-context) in the --save-timings sidecar. Aggregates to
# docs/experiments/repeat-results/table_iv_decisions.csv + a KG-latency figure.
#
# Guardrail: this is set-A provenance only. The draft's condition rows (congestion/relay/DDIL ->
# ReliableRelay) are the telemetry case the planner fails (0/4) and are NOT run as LLM "KG
# reasoning" here — attribute those to the deterministic rule oracle in the writeup.
#
# Needs venv · seeded Neo4j · GPU. Usage: repro/table4_provenance.sh [repeat_context]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

CTX="${1:-5}"   # warm the KG cache: per-query latency reported from the last (warm) read
rm -f /tmp/t4_*.json

"$PY" -m runtime.tools.seed_kg

# mission bandwidth delay loss battery  (set A — known taxonomy)
PROBES=(
  "emergency_alert_relay 20 25 2 80"
  "bulk_data_transfer 80 40 1 90"
  "real_time_pest_detection 40 6 1 80"
  "long_term_soil_monitoring 15 50 1 25"
)

cd "$NETPROMPT_ROOT"
for probe in "${PROBES[@]}"; do
  # shellcheck disable=SC2086
  set -- $probe
  mission=$1
  echo "[table4] $mission  (repeat-context=$CTX)"
  "$PY" -m llm_orchestrator.orchestrate --mission "$mission" --bandwidth "$2" --delay "$3" \
    --loss "$4" --battery "$5" --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
    --device-map cuda:0 --no-4bit --skip-artifact-check \
    --repeat-context "$CTX" --save-timings \
    --output "/tmp/t4_$mission.json" >/dev/null
done

cd "$REPO"
"$PY" -m runtime.tools.table4_to_csv --indir /tmp --outdir docs/experiments/repeat-results
