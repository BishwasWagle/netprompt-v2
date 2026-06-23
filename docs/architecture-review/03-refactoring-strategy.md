# 3 · Refactoring Strategy

The governing constraint is **no functional change**. Every step below is
sequenced low-risk-first, each is independently shippable, and each has an explicit
test gate. The drop-in code for the starred (★) items is in
[04-production-code.md](04-production-code.md).

**Global test gate (run before and after *every* step):**

```bash
cd /home/cc/RuntimeManager && python -m pytest tests/unit -q     # must stay 212 passed
# node-tagged integration (test_m4/m5/m6/m7) run on network-node where applicable
```

---

## Phase 0 — Repository hygiene (zero code risk)

Pure deletions and `.gitignore` edits. No `runtime/` import path changes, so the
unit suite cannot move.

| Step | Action | Verify-before-delete |
|------|--------|----------------------|
| 0.1 ★ | Delete the duplicate archive trees `network/newcodes` and `network/milestone-II/necodes`; keep `network/milestone-II-latest`. Reword the two doc comments that name the deleted paths. | `diff -rq network/newcodes/netprompt-milestone-II network/milestone-II/necodes` → exit 0 |
| 0.2 ★ | `git rm` the 21 `*.bak`/`*.py.bak`/`*_phase3_backup.py`/`*.txt.bak` shadows; add the globs to `.gitignore`. | `grep -rIE '\.bak\|_phase3_backup' --include='*.py' runtime controller` → no source refs |
| 0.3 ★ | Delete the stray top-level `network/sfc_experiment.py`; add a doc pointer that the controller runs a *remote* copy. | `grep -rn 'import sfc_experiment\|from sfc_experiment' runtime controller` → none |
| 0.4 | Stop tracking model weights: confirm `*.safetensors`/`*.pt` belong in `.gitignore` and history-rewrite is out of scope; at minimum stop adding new ones. | `git ls-files '*.safetensors' '*.pt'` |

**Impact:** removes ~1.1 GB and ~131 redundant `.py` copies from the working tree;
shrinks future clones; de-noises every `grep`/IDE search. **Risk:** none — nothing
executable imports the deleted paths.

> Per repo convention: do **not** push and do **not** add a Claude co-author
> trailer; leave the commit for the maintainer. A single `chore:` commit per step
> keeps the diff reviewable.

---

## Phase 1 — Typing & vocabularies (pure structure, behavior-identical)

No control-flow change; these make the existing informal contracts checkable.

| Step | Action | Why |
|------|--------|-----|
| 1.1 ★ | Centralize `Verdict.outcome` / switch-status / diagnosis-metric vocabularies as module string constants + `frozenset`s in `contracts.py`, mirroring the existing `PRIMARY/TUNE/TIER_OF` pattern. Reference them from `evaluator`, `runtime_manager`, `soak`, `pipeline`, `network_monitor`, `adapt`. | Kills the silent-typo class (Problem M1). Values stay byte-identical strings, so JSON persistence and `==` comparisons are unchanged. |
| 1.2 ★ | Unify the SLA-margin formula: one `dimension_margins` in `contracts.py`; `compute_flow_metrics` uses `min(dims.values())`; `adapt.py` imports it. | Single source of truth for core SLA semantics (Problem D2). |
| 1.3 | Promote `_EPS` to one definition (`contracts.py`), import in `adapt`/`pipeline`. | Removes the 3-way copy (Problem D6). |
| 1.4 ★ | Add `DeployerProto`, `MonitorProto`, `GateProto` (`typing.Protocol`) + narrow `TableStateCapable`/`Restartable` for optional node methods. Type `EvalContext`/`adapt()`/`RuntimeManager.__init__` params; correct `last_good` to `ConfigSnapshot \| dict \| None`. | Turns docstring contracts + `hasattr` guards into checkable types (Problems A1/A3). Runtime behavior unchanged — Protocols are structural. |

**Test gate:** unit suite green; optionally add `mypy runtime/` to CI to *lock in*
the new types (advisory, not blocking, at first).

---

## Phase 2 — Performance (hot-path cost, same observable behavior)

Each step is defaulted to reproduce today's exact behavior and is independently
revertable.

