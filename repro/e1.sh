#!/usr/bin/env bash
# E1 — planner confusion matrix (8 probes, promoted adapter, constrained-on).
#
# Runs the 8 probes (sets A/B/C) through llm_orchestrator.orchestrate, writing one
# /tmp/e1_<ID>.json per probe (IDs match results_to_csv's E1_PROBES), then emits
# docs/experiments/results/e1_confusion.csv. Needs the venv, a seeded Neo4j, and the GPU.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

"$PY" -m runtime.tools.seed_kg

# id mission bandwidth delay loss battery
PROBES=(
  "A1 emergency_alert_relay 20 25 2 80"
  "A2 bulk_data_transfer 80 40 1 90"
  "A3 real_time_pest_detection 40 6 1 80"
  "A4 long_term_soil_monitoring 15 50 1 25"
  "B1 real_time_video 40 10 1 80"
  "B2 soil_moisture_survey 15 50 1 25"
  "C1 routine_field_patrol 20 5 1 80"
  "C2 routine_field_patrol 15 50 1 20"
)

cd "$NETPROMPT_ROOT"
for probe in "${PROBES[@]}"; do
  # shellcheck disable=SC2086
  set -- $probe
  id=$1 mission=$2
  "$PY" -m llm_orchestrator.orchestrate --mission "$mission" --bandwidth "$3" --delay "$4" \
    --loss "$5" --battery "$6" --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
    --device-map cuda:0 --no-4bit --output "/tmp/e1_$id.json"
  "$PY" -c "import json;d=json.load(open('/tmp/e1_$id.json'));x=d.get('decision',d);print('$id','$mission',x['selected_sfc'],x['llm_parse_status'])"
done

cd "$REPO"
"$PY" -m runtime.tools.results_to_csv e1 --indir /tmp --outdir docs/experiments/results
