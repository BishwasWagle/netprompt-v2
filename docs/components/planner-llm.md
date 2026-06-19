# Planner LLM (`llm_orchestrator/llm_runner.py`)

**Subsystem:** a model used by a loop (the Slow Planner's decision model)
**One-liner:** The fine-tuned Qwen2.5-1.5B-Instruct + LoRA that emits the 6-key orchestration decision JSON under grammar-constrained decoding.
**Runs on:** cuda:0

## Responsibility
Owns *only* the decision-model inference: loading the base model + LoRA adapter, building the constrained-decoding logits processor, generating raw text, and parsing it to a decision dict. It does NOT assemble the input object, validate against constraints, run the fallback, or compile the artifact — those belong to the Slow Planner (see `slow-planner.md`). It does NOT regenerate P4 rules (that is the Tier-2 regen model, `regen-llm.md`).

## Files
- `llm_runner.py` — `LLMOrchestrator` (load/generate), `parse_model_decision`, `safe_default_decision`
- `decision_grammar.py` — `build_decision_gbnf` (per-request GBNF over `orchestration_constraints`)
- `validator.py` — `validate_generated_decision` + `fallback_decision` (the contract + rule-based oracle)
- `../../train_decision_lora.py` — retrains a fresh LoRA from the oracle (distillation)
- adapters: `netprompt_qwen_kg_rag_orchestrator/{final_adapter_retrained, final_adapter, final_adapter_original_backup}`

## Interface
```python
LLMOrchestrator(model_name, adapter_path=None, use_4bit=True,
                device_map="auto", max_new_tokens=512)
LLMOrchestrator.load() -> LLMOrchestrator        # loads base + PEFT adapter, eval, greedy
LLMOrchestrator.generate_raw(input_object, max_new_tokens=None) -> str
parse_model_decision(raw_output) -> Dict          # 6 keys + llm_parse_status
build_decision_gbnf(input_object) -> str          # raises ValueError if constraints too thin
validate_generated_decision(input_object, decision) -> {"valid": bool, "errors": [...]}
fallback_decision(input_object) -> Dict           # deterministic rule-based decision
```

## How it works
- Emits a 6-key decision: `selected_sfc, selected_policy, selected_path, selected_relay, priority_class, deployment_mode` (parser also stamps `llm_parse_status`).
- **Grammar-constrained decoding (default ON; `NETPROMPT_LLM_CONSTRAINED=1`):** `_grammar_processors` builds a per-request GBNF from the same `orchestration_constraints` the validator checks — SFC+policy bound as valid *pairs*, path/relay from the KG-derived allowed sets, priority/mode fixed enums. The model must emit exactly the six keys in order and stop at `}`. Any error degrades gracefully to unconstrained.
- **Key insight:** a grammar-valid decision is validator-valid *by construction*, and the deterministic fallback fires only when output is INVALID — so **constrained decoding bypasses the fallback**. Disabling it (`=0`) re-enables the path where invalid output → rule-based fallback.
- Decoding is greedy (`do_sample=False`, sampling knobs cleared); generation is deterministic. Debug artifacts (prompt, raw output, grammar) are dumped under `outputs/`.
- Retrain (`train_decision_lora.py`): distills a balanced dataset labelled by `fallback_decision` (the oracle) using the orchestrator's own prompt format, fp32 LoRA on the P100 — drop-in adapter.

## Gotchas & lessons
- The **promoted default** is `final_adapter_retrained` (mission-appropriate on the known taxonomy). Known gap: generic / telemetry-only missions default to `BandwidthOptimized`. The original mode-collapsed to `LowLatencyVideoSFC`; preserved as `final_adapter` / `final_adapter_original_backup` for rollback/compare.
- `safe_default_decision()` (a `ReliableRelaySFC` multihop) is returned only when CPU inference yields no parseable JSON — distinct from a real LLM decision; report separately.
- `build_decision_gbnf` raises (→ unconstrained) when there are no candidate pairs or no relays — i.e. an unseeded KG silently loses the constraint.

## Usage
```bash
# inference (via the planner): orchestrate.py --adapter-path <dir>   (see usage.md §3 / §1.3)
# retrain:
~/netprompt-venv/bin/python train_decision_lora.py \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --out netprompt_qwen_kg_rag_orchestrator/final_adapter_retrained
```

## See also
- `../planner-lora-retrain.md` — retrain recipe (oracle distillation)
- `../planner-lora-eval.md` — adapter evaluation + the generic-mission gap
- `../planner-design.md` §7 — constrained decoding design
- `../usage.md` §3 — run/promote/roll-back the adapter
- `slow-planner.md` — the loop that calls this model
