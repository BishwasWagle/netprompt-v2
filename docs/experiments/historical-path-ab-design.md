# Design — Historical-Performance-Aware Path-Selection A/B

**Subject.** The experiment design for the one KRONOS-draft claim that has **no result in the
paper**: *"Historical-performance-aware path-selection experiments additionally demonstrate the
use of graph-maintained operational knowledge during relay-path decisions"* (abstract / intro /
§IV). This is build **#8** in [repeat-kronos-experiments-plan.md](repeat-kronos-experiments-plan.md)
— the **build-or-drop** item. This document specifies the A/B that decides it.

**The claim, stated testably.** *Logged operational history on a relay path (prior RTT / loss /
verdicts) changes the **next relay-path decision** (primary ↔ backup) — i.e. given the same
mission + live telemetry, the planner picks a **different path** when history says the preferred
path performed badly.*

**Path note.** Planner code is under `$NETPROMPT_ROOT/llm_orchestrator/`
(`NETPROMPT_ROOT=network/milestone-II-latest/netprompt-milestone-II`); runtime code is repo-root
`runtime/`. Citations use this convention. Findings below were ground-truthed against the live
source and the crux adversarially verified.

---

## 1. Bottom line — the chain is broken at the relay/path axis

**Verdict: history is *plumbed into the prompt but not wired to the path decision* — the as-is
A/B is expected to be NULL.** Three independent facts, each verified in source:

1. **The decision never reads history through a deterministic edge.** `selected_path` /
   `selected_relay` come from exactly one of two producers, neither of which consumes history:
   - **LLM (grammar):** path/relay are bound *only* from `allowed_paths` (hardcoded
     `["primary","backup"]`, `prompt_builder.py:74`) and `allowed_relays` (KG topology set,
     `prompt_builder.py:41-47`) via GBNF (`decision_grammar.py:61-62`). The grammar **cannot
     inject a history-driven path even in principle**.
   - **Deterministic fallback:** `validator.fallback_decision` (`validator.py:76-128`) reads
     **only** mission / telemetry / resource / topology / candidates (`validator.py:78-82`) and
     keys path on `mission_type` + **live CLI telemetry** (`validator.py:95-119`). It ignores all
     three history fields. It fires on `--fallback-only` (`orchestrate.py:91-93`) **and on every
     LLM-validation failure** (`orchestrate.py:107-111`).
2. **History reaches only the prompt.** Three history fields are assembled into the LLM input and
   nowhere else: `kg_rag_historical_context` (`prompt_builder.py:68`), `runtime_feedback`
   (`prompt_builder.py:70`), `historical_path_decisions_from_kg` (`kg_context.py:249`). So history
   can move the path *only if* the LLM branch runs **and** the 1.5B model attends to those fields —
   and the planner is documented **0/4 on telemetry, keying on mission name**
   ([planner-lora-eval](../planner/planner-lora-eval.md)).
3. **The runtime never writes per-path history anyway.** `write_verdict` stores
   `{correlation_id, timestamp, outcome, tier_reached, headroom, trace}` — **no relay, no path**
   (`runtime/kg_client.py:131-144`; `Verdict` dataclass `runtime/contracts.py:232-240`).
   `BaselineSnapshot.per_flow` does carry `{rtt_avg_ms, throughput_mbps, loss_percent}` but keyed
   per `field_id` as an opaque JSON payload, and the planner **never reads it**
   (`runtime/contracts.py:176-183`). The only per-path read, `historical_path_decisions_from_kg`,
   reads `PathDecision` nodes written **only by the static seed** `update_topology_state.py:97-137`
   — never by the runtime.

**Consequence:** the honest reading is that the paper's claim is **not currently backed by the
system** — history is wired for *context*, not for *path control*. So this is genuinely
**build-or-drop**, and the design below gives both branches.

### The three "history" channels, and why none closes the loop on the path axis

| Channel | Source | Reaches | Per-path? | Knob |
|---|---|---|---|---|
| `kg_rag_historical_context` | **static CSV** `final_milestone2_results_clean.csv` (`config.py:51-54`); `selected_path` *inferred* from `policy_type` (`history_retriever.py:34-39`) | LLM prompt only | path inferred, not logged | `--results-csv` / `NETPROMPT_RESULTS_CSV` (empty file → all-None block, `history_retriever.py:74-79,124-131`) |
| `runtime_feedback` | **runtime Verdicts** (the one true closed loop), per-SFC escalation rate (`analytics.py:40-110`) | LLM prompt only | **no** — per-SFC, not per-path | `NETPROMPT_PLANNER_FEEDBACK=0/1` (`analytics.py:104-105`) |
| `historical_path_decisions_from_kg` | **static seed** `update_topology_state.py` `PathDecision` nodes, per-scenario (`kg_context.py:69-78,249`) | LLM prompt only | per-path but **not runtime-written** | (re)seed / withhold `PathDecision` nodes |

---

## 2. Design A — the as-is A/B (cheap, honest, expected-null)

