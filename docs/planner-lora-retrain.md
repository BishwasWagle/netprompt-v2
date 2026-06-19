# Planner LoRA retrain (approach #3) — methods, resources, insights

**Status:** training in progress (2026-06-19). This documents the process *so far* — the
why, the exact steps, the setup, and the gotchas — so the run is reproducible and the
decisions are auditable. Evaluation/results are appended once training completes.

## 1. Why retrain

The planner's fine-tuned LoRA (`Qwen2.5-1.5B-Instruct` +
`netprompt_qwen_kg_rag_orchestrator/final_adapter`) **collapses to `LowLatencyVideoSFC`
for every mission**. The two cheaper fixes were exhausted (see
[runtime-planner-contracts.md](runtime-planner-contracts.md) §6c/§6d):

- **#1 constrained decoding (done, kept):** forces a complete, valid 6-key decision and
  stops the rambling — but can't change *which* valid option the model picks.
- **#2 prompt/few-shot (negative, reverted):** a mission→SFC rubric + 3 exemplars did
  **not** move the choice; the **base** model behaves identically and the original
  **unconstrained** run also emitted LowLatency. A 1.5B model doing one-shot JSON
  selection over a large structured input collapses to a single mode — not promptable.

So decision *quality* needs new weights. Approach #3 trains a **fresh LoRA** that
distills the system's known-correct decision policy into the model.

## 2. Safety first — preserve the original

Before touching anything, the shipped adapter was copied so we can roll back or compare:

```
cp -r netprompt_qwen_kg_rag_orchestrator/final_adapter \
      netprompt_qwen_kg_rag_orchestrator/final_adapter_original_backup   # 86 MB
```

The orchestrator's adapter path is configurable (`--adapter-path` / `NETPROMPT_LLM_ADAPTER`),
so switching between original and retrained is a one-flag change — no overwrite.

## 3. The oracle — where correct labels come from

The deterministic **`validator.fallback_decision`** already encodes the correct
mission/telemetry → SFC policy (it's what carries the system today when the LLM is
rejected). We distill *it* into the LoRA. Its mapping (branch order matters):

| Condition (first match wins) | SFC | path | mode |
|---|---|---|---|
| `emergency_alert_relay` **or** loss ≥ 3 **or** observed_loss ≥ 5 **or** any unavailable relay | ReliableRelaySFC | backup | multihop |
| `long_term_soil_monitoring` **or** battery < 40 | EnergyAwareSFC | primary | single_switch |
| `real_time_pest_detection` **or** delay ≤ 10 | LowLatencyVideoSFC | primary | multihop |
| bandwidth ≥ 30 | BandwidthOptimizedSFC | primary | single_switch |
| else | ReliableRelaySFC | backup | multihop |

Using the oracle as the label source means every training target is **already
validator-valid and grammar-valid** — training and the §6c constrained decoder agree by
construction.

## 4. Dataset — balanced, generate-then-bin

Script: [`train_decision_lora.py`](../network/milestone-II-latest/netprompt-milestone-II/train_decision_lora.py).

- **Fixed context from the live KG (once):** `get_topology_snapshot` +
  `get_candidate_sfc_policy_set` → the same `compact_topology_context` + candidate set
  the orchestrator uses at inference. Reused for every example (topology is static).
- **Inputs:** for a target SFC, sample telemetry that lands on its oracle branch
  (`_telemetry_for`), and **half the time** use a named mission (e.g.
  `emergency_alert_relay`), **half** a generic one (`routine_field_patrol`) so the model
  must read telemetry, not just memorize mission strings.
- **Generate-then-bin:** build the input, label it with `fallback_decision`, bin by the
  returned SFC, and keep an **equal count per SFC** — so branch-order subtleties can't
  skew balance and labels are always correct.
- **Example shape:** `prompt = build_inference_prompt(input_object)` (the *exact*
  inference format, `<|system|>…<|user|>…Input:{json}…<|assistant|>\n`), `target =`
  the compact 6-key decision JSON.

Verified before spending GPU time (per-class = 8 dry run): **8/8/8/8 across all four
SFCs**, labels correct, prompt ends at `<|assistant|>\n`.

Final dataset: **640 examples, 160 per SFC**, seeded (`--seed 0`) for reproducibility.

