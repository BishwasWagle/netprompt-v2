# M7 live Tier-2 regen demo — design note (paper-first)

**Status:** design for review — nothing built/run yet.
**Goal (DoD #3 / M7 exit §8):** on the live testbed, a *real* Tier-2 regen candidate
flows **propose → gate → apply → observe**, and the system **survives the LLM being
unavailable** (escalate-sooner). Stretch: a regen candidate **commits a recovered episode**.

---

## 1. Why the soak can't show this as-is

`soak.py:_spec()` builds its envelope with `legal_tiers=frozenset((TUNE, REROUTE))` —
no `REGEN`. So `--with-regen` loads the model but the engine never reaches Tier-2.
M6's 351 episodes also never left Tier-0. The demo therefore needs a **purpose-built
spec + fault**, not the steady-state soak. Best done as a dedicated integration test
(`tests/integration/test_m7_regen_live.py`), sibling to the M6 acceptance tests.

## 2. How Tier-2 is reached (the ladder)

`adapt.propose(diag, tier, ...)` ([adapt.py:130](runtime/adapt.py)):
`tier 0 → _propose_tune`, `tier 1 → _propose_reroute`, `tier 2 → regen_proposer`.
The engine escalates a tier only when `propose` returns `None`. So to land on Tier-2:

- **Tune exhausts:** `knob_ranges={}` ⇒ nothing to tune ⇒ `None`.
- **Reroute exhausts:** `legal_paths=frozenset((PRIMARY,))` ⇒ no backup to flip to ⇒ `None`.

Equivalently, the unit path uses `legal_tiers=frozenset((REGEN,))` to go straight to
Tier-2 (`test_regen.py:REGEN_ENV`). **Recommended for the demo:**
`legal_tiers=frozenset((TUNE, REROUTE, REGEN))`, `knob_ranges={}`,
`legal_paths=frozenset((PRIMARY,))` — proves the *full* ladder (tune→reroute→regen)
exhausts realistically into Tier-2, rather than hard-forcing regen-only.

## 3. Fault model — table-level, regen-recoverable

Regen rewrites BMv2 tables (`deployer` applies the candidate's `rules_text` via
`simple_switch_CLI`, [deployer.py:219](runtime/deployer.py)). So the fault must be a
**table misconfiguration** — not a link impairment (tune/reroute territory). The fault
also must leave a **single forward_table command** sufficient to repair it (within the
grammar) and the repaired state must satisfy the gate's L2 blackhole invariant.

Two candidates (inject out-of-band on s1 via `simple_switch_CLI`):

| Fault | Inject | Corrective regen command | Recovery likelihood |
|---|---|---|---|
| **A. Drone entry deleted** | `table_delete forward_table <handle for 00:..:0a>` | `table_add forward_table forward 00:00:00:00:00:0a => 10` | Higher — MAC↔port is a regular pattern (`…0X → port X`) the prompt's table dump reveals; the model re-adds the missing row |
| **B. Edge entry mis-ported** | `table_modify forward_table forward <edge handle> => 1` (wrong port) | `table_modify forward_table forward <edge handle> => 11` | Lower — model must know the *correct* port (11) is "primary"; needs a prompt hint |

**Recommended: Fault A** (delete drone d10's `…0a` entry). It blackholes one drone (gate
L2 `required_macs` flags it), the corrective action is a single `table_add` whose
key/port the model can read off the regular pattern in the prompt's `CURRENT TABLE
ENTRIES` dump, and the grammar already permits exactly that command.

## 4. Spec for the demo

```python
Envelope(max_latency_ms=70, min_bandwidth_mbps=5, max_loss_percent=20,
         legal_tiers=frozenset((TUNE, REROUTE, REGEN)),
         legal_paths=frozenset((PRIMARY,)), knob_ranges={})
DeploymentSpec(sfc="LowLatencyVideoSFC", binding=real_binding(...),
               envelope=env, correlation_id="M7", target_field="F1")
```

## 5. Test design — `tests/integration/test_m7_regen_live.py`

Prereqs (same gating as M6): resident `launch_network.py` up, `simple_switch_CLI`,
thrift 9090, host `d4`; **plus** the Coder model reachable on `cuda:1` and continuous
iperf traffic (so the blackholed flow actually reads as unmet). Run as `sudo` venv-python
with `gpu-node.env` sourced (tree + `NETPROMPT_REGEN_*` + KG).

- **A · propose→gate→apply→observe (real model)** — inject Fault A; build
  `RegenProposer(LocalHFClient(), table_state_fn=deployer.table_state, switch="s1")`;
  run one episode. Assert: an attempt with `candidate.kind == REGEN` exists in the trace
  with `gate_ok=True` and `applied=True` (the model produced a grammar-valid,
  gate-passing candidate that deployed live). This is the core DoD proof.
- **B · LLM-down fail-safe** — same fault, `RegenProposer(StubLLMClient([]))`. Assert the
  episode escalates (no commit), `deployer.applied == []` for REGEN, network untouched.
- **C · committed recovery (STRETCH)** — assert the post-deploy verdict is
  `healthy|marginal` and the `…0a` route is restored (`Verdict`/`LastKnownGood` in the
  KG). Gated/`xfail`-tolerant: depends on the 1.5B model emitting the *exact* corrective
  row; A+B are the must-pass.

Always restore s1's tables in a `finally` (re-add the deleted entry) so the test leaves
the testbed clean for re-runs.

## 6. Risks & mitigations

- **Model doesn't emit the corrective row** → A still passes if *any* gate-valid regen
  applies; only C (recovery) is at risk. Mitigation: the prompt already includes the live
  table dump + bounds + prior-rejected list; Fault A's regular pattern is the easiest
  signal. If C is flaky, iterate the prompt (name the missing MAC) — tracked separately,
  not a blocker for A/B.
- **Gate rejects non-restoring candidates** (correct, by design) → burns K-cap
  (`REGEN_MAX_REJECTS=3`) then escalates. For the demo, K-cap 3 is enough for Fault A;
  bump via `NETPROMPT`/proposer arg if needed.
- **Two models on the GPU** — orchestrator (cuda:0) is not loaded during this test; the
  Coder model (cuda:1) loads on first `generate()` (~adds seconds to the episode).
- **Determinism** — pinned revision + greedy ⇒ the model's candidate is reproducible, so
  A/B/C are stable across runs (a flaky C is a model-capability fact, not nondeterminism).

## 7. Assertions summary

| Test | Asserts | Must-pass? |
|---|---|---|
| A propose→…→observe | trace has REGEN attempt, `gate_ok`, `applied`; ≥1 model consult | ✅ yes |
| B LLM-down | escalates, no REGEN applied, network untouched | ✅ yes |
| C recovery (stretch) | verdict healthy/marginal, route restored, KG `LastKnownGood` | ◇ stretch |

## 7b. Live-run findings (2026-06-16)

Built and run on the real testbed. **A (propose→gate) and B (LLM-down fail-safe) PASS.**
**C (recovery) honestly XFAILs**, and the reason is a genuine design insight:

- A first cut deleted **d10**, but F1 only measures **d4** (`host_map["F1"][0]`), so there was no
  violation; combined with the recovery assertion reading the deployer's **stale cache** (the raw
  `_cli` delete bypasses the deployer), C was a **false green**. Fixed: target **d4** and read the
  **live** switch (`_live_routable` → `_refresh_tables`).
- With the corrected fault, the episode returns **`outcome='rollback'`, `tier_reached=0`, empty
  trace** — the engine recovers the deleted entry via the **cheaper rung-3 rollback** (restore
  last-known-good), so **Tier-2 regen never fires**. The network recovers, but not via regen.

**Implication:** Tier-2 regen has a *narrow* practical trigger. A deleted/edited entry reads as
"our config regressed" → rollback. A link impairment → tune/reroute. Regen only fires for a fault
that is exogenous (so no rollback), table-shaped (so reroute/tune can't fix it), and reaches Tier-2
— which a single injected delete is not. Demonstrating a regen-*driven* committed recovery needs a
purpose-built scenario in that niche (or temporarily disabling the rollback rung), and is left as
the open item for DoD #3. propose→gate→apply→observe + the fail-safe are proven; regen-driven
recovery is not yet.

## 8. Build order once approved

1. Add the spec + Fault A inject/restore helpers (reuse `simple_switch_CLI` via the runner).
2. Write `test_m7_regen_live.py` (A, B; C as xfail-tolerant).
3. Bring up the resident testbed, source `gpu-node.env`, run under sudo venv-python.
4. Record results; fold a pass into the M7 status + DoD checklist.

## 7c. DoD #3 RESOLVED — recovery path proven, plus a real gate bug (2026-06-16)

The recovery is now demonstrated, after uncovering a latent gate bug.

**The gate bug.** Building a scenario where Tier-2 actually *fires* (the fault baked into the
BASELINE so rung-3 rollback doesn't pre-empt it — evaluator stage 3 only rolls back on a regression
vs baseline) exposed that **every regen candidate was being gate-rejected on the live testbed** with
a false `L2: <drone> not routable (blackhole)`. Cause: `gate._simulate` keyed its working set by
`e.handle`, but **BMv2 handles are not unique across tables** — a `priority_table` entry collided
with a `forward_table` entry, collapsing the dict and dropping a drone's forward entry. M6 never hit
this (it stops at Tier-1); only a *passing* regen does. Fixed to key by `(table, handle)`; unit
regression in `test_gate.py::test_regen_l2_handles_collide_across_tables`.

**Recovery now proven.** `test_regen_recovers_via_path_live` (test **D**) deterministically deploys a
recovered episode: d4 (F1's ping target) is mis-ported to a wrong-but-valid port *before* the
baseline (so it's the steady state, not a regression), the ladder falls through to Tier-2, and a
`StubLLMClient` feeds the known-correct corrective row → propose → gate → apply → observe → **COMMIT
(marginal, tier 2)**, with d4's route restored on the live switch. This proves the Tier-2 recovery
**path/machinery** end-to-end (DoD #3's "deploys ≥1 recovered episode"). Whether the 1.5B Coder
*itself* emits that row is the separate, model-capability question — test **C** stays `xfail`.

**Final live status:** A (propose→gate) ✅ · B (LLM-down fail-safe) ✅ · C (real-model recovery)
xfail (model capability) · **D (recovery path) ✅ — DoD #3 met.**
