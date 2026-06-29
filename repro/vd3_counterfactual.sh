#!/usr/bin/env bash
# Build #4 — KRONOS §V.D(3): counterfactual sensitivity (planner vs oracle).
#
# Varies one signal at a time (mission name; then delay/loss/battery with the name fixed to
# bulk_data_transfer, which has no oracle name-trigger) and contrasts the planner's SFC against
# the deterministic oracle on the same input. Writes
# docs/experiments/repeat-results/vd3_counterfactual.csv (+ summary + figure).
#
# Reframe: NOT a generalization claim. The planner should track the mission name (it does) but is
# expected to stay flat to telemetry counterfactuals while the oracle responds — scored as
# per-episode oracle-agreement.
#
# Needs venv · seeded Neo4j · GPU.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

"$PY" -m runtime.tools.seed_kg

cd "$NETPROMPT_ROOT"
"$PY" "$REPO/runtime/tools/e1_counterfactual.py" --outdir "$REPO/docs/experiments/repeat-results"
