#!/usr/bin/env bash
# Build #5 — KRONOS Table VII: KG ablation (full vs NoKG), re-measured on the live fabric.
#
# Focused E3 over the discriminating scenarios with 3 arms:
#   rule     = the draft's NoKG ~= static scenario mappings, NO adaptation
#   nokg     = full proposed pipeline, but with the KG-derived REROUTE capability removed
#              (single-factor: the KG's topology reasoning that enables rerouting)
#   proposed = full KRONOS (LLM SFC + adaptive runtime, KG reroute available)
# backup_fault is the discriminating scenario (only full KRONOS can reroute); healthy = control;
# ddil = infeasible (honest escalate for all).
#
# Needs the Mininet/BMv2 testbed, passwordless sudo, GPU, and a seeded Neo4j.
# Usage: repro/table7_nokg.sh [repeats]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

REPEATS="${1:-1}"
OUT="/tmp/e3_nokg.jsonl"

sudo -n true 2>/dev/null || { echo "passwordless sudo required (sudo -n failed)"; exit 1; }
"$PY" -m runtime.tools.seed_kg

"$PY" -m runtime.tools.e3_compare --arms rule,proposed,nokg \
  --scenarios healthy,backup_fault,ddil --repeats "$REPEATS" \
  --out "$OUT" --csv-dir /tmp/e3_nokg_csv
"$PY" -m runtime.tools.table7_to_csv --in "$OUT" --outdir docs/experiments/repeat-results
