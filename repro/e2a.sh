#!/usr/bin/env bash
# E2a — runtime behavioral gate (off-node).
#
# The 6 fixture scenarios through RuntimeManager.run_episode (no GPU/fabric/KG) — the
# same scenarios as the unit assertions. Expect 6/6 reaching their designed verdict.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

exec "$PY" -m pytest tests/unit/test_evaluator.py tests/unit/test_runtime_manager.py -q
