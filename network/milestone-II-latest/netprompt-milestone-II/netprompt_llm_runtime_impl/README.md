# NetPrompt KG-RAG LLM Runtime Implementation

This package converts the research notebook into a plug-and-play runtime system for NetPrompt.
The LLM does **not** generate P4 code or shell commands. It selects a constrained JSON orchestration decision, which is then validated and compiled into existing P4/BMv2 artifacts.

## Runtime flow

```text
Mission + telemetry
  -> Neo4j topology context
  -> candidate SFC/P4 policy set
  -> KG-RAG historical context
  -> fine-tuned Qwen orchestrator
  -> JSON decision parser
  -> constraint validator
  -> deterministic policy compiler
  -> llm_generated_experiment_config.json
```

## Install/copy into NetPrompt

From your project root:

```bash
cd /home/cc/netprompt-milestone-II
cp -r /path/to/netprompt_llm_runtime_impl/llm_orchestrator .
cp -r /path/to/netprompt_llm_runtime_impl/scripts .
cp -r /path/to/netprompt_llm_runtime_impl/configs .
```

Then install runtime requirements if needed:

```bash
pip install -r requirements-runtime.txt
```

## Environment variables

Use `configs/neo4j_config.example.env` as a template:

```bash
source configs/neo4j_config.example.env
```

Do not commit real passwords.

## Generate an LLM-compiled experiment config

```bash
python3 -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay \
  --priority critical \
  --bandwidth 20 \
  --delay 25 \
  --loss 2 \
  --battery 80 \
  --output outputs/llm_generated_experiment_config.json \
  --save-input
```

If you want to test the pipeline without loading the model:

```bash
python3 -m llm_orchestrator.orchestrate \
  --mission emergency_alert_relay \
  --priority critical \
  --bandwidth 20 \
  --delay 25 \
  --loss 2 \
  --battery 80 \
  --fallback-only \
  --output outputs/fallback_generated_experiment_config.json
```

If your P4/rule files are not yet in place, add:

```bash
--skip-artifact-check
```

## Run an existing experiment script

This helper bridges the compiled config into your existing runner command:

```bash
python3 scripts/run_llm_compiled_experiment.py \
  --config outputs/llm_generated_experiment_config.json \
  --runner 'python3 your_existing_runner.py --config {config}' \
  --dry-run
```

Remove `--dry-run` when the command is correct.

## Parse a new LLM-driven experiment result

If your runner writes metrics to JSON:

```bash
python3 scripts/parse_llm_experiment_results.py \
  --config outputs/llm_generated_experiment_config.json \
  --result-json outputs/latest_metrics.json \
  --output-csv outputs/llm_experiment_results_clean.csv
```

## Files

- `llm_orchestrator/kg_context.py`: Neo4j topology and candidate policy retrieval.
- `llm_orchestrator/history_retriever.py`: historical outcome retrieval from CSV.
- `llm_orchestrator/prompt_builder.py`: LLM input object and prompt construction.
- `llm_orchestrator/llm_runner.py`: model/adapter loading and JSON generation.
- `llm_orchestrator/validator.py`: constraint validation and deterministic fallback.
- `llm_orchestrator/policy_compiler.py`: decision-to-P4 artifact compilation.
- `llm_orchestrator/artifact_checker.py`: pre-run P4/rule file checks.
- `llm_orchestrator/orchestrate.py`: main CLI entry point.
