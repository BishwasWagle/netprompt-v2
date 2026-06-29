#!/usr/bin/env bash
# Build #6 — KRONOS Fig. 6: SFC decision-accuracy confusion matrix, re-measured.
#
# Runs the held-out probe set (repro/fig6_probes.json: set-A known names + set-B/C novel/generic
# variants, true class labelled) through the planner and builds the 4x4 row-normalized confusion
# matrix. Writes docs/experiments/repeat-results/fig6_{probes,confusion,summary}.csv + figure.
#
# Reframe: the defensible reading is known-taxonomy (set-A) accuracy, NOT generalization; the
# set-A sub-matrix is reported separately from the full matrix.
#
# Needs venv · seeded Neo4j · GPU.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

"$PY" -m runtime.tools.seed_kg

cd "$NETPROMPT_ROOT"
"$PY" "$REPO/runtime/tools/fig6_confusion.py" \
  --probes "$REPO/repro/fig6_probes.json" \
  --outdir "$REPO/docs/experiments/repeat-results"
