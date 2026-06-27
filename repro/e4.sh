#!/usr/bin/env bash
# E4 — Tier-2 regen correction over the deliberately-broken SFC corpus (regen_corpus/).
#
# Default (deterministic, no GPU): the `gate` arm refuses every bad rule at its
# expected layer, and the `stub` arm proves the recovery machinery. Pass `stub,real`
# (or run with the GPU + cuda:1) to add the live Qwen2.5-Coder capability frontier.
#
# Usage: e4.sh [arms]        (default arms: gate,stub ; real arm needs the GPU)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

ARMS="${1:-gate,stub}"
OUT=/tmp/e4_regen.jsonl

"$PY" -m runtime.tools.e4_regen --arms "$ARMS" --out "$OUT"
"$PY" -m runtime.tools.results_to_csv e4 --in "$OUT" --outdir docs/experiments/results
"$PY" -m runtime.tools.plot_results --resultsdir docs/experiments/results
