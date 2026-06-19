# Tier-2 Regen LLM (`runtime/regen/llm_client.py`)

**Subsystem:** a model used by a loop (the Runtime Manager's Tier-2 regen component)
**One-liner:** A grammar-constrained Qwen2.5-Coder-1.5B-Instruct component the Runtime Manager *can* call at the top of its adapt ladder (tier 2) to regenerate P4 table rules — it is NOT the Runtime Manager.
**Runs on:** cuda:1

## Responsibility
Owns *only* candidate generation: given a violation + envelope + current table state, propose replacement BMv2 `table_modify`/`table_add` lines under GBNF-constrained greedy decoding. It does NOT own the adapt ladder, the gate (which remains the authority on every candidate), apply/observe, or escalation — those are the Runtime Manager. It is a fail-safe leaf: an unavailable/erroring model degrades to escalate-sooner, leaving the network untouched.

## Files
- `llm_client.py` — `StubLLMClient` (canned), `LocalHFClient` (real M7 HF client), `manifest()`
- `grammar.py` — `gbnf(switch)` (constrained-decoding grammar) + `validate(text, switch)` (local check)
- `prompt.py` — `TEMPLATE` (verbatim), `build_prompt(...)`, `render_tables`
- `proposer.py` — `RegenProposer` (the `adapt.propose` seam plug; K-cap + gate-deferral)
- `../config.py` — `REGEN_*` constants (`REGEN_MAX_REJECTS=3`, model, revision, `REGEN_DEVICE="cuda:1"`, `REGEN_MAX_NEW_TOKENS=64`)

## Interface
```python
LocalHFClient(model=None, revision=None, device=None, max_new_tokens=None,
              grammar_str=None, switch="s1").generate(prompt: str) -> str
StubLLMClient(responses: list).generate(prompt) -> str   # "" when exhausted -> escalate
manifest() -> dict                                       # reproducibility manifest (DoD #4)
gbnf(switch="s1") -> str ;  validate(text, switch="s1") -> bool
RegenProposer(client, table_state_fn, switch="s1",
              max_rejects=config.REGEN_MAX_REJECTS).__call__(diag, env, state, exclude) -> Candidate|None
```

## How it works
- `LocalHFClient` lazy-loads the pinned model (FP16, greedy: `do_sample=False`, `num_beams=1`) once on first `generate`, applies an `IncrementalGrammarConstraint` logits processor, resets parser state per call, and returns only complete (newline-terminated) lines via `_complete_lines` (the unbounded `root ::= line+` grammar otherwise truncates mid-line at the token cap).
- The grammar is built from the *same constants the gate enforces* (`KNOWN_TABLES` / `PORT_ACTIONS` / `NOARG_ACTIONS` / `SWITCH_PORTS`), so grammar and gate stay aligned; it is deliberately narrower than the gate (no `table_delete`). With constrained decoding on, `validate()` is defense-in-depth.
- `RegenProposer` is stateless across calls: it counts prior REGEN candidates in `exclude`, K-caps at `REGEN_MAX_REJECTS=3`, and within budget generates → `validate()` → de-dupes → returns a `Candidate(REGEN, (switch, text))`. Generation exceptions are caught and treated as a rejected attempt (§7.4 fail-safe) — never a crash.
- `manifest()` pins model+revision, decoding params, library versions, and content hashes of the grammar + prompt template for reproducibility.

## Gotchas & lessons
- **Default-stubbed:** `run_episode` / `run_from_planner` ship with tier 2 escalating (no model wired). The real client is wired only by `soak --with-regen` and the M7 live tests.
- Pinned HF revision (`REGEN_REVISION`) — change it and the reproducibility manifest hash shifts.
- vLLM is unsupported on the P100 (sm_60), so this is in-process HF; `_resolve_device` degrades off `cuda:1` to a present GPU or CPU.
- Grammar over-accepts per-table action sets (e.g. `priority_table forward` is grammar-valid); the gate L0-rejects that residual.

## Usage
```bash
# in-loop only — not a standalone CLI. Plus the offline tools:
python -m runtime.tools.regen_manifest    # print the reproducibility manifest
python -m runtime.tools.regen_compare     # stub vs real candidate comparison
# wire the real model into a soak run:
python -m runtime.soak --with-regen
```

## See also
- `../m7-implementation-plan.md` — M7 real-client build plan
- `../m7-regen-comparison.md` — stub vs real comparison
- `../runtime-manager-design.md` §7.3 (adapt ladder), §7.4 (fail-safe)
- `../usage.md` §4 — running the Tier-2 regen LLM
- `slow-planner.md` / `planner-llm.md` — the outer-loop planner (separate subsystem)
