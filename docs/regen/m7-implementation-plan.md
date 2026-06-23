# M7 — Tier-2 regen (real serving) + reproducibility: implementation plan

**Companion to:** [runtime-manager-design.md](../design/runtime-manager-design.md) §7.3/§11/§13b.B and
[runtime-manager-implementation-plan.md](../design/runtime-manager-implementation-plan.md) §M7.
**Owner:** Kevin. **Status: M7 ✅ COMPLETE (2026-06-16).** All work items landed and live-verified:
#1/#2 LocalHFClient + GBNF decoding, #3 config, #4 grammar tightening, #5 gate L3 hook, #6 live
wiring, #7 reproducibility kit, #8 multi-model comparison harness, #9 DoD demo. Exit criteria met —
propose→gate→apply→observe + a committed **recovered episode** (test D, via the path), the **LLM-down
fail-safe**, the **comparison table** ([m7-regen-comparison.md](m7-regen-comparison.md)), and the
**reproducibility kit** (pinned model+rev, manifest, deterministic-candidate test). The real 1.5B
Coder emitting the *exact* corrective row is the documented model-capability frontier (test C `xfail`).
A latent gate bug (handle collision across tables) found and fixed along the way.

---

## 1. What M7 is

When the deterministic tiers (Tier-0 tune, Tier-1 reroute) are exhausted, Tier-2 asks an LLM to
**regenerate BMv2 table entries** for a switch. The candidate is grammar-constrained, gate-checked,
applied, and observed — and if it doesn't improve, rolled back; if the model is unavailable, the
loop escalates sooner (fail-safe, §7.4). Today this runs end-to-end against a `StubLLMClient`;
M7 makes it real.

## 2. Already done (the plumbing) — do not rebuild

`runtime/regen/` + the engine seam are built and green (`tests/unit/test_regen.py`):

- `grammar.py` — `gbnf()` (GBNF string) + `validate()`, both generated from `gate.KNOWN_TABLES`
  so grammar and gate can't drift; deliberately narrower than the gate (no `table_delete`).
- `prompt.py` — verbatim `TEMPLATE` + deterministic `build_prompt(...)`.
- `proposer.py` — `RegenProposer`: stateless K-cap (`config.REGEN_MAX_REJECTS=3` from the
  engine's `exclude` set), grammar-validate, dedup, **and the §7.4 fail-safe** (any
  `client.generate()` exception → treated as a failed attempt → eventually `None` → escalate).
- `llm_client.py` — `StubLLMClient` (canned completions); the real client is the M7 work.
- Seam: `RuntimeManager(regen_proposer=None)` (stubbed in M6/soak). The engine plugs a proposer
  in via `adapt.propose(... regen_proposer)` / `evaluator` `ctx.regen_proposer`.

The real client only needs **`.generate(prompt: str) -> str`** — the same protocol as the stub —
so swapping it in touches the engine almost nowhere.

## 3. Architecture decision: in-process transformers-cfg, not a separate server

`deploy/gpu-node/requirements-gpu.txt` already pins **`transformers-cfg>=0.2.5,<0.3`**: it brings
llama.cpp-style GBNF grammars to HF transformers as a **logits processor**, so
`grammar.gbnf()` constrains generation directly. Therefore the M7 serving path is:

> **In-process HF transformers** (the venv stack we validated for the GPU node) loading a pinned
> Qwen-Coder in **FP16, greedy**, with a `GrammarConstrainedLogitsProcessor` built from `gbnf()`.