## 5. Tokenization — loss on the decision only

```
prompt_ids + (target_ids + EOS)         # input
[-100]*len(prompt_ids) + (target_ids+EOS)  # labels  -> prompt masked
```

The model is graded only on generating the decision, not on echoing the (large, fixed)
prompt. EOS is appended so it learns to **stop** at the closing brace (the original
model's rambling came from never learning to stop).

## 6. Model + LoRA — match the original, train fresh

- **Base:** `Qwen/Qwen2.5-1.5B-Instruct`.
- **Fresh LoRA**, hyperparams copied from the original `adapter_config.json` so the
  output is drop-in compatible: `r=16, lora_alpha=32, lora_dropout=0.05, bias=none,
  task=CAUSAL_LM`, target modules `q,k,v,o,gate,up,down_proj`.
- **Trainable: 18,464,768 params (1.18% of 1.56B).**

## 7. Training setup + the fp32 decision

- Manual loop (no `datasets` lib — not installed): **batch 1 + gradient accumulation 8**,
  `gradient_checkpointing` + `enable_input_require_grads`, AdamW `lr 2e-4`, grad-clip 1.0,
  **3 epochs**, `max_len 2600`.
- **fp32, deliberately.** The P100 is `sm_60`: **no bf16**, and fp16 Adam states
  underflow → unstable LoRA training. Full fp32 (1.5B ≈ 6 GB weights) fits in the 16 GB
  card with gradient checkpointing and is numerically stable. The saved LoRA weights are
  dtype-agnostic, so **inference still runs fp16**.

Launch command:

```
source deploy/gpu-node/gpu-node.env
~/netprompt-venv/bin/python train_decision_lora.py \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained \
  --per-class 160 --epochs 3 --device cuda:0
```

## 8. Resources

- **GPU:** 1× Tesla P100-16GB on `cuda:0` (the other P100, `cuda:1`, is reserved for M7
  Tier-2 regen serving). Training holds **~11 GB**, util pinned at **100%**.
- **Host:** 125 GB RAM (≈4 GB used — never the bottleneck), `~/netprompt-venv`
  (torch 2.3.1+cu121, transformers 4.46.3, peft 0.13.2, accelerate).
- **KG:** local Neo4j `bolt://localhost:7687` (read once for context).
- **Throughput:** ≈ **1.8 s/example** (fp32 + ~2400-token prompts) → 640×3 ≈ **~55 min**.

## 9. Insights / gotchas so far

- **Loss falls fast:** `0.33 → 0.17 → 0.12` within the first 240 examples — the targets
  are short, structured, and oracle-consistent, so the mapping is easy to fit. (Watch for
  over-fitting to the oracle — which is acceptable here: mimicking the correct rule-based
  policy *is* the goal.)
- **The prompt, not the model, dominates cost.** ~2400 of the ~2460 tokens per example
  are the **fixed topology JSON**; the model only needs the small mission/telemetry head
  + the short target. fp32 over that long sequence is why it's ~1.8 s/example. (A future
  speed-up: trim the topology in the *training* prompt, or train in fp16 with fp32 LoRA
  params — left as-is here for stability + inference-fidelity.)
- **Process-watching gotcha:** PyTorch renames the main thread to **`pt_main_thread`**, so
  `pgrep <name>` reports the job as gone. Use `pgrep -f train_decision_lora` or, more
  reliably, `nvidia-smi --query-compute-apps=pid,used_memory,process_name` to confirm a
  GPU job is alive. (This briefly looked like a crash mid-run; it wasn't.)
- **Why distillation works where prompting didn't:** prompting asks a frozen, biased model
  to behave; training *moves the weights* toward the policy. The oracle gives clean,
  balanced supervision the base model never had.

## 10. Pending (to append on completion)

- Evaluate the retrained adapter on the held-out mission battery
  (`emergency_alert_relay`, `bulk_data_transfer`, `real_time_video`,
  `soil_moisture_survey`) — does it now pick mission-appropriately, with
  `llm_parse_status: parsed_json`?
- Side-by-side vs the original (`--adapter-path final_adapter_original_backup`).
- Decide whether to promote `final_adapter_retrained` to the default adapter.
