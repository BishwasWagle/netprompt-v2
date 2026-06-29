#!/usr/bin/env bash
# M7 — cross-family Tier-2 regen comparison (the multi-LLM table/figure).
#
# Runs `regen_compare` over 9 run models (4 architecture families: Qwen2, Llama, Phi3,
# Granite) + 2 deliberate failure probes (StableLm unsupported-tokenizer, Starcoder2
# no-chat-template), each PINNED to a fixed commit SHA via the harness's 'org/model@<sha>'
# syntax, then exports m7_xfam.csv and the two figures.
#
# Needs the GPU (cuda:1) + network (first run downloads ~25 GB of weights). FP16, greedy,
# GBNF-constrained, max_new_tokens=64. Per-model @sha pins do NOT inherit the env's global
# NETPROMPT_REGEN_REVISION (which would apply one repo's commit to every model).
#
# Usage: repro/m7_xfam.sh [device]      (default device: cuda:1)
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

DEVICE="${1:-cuda:1}"
OUT=/tmp/m7_xfam.json
export HF_HUB_DISABLE_PROGRESS_BARS=1 TOKENIZERS_PARALLELISM=false

# Pinned run models (full weights) — resolved 2026-06-29.
RUN=(
  "Qwen/Qwen2.5-Coder-0.5B-Instruct@ea3f2471cf1b1f0db85067f1ef93848e38e88c25"
  "Qwen/Qwen2.5-Coder-1.5B-Instruct@2e1fd397ee46e1388853d2af2c993145b0f1098a"
  "Qwen/Qwen2.5-Coder-3B-Instruct@488639f1ff808d1d3d0ba301aef8c11461451ec5"
  "Qwen/Qwen2.5-1.5B-Instruct@989aa7980e4cf806f80c7fef2b1adb7bc71aa306"
  "TinyLlama/TinyLlama-1.1B-Chat-v1.0@fe8a4ea1ffedaf415f4da2f062534de366a451e6"
  "deepseek-ai/deepseek-coder-1.3b-instruct@e063262dac8366fc1f28a4da0ff3c50ea66259ca"
  "HuggingFaceTB/SmolLM2-1.7B-Instruct@31b70e2e869a7173562077fd711b654946d38674"
  "ibm-granite/granite-3.0-2b-instruct@5ad66c190631382717bd92d7b052adb1a7b669e7"
  "microsoft/Phi-3-mini-4k-instruct@f39ac1d28e925b323eae81227eaba4464caced4e"
)
PROBE=(
  "stabilityai/stable-code-instruct-3b@20e21f0e817b72499c8585d86a139c0fd011adba"
  "bigcode/starcoder2-3b@733247c55e3f73af49ce8e9c7949bf14af205928"
)

# Clear any stale HF negative cache (a transient 429 can poison .no_exist/<sha>/config.json,
# faking an 'Unrecognized model' load failure). Then pre-download pinned revisions sequentially
# (snapshot_download retries on 429) so the in-process load reads complete local snapshots.
for spec in "${RUN[@]}" "${PROBE[@]}"; do
  repo="${spec%@*}"; rm -rf "$HOME/.cache/huggingface/hub/models--${repo//\//--}/.no_exist" 2>/dev/null || true
done
for spec in "${RUN[@]}"; do "$PY" - "${spec%@*}" "${spec##*@}" <<'PY'
import sys, time
from huggingface_hub import snapshot_download
repo, sha = sys.argv[1], sys.argv[2]
for a in range(1, 5):
    try:
        snapshot_download(repo, revision=sha, ignore_patterns=["*.gguf","*.onnx","onnx/*","*.msgpack","*.h5"],
                          max_workers=4, etag_timeout=30); print(f"  ok {repo}@{sha[:12]}"); break
    except Exception as e:
        print(f"  retry {a}/4 {repo}: {type(e).__name__}"); time.sleep(5*a)
PY
done

ALL=$(IFS=,; echo "${RUN[*]},${PROBE[*]}")
"$PY" -m runtime.tools.regen_compare --device "$DEVICE" --out "$OUT" --models "$ALL"
"$PY" -m runtime.tools.results_to_csv xfam --in "$OUT" --outdir docs/experiments/results
"$PY" -m runtime.tools.plot_results --resultsdir docs/experiments/results
cp "$OUT" docs/experiments/results/m7_xfam.json        # commit the bundle (manifest + per-model pins)
echo "M7 xfam: wrote docs/experiments/results/m7_xfam.csv + plots/m7_xfam_*.png + m7_xfam.json"
