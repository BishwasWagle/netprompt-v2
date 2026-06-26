#!/usr/bin/env bash
# E2a — runtime behavioral gate (off-node).
#
# The 6 fixture scenarios through RuntimeManager.run_episode (no GPU/fabric/KG) — the
# same scenarios as the unit assertions. Expect 6/6 reaching their designed verdict.
# Runs the pytest gate (the authoritative assertions), then records the per-scenario
# verdicts to docs/experiments/results/e2a_gate.csv.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

"$PY" -m pytest tests/unit/test_evaluator.py tests/unit/test_runtime_manager.py -q
"$PY" -m runtime.tools.results_to_csv e2a --outdir docs/experiments/results
