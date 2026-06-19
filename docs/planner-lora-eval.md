# Planner LoRA — Evaluation (retrained vs original)

**Date:** 2026-06-19. **Subject:** the retrained planner LoRA
(`final_adapter_retrained`, approach #3) vs the original shipped adapter
(`final_adapter`). **Question:** did the retrain fix the decision-quality collapse — does
the model now pick a *mission-appropriate* SFC instead of always `LowLatencyVideoSFC`?

Companion docs: [planner-lora-retrain.md](planner-lora-retrain.md) (how the adapter was
trained) · [planner-design.md](planner-design.md) (the planner as a whole) ·
[runtime-planner-contracts.md](runtime-planner-contracts.md) §6c/§6d (constrained decoding
+ the prior prompt/few-shot negative result).

## 1. Method

- **Adapters:** original = `netprompt_qwen_kg_rag_orchestrator/final_adapter`; retrained =
  `…/final_adapter_retrained` (640 balanced examples distilled from the
  `fallback_decision` oracle, 3 epochs, final mean loss 0.0066).
- **Decoding:** grammar-constrained (the production default, `NETPROMPT_LLM_CONSTRAINED=1`),
  so **every** output is a valid 6-key decision (`llm_parse_status: parsed_json`) — the
  test is purely *which* SFC, not whether the JSON is well-formed.
- **Oracle / "expected":** `validator.fallback_decision` (emergency/loss≥3 → Reliable·backup;
  soil/battery<40 → Energy·primary; pest/delay≤10 → LowLatency·primary; bw≥30 → Bandwidth·primary).
- **KG:** local Neo4j, seeded with the planner topology (`bolt://localhost:7687`).
- **Probe command** (per mission; swap `--adapter-path` for original vs retrained):

```
source deploy/gpu-node/gpu-node.env
python -m llm_orchestrator.orchestrate \
  --mission <MISSION> --bandwidth <BW> --delay <DELAY> --loss <LOSS> --battery <BATT> \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit \
  --adapter-path netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained \
  --output /tmp/eval.json
# selected_sfc = the decision under test; decision.llm_parse_status = parsed_json
```

Three probe sets: **(A)** known mission names, **(B)** novel mission names (OOD),
**(C)** generic mission names where only telemetry disambiguates.

## 2. Results

| # | Mission (telemetry) | Expected | Original | Retrained |
|---|---|---|---|---|
| A1 | `emergency_alert_relay` (bw 20, delay 25, loss 2, batt 80) | ReliableRelaySFC | LowLatency ✗ | **ReliableRelaySFC ✓** |
| A2 | `bulk_data_transfer` (bw 80, delay 40, loss 1, batt 90) | BandwidthOptimizedSFC | LowLatency ✗ | **BandwidthOptimizedSFC ✓** |
| A3 | `real_time_pest_detection` (bw 40, delay 6, loss 1, batt 80) | LowLatencyVideoSFC | LowLatency ✓\* | **LowLatencyVideoSFC ✓** |
| A4 | `long_term_soil_monitoring` (bw 15, delay 50, loss 1, batt 25) | EnergyAwareSFC | LowLatency ✗ | **EnergyAwareSFC ✓** |
| B1 | `real_time_video` (bw 40, delay 10, loss 1, batt 80) | LowLatencyVideoSFC | LowLatency | BandwidthOptimizedSFC ✗ |
| B2 | `soil_moisture_survey` (bw 15, delay 50, loss 1, batt 25) | EnergyAwareSFC | LowLatency | BandwidthOptimizedSFC ✗ |
| C1 | `routine_field_patrol` (bw 20, delay 5, loss 1, batt 80) | LowLatencyVideoSFC | LowLatency | BandwidthOptimizedSFC ✗ |
| C2 | `routine_field_patrol` (bw 15, delay 50, loss 1, batt 20) | EnergyAwareSFC | LowLatency | BandwidthOptimizedSFC ✗ |

\* the original only ever emits `LowLatencyVideoSFC`, so it "passes" LowLatency rows by accident, not by reasoning.

**Score (by intent):**
- **Original:** mode-collapsed — 1 of 4 SFC *types* ever reachable. Effectively right only
  when the answer happens to be LowLatency.
- **Retrained:** **4/4 on known mission names (set A)**; **0/4 on novel/generic (sets B, C)**.

## 3. Analysis

- **The retrain broke the collapse.** On the known mission taxonomy the model is now
  **mission-sensitive and correct** — a categorical improvement over "always LowLatency."
- **It learned names, not telemetry.** Sets B and C isolate telemetry reasoning (novel or
  generic mission name, answer determined by `delay`/`battery`). There the model defaults
  to **BandwidthOptimizedSFC** rather than reading the numbers. Likely causes: the
  telemetry digits sit deep in a ~2400-token prompt a 1.5B model under-attends to, and
  mission name is the easier signal to fit during training.
- **Format is a solved problem** (orthogonal to this eval): constrained decoding (§6c)
  guarantees a valid, complete 6-key decision regardless of adapter — every row above is
  `parsed_json`.

## 4. The default-config interaction (corrected after the 2026-06-19 review)

> **Correction.** An earlier version of this section claimed "keep the original adapter —
> the fallback decides every mission correctly." **That is false under the production
> default** (constrained decoding ON). Constrained decoding only lets the *fallback* win
> when the LLM output is **invalid**; the grammar makes the output **always valid**, so the
> LLM's choice is **used** and the fallback is **bypassed**. Verified below.

For `emergency_alert_relay` (correct = ReliableRelaySFC):

| | constrained **OFF** | constrained **ON** (default) |
|---|---|---|
| **original** (`final_adapter`) | invalid JSON → fallback → ReliableRelaySFC ✓ | **LowLatencyVideoSFC ✗** (valid, used) |
| **retrained** (`final_adapter_retrained`) | — | **ReliableRelaySFC ✓** (valid, used) |

So the **current shipped default — original adapter + constrained decoding — emits the
wrong SFC** (its mode-collapsed LowLatency) for every non-LowLatency mission, because the
grammar validates that wrong choice and it bypasses the fallback. The retrained adapter is
the one that is *correct under the production setting*.

**Implications / options:**
1. **Promote the retrained adapter (recommended).** Under constrained-on it is correct on
   all known mission types; the original is wrong on all non-LowLatency ones. This is the
   right default for "LLM in the loop." Residual gap: generic/telemetry-only missions
   (sets B/C) still resolve to BandwidthOptimized.
2. **Original + constrained decoding OFF** — the only config where the fallback decides
   *every* mission correctly (LLM output invalid → fallback). But then the LLM is never
   actually used (defeats the KG-RAG purpose) and the §6c format guarantee is off.
3. **Improve the retrain** — telemetry-weighted dataset (telemetry up front, oversample
   generic-mission examples) or a larger model behind the same constrained-decoding seam,
   to close the B/C gap so the promoted adapter is correct everywhere.

The default has **not** been auto-changed in code — promotion is the user's call
(`NETPROMPT_LLM_ADAPTER` / `--adapter-path`) — but the review's recommendation is **option
1 (promote)**, since the status quo is strictly the worst of the three for decision quality.

**Bottom line:** approach #3 met its goal — the planner LLM is no longer mode-collapsed and
is now the **only** adapter that decides correctly under the production (constrained-on)
default. Robust telemetry generalization (sets B/C) is the remaining gap.
