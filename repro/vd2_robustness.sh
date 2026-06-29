#!/usr/bin/env bash
# Build #3 — KRONOS §V.D(2): adversarial robustness of the set-A decision.
#
# Loads the planner once, then for each set-A mission perturbs the assembled input 5 ways
# (misleading advisory, stale KG, conflicting telemetry, noisy topology, format-shift) and
# checks the decision stays stable. Writes docs/experiments/repeat-results/vd2_robustness.csv
# (+ summary + figure). Runs from NETPROMPT_ROOT so results_csv resolves; the harness adds
# NETPROMPT_ROOT to sys.path to import llm_orchestrator.
#
# Needs venv · seeded Neo4j · GPU.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

"$PY" -m runtime.tools.seed_kg

cd "$NETPROMPT_ROOT"
"$PY" "$REPO/runtime/tools/e1_robustness.py" --outdir "$REPO/docs/experiments/repeat-results"
