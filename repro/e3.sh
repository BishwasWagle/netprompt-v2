#!/usr/bin/env bash
# E3 — whole system, 3 arms x 4 scenarios (live, topology-equivalent).
#
# Runs the comparative campaign (each cell self-launches + tears down the fabric),
# then writes the tidy CSVs and renders the figures into docs/experiments/results/. Needs
# the Mininet/BMv2 testbed, passwordless sudo, and the GPU (promoted adapter on cuda:0
# for the proposed arm) + a freshly seeded Neo4j on localhost:7687.
#
# Usage: e3.sh [repeats] [out.jsonl]      (defaults: 3 repeats -> /tmp/e3_full.jsonl)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

REPEATS="${1:-3}"
OUT="${2:-/tmp/e3_full.jsonl}"

sudo -v                                  # cache sudo creds up front (the driver shells out to sudo)
"$PY" -m runtime.tools.seed_kg

"$PY" -m runtime.tools.e3_compare --repeats "$REPEATS" --out "$OUT"
"$PY" -m runtime.tools.results_to_csv e3 --in "$OUT" --outdir docs/experiments/results
"$PY" -m runtime.tools.plot_results --resultsdir docs/experiments/results