**Purpose.** Settle empirically whether the claim holds *with the system as built* — does toggling
the available history actually flip the planner's `selected_path`? This is a legitimate result
either way: a flip backs the claim; a null is an honest **scoping/limitation** finding (history is
plumbed but the 1.5B model doesn't exploit it for path), which is what the evidence predicts.

**What it isolates.** The **planner decision only** — at the `orchestrate` boundary, before any
deployment. **Not** through the E3 harness: `e3_compare.proposed_sfc` returns only
`dec["selected_sfc"]` and discards the path (`e3_compare.py:137-139`), and `e3_measure` sets
`final_path` from the **runtime reroute** (`deployer.state`, `e3_measure.py:159-160`) — so an E3
path A/B is moot by construction.

**Hypotheses.**
- **H-A0 (null, expected):** toggling history leaves `selected_path` / `selected_relay`
  unchanged for every probe (A == B).
- **H-A1 (claim holds):** for ≥1 probe where history contradicts the telemetry-default path, the
  history-on arm selects the history-favored path and history-off does not.

**Independent variable — the history knob (toggle *only* this):**
- **Arm HIST:** `NETPROMPT_PLANNER_FEEDBACK=1` + `--results-csv <populated.csv>` (history present).
- **Arm NONE:** `NETPROMPT_PLANNER_FEEDBACK=0` + `--results-csv /tmp/empty.csv` (both history
  blocks collapse to all-None).
- Everything else identical: same `--mission/--bandwidth/--delay/--loss/--battery`, same seeded KG,
  same adapter, constrained-on, `--no-4bit`, `--device-map cuda:0`.

**Must use the real LLM.** Never `--fallback-only`; the fallback ignores history, so a fallback run
makes A == B trivially. Verify each decision came from the model — `llm_parse_status ==
"parsed_json"` **and** `validation.valid` — not the fallback (`orchestrate.py:107-111`).

**Dependent variables.** `decision.selected_path`, `decision.selected_relay` (primary; from the
`--output` JSON, `orchestrate.py:219-220`); `decision.selected_sfc` (secondary). Capture the
exact differing prompt block with `--save-input` to **prove the only delta is the history fields**.

**The probe set must make history *pivotal*.** A flip is only possible where the history-favored
path differs from the path the model would pick from telemetry alone. Construct probes where the
populated CSV / verdicts say the **telemetry-default path performed badly** (e.g. mission whose
telemetry → primary, but history shows primary had high loss / rollbacks → should favor backup).
If no probe's history implies a different path than the telemetry default, the A/B can only return
null regardless of model behavior — so this construction is part of the experiment, not an
afterthought.

**Protocol (new tiny driver — no testbed, no `sudo`).**
```bash
# repro/hist_ab.sh (to build): for each probe, run orchestrate twice, diff selected_path/relay
source deploy/gpu-node/gpu-node.env            # adapter, cuda:0, constrained-on
cd "$NETPROMPT_ROOT"
for probe in "${PROBES[@]}"; do
  NETPROMPT_PLANNER_FEEDBACK=1 python -m llm_orchestrator.orchestrate $probe \
    --results-csv "$POPULATED_CSV" --no-4bit --device-map cuda:0 \
    --save-input /tmp/hist_${id}_HIST.in.json --output /tmp/hist_${id}_HIST.json
  NETPROMPT_PLANNER_FEEDBACK=0 python -m llm_orchestrator.orchestrate $probe \
    --results-csv /tmp/empty.csv --no-4bit --device-map cuda:0 \
    --save-input /tmp/hist_${id}_NONE.in.json --output /tmp/hist_${id}_NONE.json
done
# scorer: assert input deltas are history-only; tabulate selected_path/relay HIST vs NONE + flip?
```

**Success / interpretation.** Report per-probe `selected_path` HIST vs NONE, whether it flipped,
and whether the flip was *toward the history-favored path*. **A null is a valid, publishable
limitation** ("history is available to the planner but does not alter path selection at 1.5B");
a flip is the (unlikely) positive. Either way it is honest and settles build-or-drop.

**Outputs.** `docs/experiments/repeat-results/historical_path_ab.csv` (+ a small figure in
`repeat-results/plots/` if there is any signal). Pre-register the expected null in the writeup.

---

## 3. Design B — close the loop on the path axis (to actually back the claim)

If Design A is null (expected) and we choose to **back** the abstract claim rather than drop it,
the minimal honest change makes the history→path edge **real and deterministic** (the grammar /
validator cannot, since they bind path from `allowed_*` sets only). Three small edits, then a
clean deterministic A/B.

**Edit 1 — WRITE per-path operational history.** Extend the `Verdict` record (or add a runtime
`PathDecision` write) to carry `selected_relay` + `selected_path` + observed `rtt`/`loss`/`outcome`:
`runtime/contracts.py:232-240` (`Verdict` dataclass) + `runtime/kg_client.py:131-144`
(`write_verdict`). Today the runtime writes no path-tagged history at all.

**Edit 2 — READ a per-relay/per-path aggregation.** Add, alongside the per-SFC `reliability_summary`
(`analytics.py:81-96`), a per-`(relay,path)` aggregation returning prior `outcome` / `rtt` / `loss`,
and fold it into a **new prompt field** in `build_llm_input_object` (`prompt_builder.py:49-85`).

**Edit 3 — make the signal MOVE the decision (deterministic edge).** Add a history-aware
tie-break / penalty in `fallback_decision` (`validator.py:76-119`) that **downgrades a relay/path
with a poor logged verdict** — e.g. if the telemetry-default path has a recent rollback/escalate or
loss above threshold on record, prefer the alternative path when its envelope allows. This puts the
history→path edge on logic we control, not on 1.5B model attention.

**The A/B then becomes clean and non-null.** One env flag toggles the Edit-3 penalty
(`history-aware path penalty` on/off), with the **runtime-written per-path records present in both
arms**. IV = penalty on/off; DV = `selected_path` after a logged poor verdict on the default path;
expected = penalty-on flips to the alternative path, penalty-off does not. This isolates *"logged
per-path outcomes change the next path"* on a deterministic edge — the exact paper claim, honestly
demonstrated.

**Optional end-to-end realization.** Once Edit 3 exists, the effect can also be shown on the fabric
by extending E3: run scenario S twice — first episode logs a poor verdict on the chosen path, the
second episode (penalty-on) selects the other path *because of* that logged history — but this
requires plumbing `selected_path` through `e3_compare`/`e3_measure` (currently SFC-only +
runtime-reroute-owned path), so keep it as a follow-on, not the primary.

---

## 4. Recommendation — the decision gate

```
Run Design A  (cheap, no testbed, ~minutes)
   │
   ├─ FLIP toward history-favored path on ≥1 pivotal probe  → claim holds as-is; report it, keep the sentence.
   │
   └─ NULL (expected)  → decide:
        ├─ Build Design B (3 small edits) → deterministic history→path edge → back the claim honestly, OR
        └─ DROP the abstract/intro sentence (edits-doc P0 #2 / P2 #10) and state the loop "functions, does not yet steer path".
```

**Recommendation: run Design A first.** It is minutes of work, needs no testbed, and converts a
hand-wave into evidence. The honest expected result is null — at which point Design B is the only
way to legitimately keep the claim, and is a *real contribution* (per-path closed-loop steering)
rather than a relabeling. Do **not** report Design A's null as if history works, and do **not**
substitute the per-SFC `runtime_feedback` signal for the per-path claim — that would be circular
(it tests history-aware *SFC* selection, not *path* selection).

---

## 5. Risks / circularity (read before running)

- **Fallback dominance → A == B.** Any run that uses `--fallback-only` or validation-fails into the
  fallback reads **zero** history. Force the LLM path; assert `parsed_json` + `valid`.
- **Grammar independence.** Constrained decoding binds path from `allowed_paths`/`allowed_relays`
  only — it cannot emit a history-driven path. So Design A tests *model attention*, not capability;
  Design B is required for a guaranteed edge.
- **Per-SFC ≠ per-path.** `runtime_feedback` is the only live runtime→planner loop but is per-SFC
  reliability. Using it to claim *path* steering is mislabeling.
- **Static-source confound.** `kg_rag_historical_context` (CSV) and `historical_path_decisions_from_kg`
  (seed) are static and present in both arms unless explicitly toggled; the CSV's `selected_path` is
  *inferred from policy_type*, not a logged path. Toggle deliberately and document what each arm
  contains.
- **Empty-history trap.** If `--results-csv` resolves to a missing file and no Verdicts are logged,
  both arms already carry empty history → trivial null. Verify the populated arm is actually
  populated (`--save-input`).
- **Duplicate trees.** `llm_orchestrator/` is mirrored under `.../netprompt_llm_runtime_impl/`;
  confirm edits land in the copy `repro/`/`gpu-node.env` actually runs (`$NETPROMPT_ROOT`).

---

## 6. Outputs & repro

- **Driver to build:** `repro/hist_ab.sh` + a scorer (Design A); the three edits + a penalty-toggle
  driver (Design B).
- **Results:** `docs/experiments/repeat-results/historical_path_ab.csv` (+ `plots/` figure if
  signal). Plans/designs stay in `docs/experiments/`.
- **Controls:** the §0 controls from [experiment-design.md](experiment-design.md) (seed + verify
  KG, `source gpu-node.env`, pin adapter + base revision, explicit `--correlation-id`); score paths
  against the oracle where relevant; never import the draft's numbers.

---

*See also: [repeat-kronos-experiments-plan.md](repeat-kronos-experiments-plan.md) (build #8),
[kronos-draft-experiment-edits.md](kronos-draft-experiment-edits.md) (abstract P0 #2 / P2 #10),
[experiment-validity.md](experiment-validity.md) §8 ("the loop functions, does not improve
decisions"), [planner-lora-eval.md](../planner/planner-lora-eval.md) (0/4 telemetry).*