| Step | Action | Safety |
|------|--------|--------|
| 2.1 ★ | Parallelize the independent per-field pings in `_probe` via a small `ThreadPoolExecutor`; lift `ping_count`/`-W` into config. Serial fallback when `ping_workers <= 1`. | Same `(rtt,loss)` tuples, reassembled in `requirements` order; default config = byte-identical command. |
| 2.2 ★ | Warm-load the regen model before the soak loop; wrap `generate()` in a wall-clock deadline → `TimeoutError` caught at `proposer.py:43` (escalate-sooner). | Same generation body/output; only adds a bound. Fail-safe path already exists. |
| 2.3 | Collapse per-episode KG writes into one session/`execute_write`; drop the duplicate pre-evaluate `write_baseline`. Add a `timeout=` to `pgrep` in `liveness()`. | Best-effort semantics preserved; idempotent MERGEs unchanged. |
| 2.4 | Guard `table_state()` so it is only materialized for REGEN candidates (TUNE/REROUTE gate checks don't read tables); batch `_reset_switch` deletes into one `run_cli`. | The gate's `_check_tune`/`_check_reroute` provably ignore `current_tables`; deletes already batch on install. |

**Test gate:** unit suite green. For 2.1, a test asserting ping *order* should set
`ping_workers=1`. For 2.2, assert `TimeoutError` maps to a rejected attempt.

---

## Phase 3 — Scalability & durability (small surface, higher value)

| Step | Action | Why |
|------|--------|-----|
| 3.1 | On `RuntimeManager.__init__`, attempt `KGClient.read_last_good(spec.sfc)` (reconstructing a `ConfigSnapshot`) before falling back to `deployer.capture()`; key `LastKnownGood` on `(sfc, target_field)`. | Survives a process restart with its rollback target intact (Problem S1). |
| 3.2 | Split `config.py` into `topology.py` (ports/MACs/`tc` templates/tree root) and a `ControlPolicy` dataclass (`BUDGET_N`, `HEADROOM_TAU`, `EPS_IMPROVE`, `SFC_ACTION_SPACE`, `KNOB_STEPS`) injected into `RuntimeManager`/`adapt`/`gate`. Keep module-level constants as the default instance. | One injection point for tunables; two configs can coexist without monkeypatching globals (Problem A2). Also fixes the stale `Run-time-Manager-2` default path. |
| 3.3 | Make the single-active-path / single-access-switch assumptions explicit (per-relay verdict seam in `_switch_status`, documented in the Protocols). | Turns implicit assumptions into visible seams before any multi-tenant work (Problem S2). |

**Test gate:** unit suite green; 3.1 needs a `read_last_good` round-trip test
against the KG fake.

---

## Sequencing rationale

```
Phase 0  ──▶  Phase 1  ──▶  Phase 2  ──▶  Phase 3
hygiene      typing        perf          durability
(no code)    (structure)   (defaults)    (new behavior path, opt-in)
 lowest risk ─────────────────────────────▶ highest value-per-risk
```

* **Phase 0 first** because it shrinks the surface everything else is reasoned
  over — and it cannot break tests.
* **Phase 1 before Phase 2/3** because the Protocols and constants are what make
  the later changes *checkable* (e.g. the `MonitorProto` makes the parallel-ping
  change provably surface-preserving).
* **Phase 2 defaults to current behavior**, so it can ship continuously and be
  measured under soak before any tunable is changed.
* **Phase 3 is the only phase that introduces a new code path** (KG-backed
  recovery, injected policy), so it is last and explicitly opt-in.

## What this review deliberately does **not** do

* It does not rewrite the control loop, the 6-stage ladder, or the adapt engine —
  they are correct and well-factored.
* It does not convert vocabularies to `enum.Enum` (would change JSON
  serialization and break bare-string `==`).
* It does not touch the documented design tradeoffs cleared in
  [02 appendix](02-critical-problems.md#appendix-what-is-not-a-problem).

---

## Key Takeaways

- **One hard constraint governs everything: no functional change, behind a global test gate.** Before and after *every* step you run `python -m pytest tests/unit -q`, which must stay 212 passed (with node-tagged `test_m4/m5/m6/m7` integration where applicable). Each step is independently shippable and revertable, and the drop-in code for starred (★) items lives in `04-production-code.md`.

- **Phase 0 (hygiene) goes first because it shrinks the surface and cannot break tests.** It is pure deletions and `.gitignore` edits with no `runtime/` import-path changes: removing duplicate archive trees, 21 `.bak`/backup shadows, and the stray `network/sfc_experiment.py`. Impact is ~1.1 GB and ~131 redundant `.py` copies removed, de-noising every grep/IDE search, at zero risk since nothing executable imports the deleted paths.

- **Phase 1 (typing) comes before perf/scalability because it makes later changes checkable.** It centralizes vocabularies and the SLA-margin formula in `contracts.py`, dedupes `_EPS`, and adds `typing.Protocol`s (`DeployerProto`, `MonitorProto`, `GateProto`). These are pure-structure changes — values stay byte-identical strings and Protocols are structural — so e.g. `MonitorProto` is what makes the later parallel-ping change provably surface-preserving.

- **Phase 2 (performance) defaults to today's exact behavior so it can ship and be soak-measured.** Steps like parallelizing `_probe` pings via a `ThreadPoolExecutor` keep a serial fallback (`ping_workers <= 1`) and reassemble the same `(rtt,loss)` tuples in `requirements` order, so the default config issues a byte-identical command. Each step is independently revertable and tuned only after measurement under soak.

- **Phase 3 (scalability/durability) is last because it is the only phase that introduces a new code path, and it is opt-in.** It adds KG-backed recovery (`read_last_good` before `deployer.capture()` on restart), an injectable `ControlPolicy` dataclass so two configs coexist without monkeypatching globals, and explicit single-active-path seams. Its test gate adds a `read_last_good` round-trip test against the KG fake.

- **The review deliberately leaves the working machinery and risky rewrites alone.** It does *not* rewrite the control loop, 6-stage ladder, or adapt engine (deemed correct and well-factored), does *not* convert vocabularies to `enum.Enum` (would change JSON serialization and break bare-string `==`), and does *not* touch the cleared tradeoffs in the `02` appendix.

- **Phase 0 and Phase 1 are now applied.** The hygiene cleanup and the typing/vocabulary centralization are already in place, so the remaining work begins at Phase 2 (performance) and Phase 3 (scalability/durability).
