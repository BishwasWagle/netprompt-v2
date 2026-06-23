# Slow Planner (`llm_orchestrator/orchestrate.py`)

**Subsystem:** Slow Planner (outer loop)
**One-liner:** The outer-loop KG-RAG orchestrator that reads the Neo4j topology + run history, prompts the decision LLM, validates/falls-back, and compiles a deployable `llm_generated_experiment_config.json` artifact.

## Responsibility
Owns the end-to-end *planning* pipeline: assembling the LLM input object from the KG + telemetry + history, invoking the decision model, enforcing the decision contract, and compiling the chosen SFC/policy into a concrete deployment artifact (P4 JSON + rule-file paths). It does NOT own the decision model itself (see `planner-llm.md`), the inner control loop / deployment execution (see [`../runtime-manager-design.md`](../design/runtime-manager-design.md)), or the KG's contents (it only reads them).

## Files
- `orchestrate.py` — `build_runtime_input_object` (assemble) + `run_pipeline` (decide→validate→compile→check) + CLI
- `kg_context.py` — Neo4j client + compact topology context + candidate SFC/policy set (with M-II policy aliases / fallback set)
- `history_retriever.py` — CSV normalization, live current-row, top-k similarity-ranked historical context
- `prompt_builder.py` — `SYSTEM_PROMPT`, `build_llm_input_object`, `build_inference_prompt`
- `validator.py` — `validate_generated_decision` (the contract) + `fallback_decision` (deterministic rule-based planner)
- `policy_compiler.py` — `compile_llm_decision_to_experiment_config` (decision → deployment artifact)
- `artifact_checker.py` — verifies compiled P4 JSON + rule files exist before deploy
- `config.py` — `RuntimeConfig.from_env` (Neo4j, model, adapter, paths, device)

## Interface
```python
build_runtime_input_object(cfg, mission_type, priority, bandwidth, delay_ms,
    loss_percent, battery_percent, observed_rtt_ms, observed_loss_percent,
    observed_throughput_mbps, use_fallback_candidates=False) -> Dict
run_pipeline(cfg, input_object, use_fallback_only=False, check_artifacts=True) -> Dict
#   -> {raw_model_output, decision, validation, experiment_config, artifact_check}
compile_llm_decision_to_experiment_config(decision, input_object, netprompt_root,
    experiment_type="LLM KG-RAG Dynamic SFC-P4") -> Dict
```

## How it works
- `build_runtime_input_object`: opens a `Neo4jContextClient`, pulls a topology snapshot + candidate SFC/P4 policy set, expands M-II policy aliases (or uses `fallback_candidate_actions()` if the KG is unpopulated), loads/normalizes the results CSV, builds the live current row, and ranks the top-3 historical runs by similarity. `build_llm_input_object` derives `orchestration_constraints` (allowed SFC ids / policy types / paths / relays) — the single source of truth the validator and grammar both consume.
- `run_pipeline`: unless `use_fallback_only`, loads `LLMOrchestrator`, generates raw output, parses to a 6-key decision, and validates. If invalid, prints a WARN and swaps in `fallback_decision`. Then `compile_llm_decision_to_experiment_config` re-validates and emits the artifact; `raise_if_artifacts_missing` aborts on missing P4/rule files (unless `--skip-artifact-check`).
- **Invariant:** a grammar-valid decision is validator-valid by construction, so with constrained decoding ON the rule-based fallback effectively never fires (see `planner-llm.md`).

## Gotchas & lessons
- The fallback path is *deterministic rule-based*, not the LLM — it only triggers on invalid output, so it's masked when constrained decoding is on. To exercise it, run `--fallback-only`.
- `single_switch` vs `multihop` selects entirely different rule-file fan-out in `policy_compiler.py`; an unknown `deployment_mode` raises.
- Topology/candidate sets come live from Neo4j every call — an empty/unseeded KG silently degrades to `fallback_candidate_actions()`.
- Provenance: original orchestrator by Bishwas & Kiran; our extensions are the KG-topology-seeded input object, constrained decoding, and the retrained LoRA.

## Usage
```bash
cd "$NETPROMPT_ROOT"
~/netprompt-venv/bin/python -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay --bandwidth 20 --delay 25 --loss 2 --battery 80 \
  --neo4j-uri bolt://localhost:7687 --neo4j-password netprompt123 \
  --device-map cuda:0 --no-4bit --output /tmp/plan.json
```

## See also
- `../planner-design.md` — authoritative slow-planner design (this doc summarizes + points)
- `../usage.md` §1 (end-to-end), §3 (planner LLM)
- `planner-llm.md` — the decision model + constrained decoding / fallback interaction
- `regen-llm.md` — the inner-loop Tier-2 regen model (separate subsystem)