No vLLM (unsupported on the P100's sm_60), no llama.cpp build required. With **2× P100** on the
node, the regen *Coder* model runs on **`cuda:1`** while the orchestrator *Instruct* model uses
`cuda:0` — no memory contention.

## 4. Locked decisions (recommended; override freely)

| Decision | Locked choice | Rationale / how to change |
|---|---|---|
| **Regen model** | **Qwen2.5-Coder-1.5B-Instruct**, pinned commit revision, `cuda:1`, FP16, greedy | ~3 GB → safe headroom, fast; a *Coder* model (P4 table-entry generation is a code task). The comparison harness (item #8) adds 7B-Coder / DeepSeek-Coder / Granite/StarCoder as extra rows. Bump the core model later via `NETPROMPT_REGEN_MODEL`. |
| **Gate L3 (dry-install)** | **Option A** — node-only injected `dry_install_fn` hook, **default-off** in unit/local tests | Closes a real soundness gap (`DUPLICATE_ENTRY` / handle drift that L2 simulation can't see) using existing infra (`simple_switch_CLI` + `Deployer` rollback). Keeps unit tests hardware-free. To defer: leave the hook unset and rely on deployer idempotency + post-deploy dominates-guard (Option B). |
| **Grammar tightening** | **Port + key-type only** — `port ∈ SWITCH_PORTS[switch]`, key-type conditioned on table | Eliminates the most common wasted-K-cap (gate L0-rejects out-of-range ports / wrong key-type). Full per-table *action* conditioning is deferred (documented residual over-accept; `validate()` + gate still catch it). |

## 5. Work items (file-level)

### Core path (unblocks an end-to-end real candidate)
1. **Real client `LocalHFClient`** — `runtime/regen/llm_client.py`.
   Lazy-singleton load of the pinned Qwen-Coder (fp16, `cuda:1`); `generate(prompt)`: tokenize →
   `model.generate(do_sample=False, max_new_tokens=…, logits_processor=[GBNF])` → decode the
   completion (strip the prompt). Conforms to `.generate` → drops into `RegenProposer` unchanged.
2. **Plumb `gbnf()` into decoding** — same file + `grammar.py`.
   Build `GrammarConstrainedLogitsProcessor` from `grammar.gbnf()` (transformers-cfg). `validate()`
   stays as defense-in-depth. **Verify the GBNF dialect parses** under transformers-cfg early (the
   `"\\n"` newline literal and `root` rule are the likely friction points) — a 20-line spike.
3. **Regen serving config** — `runtime/config.py`, `deploy/gpu-node/gpu-node.env`,
   `deploy/gpu-node/setup_gpu_node.sh`.
   Add `NETPROMPT_REGEN_{MODEL,REVISION,DEVICE,MAX_NEW_TOKENS}`; emit them from the env template
   (keep the three setup scripts self-consistent, as established this session).
6. **Wire the proposer into the live loop** — `runtime/runtime_manager.py` + a node entrypoint.
   `RegenProposer(LocalHFClient(...), table_state_fn=deployer.table_state, switch="s1")` → pass to
   `RuntimeManager(regen_proposer=…)`. **Confirm `run_episode` feeds the deployer's *live*
   `table_state` as the gate's `current_tables`** — the gate's L2 (`_check_regen` → `_simulate`)
   self-rejects with "no current table state" otherwise; unit tests pass a fixture, the live loop
   must pass real state. Add `--with-regen` to `soak.py` / `run_episode`.

### Quality / soundness
4. **Tighten grammar↔gate over-accept** — `runtime/regen/grammar.py`.
   Parametrize `gbnf(switch)`: `port ::= "<p>" | …` from `SWITCH_PORTS[switch]`; key-type by table
   (forward_table→MAC, else IPv4). Keep `validate()` in lockstep. (Per-table action conditioning
   deferred — document the residual.)
5. **Gate L3 dry-install hook** — `runtime/gate.py` + deployer.
   Inject an optional `dry_install_fn(commands, switch) -> GateResult` invoked after L2; node-only,
   default-off. Real apply to a scratch context (or the live switch in a deployer-rollback
   transaction) to catch install-time errors L2 can't.

### Paper deliverables
7. **Reproducibility kit** — `config.py`, `prompt.py`, new manifest.
   Pin model commit hash; normalize int/float knob formatting in the prompt; emit a manifest
   (model+rev, transformers/transformers-cfg versions, greedy params, `gbnf()` hash). Repro test:
   same inputs → same candidate twice (batch=1).
8. **Multi-model comparison harness** — new `runtime/tools/regen_compare.py`.
   Fixed violation scenarios → propose→gate for Qwen-Coder vs DeepSeek-Coder vs Granite/StarCoder;
   record accept-rate, K-cap usage, candidate validity, latency → the paper's comparison table.
9. **Phase-5 logging + DoD demo** — runtime + `tests/integration/`.
   Structured episode/verdict logging; a live integration test showing **≥1 recovered episode via
   real Tier-2** + re-confirm the **LLM-down fail-safe** with the real client path.

## 6. Sequencing

```
Core:    [1 client] → [2 gbnf plumbing] → [3 config] → [6 wire live + current_tables]
Quality: [4 grammar tighten]   [5 gate L3]            (parallel, model-free except a node for L3)
Paper:   [7 repro kit] → [8 comparison harness] → [9 logging + DoD demo]
```
Critical path is 1→2→3→6: a real constrained candidate flowing propose→gate→apply→observe on the
live testbed. Items 4 and 5 are model-free and can proceed in parallel.

## 7. Risks / open questions

- **transformers-cfg GBNF dialect** vs our `gbnf()` string — spike item #2 first (cheap), before
  building the client around it.
- **Qwen-Coder memory on P100 16 GB FP16** — 1.5B is safe; if we later try 7B (~15 GB) watch the
  KV cache; the comparison harness can use the 2nd P100 or short context.
- **Greedy determinism** isn't bitwise-stable across transformers/transformers-cfg versions or
  batch sizes — pin versions, batch=1; that's what the repro manifest captures.
- **`current_tables` in the live loop** must be the deployer's real `table_state`, not a fixture —
  confirm/​wire in item #6 (the single most likely integration foot-gun).
- **Two models on the GPU** (orchestrator Instruct on `cuda:0`, regen Coder on `cuda:1`) — keep
  them on separate devices; don't co-locate on one P100.

## 8. Exit criteria (M7 done)

1. A **real** regen candidate flows propose → gate → apply → observe end-to-end on the live testbed
   and deploys **≥1 recovered episode** (DoD #3).
2. **LLM-down** degrades to escalate-sooner with the real client path (§7.4) — re-confirmed.
3. **Comparison table** generated across ≥3 models (item #8).
4. **Reproducibility kit** complete: pinned model+revision, greedy constrained decoding, manifest,
   fixture matrix, soak log — a reviewer can re-run (DoD #4).
