# 2 · Critical Problem Areas

Findings are grouped by the five requested dimensions. Every item carries
`file:line` evidence and a post-verification **corrected severity**. A handful of
plausible findings were adversarially refuted — they are documented, deliberate
design decisions — and are listed in the [appendix](#appendix-what-is-not-a-problem)
so you can see what was *cleared*, not just what was flagged.

## 2.0 Severity-ranked summary

| # | Severity | Dimension | Problem | Anchor |
|---|----------|-----------|---------|--------|
| 1 | **HIGH** | Duplicate / Maintainability | ~1.1 GB of forked, byte-identical `network/` archive trees committed to git | `network/{milestone-II,milestone-II-latest,newcodes,…/necodes}` |
| 2 | **HIGH** | Performance | `observe_window()` subprocess storm runs on *every* adapt attempt | `adapt.py:226`, `network_monitor.py:189` |
| 3 | **HIGH** | Performance / Scalability | Tier-2 regen does blocking in-process GPU inference in the loop, no timeout | `regen/llm_client.py:111`, `regen/proposer.py:38` |
| 4 | **HIGH** | Maintainability | Closed vocabularies (`Verdict.outcome`, switch-status, diagnosis-metric) are bare string literals duplicated across 6 files | `contracts.py:188`, `evaluator.py:49`, `soak.py:132` |
| 5 | MEDIUM | Duplicate | Core SLA-margin formula duplicated verbatim in two modules | `contracts.py:77`, `adapt.py:43` |
| 6 | MEDIUM | Scalability | Episode-critical rollback state (`last_good`) is in-memory only | `runtime_manager.py:32`, `deployer.py:144` |
| 7 | MEDIUM | Duplicate | 21 git-tracked backup shadow files (`.bak`, `_phase3_backup.py`) | across `network/` |
| 8 | MEDIUM | Duplicate | `network/sfc_experiment.py` is a 5th byte-identical dead copy | `network/sfc_experiment.py` |
| 9 | LOW | Architecture | Deployer/Monitor protocols are prose docstrings enforced by drifting `hasattr` guards | `contracts.py:224`, `adapt.py:213`, `runtime_manager.py:58,96` |
| 10 | LOW | Architecture | `config.py` is an ambient global singleton mixing mechanism + policy | `config.py`, `gate.py:59`, `adapt.py:30` |
| 11 | LOW | Performance | KG writes use a fresh session per statement; `write_baseline` fires twice per commit | `kg_client.py:131…`, `runtime_manager.py:75,88` |
| 12 | LOW | Maintainability | `_kg_write` swallows *all* exceptions into a counter | `runtime_manager.py:34` |
| — | (cleared) | — | shared `Budget`, `policy_type` sniff, Fake/real divergence | see appendix |

---

## 2.1 Bad architecture decisions

### A1 · Optional protocols are docstrings + duck-typing  · LOW
> Downgraded from "high": the loop is single-threaded and the guards are correct
> today, so this is a *quality/safety-net* gap, not a live defect.

The deployer/monitor contracts live only as a prose comment
(`contracts.py:224-249`) and are enforced by three differently-spelled runtime
probes:

* `hasattr(self.deployer, "table_state")` — `runtime_manager.py:58`
* `hasattr(deployer, "table_state")` — `adapt.py:213` (same contract, re-encoded)
* `getattr(self.monitor, "baseline_snapshot", None)` — `runtime_manager.py:96`

**Why it matters:** nothing a type-checker can see defines the surface every
consumer depends on. A typo in any guard silently changes behavior instead of
raising. **Fix:** a real `typing.Protocol` (`DeployerProto`, `MonitorProto`) plus
a narrow `TableStateCapable`/`Restartable` for the optional node-only methods.
See refactor in [04 §A1](04-production-code.md).

### A2 · `config.py` is an ambient global mixing mechanism and policy  · LOW
Eight+ modules `from runtime import config` and reach into one mutable namespace;
gate constructors even bind globals as default args (`gate.py:59-61`). Physical
mechanism (`THRIFT_PORTS`, `EDGE_MAC`, `TC_TEMPLATES`) sits beside control policy
(`BUDGET_N`, `HEADROOM_TAU`, `SFC_ACTION_SPACE`). **Why it matters:** no single
injection point for tunables; can't run two configs side-by-side without
monkeypatching globals. **Fix:** split topology/mechanism from a `ControlPolicy`
object injected into `RuntimeManager`/`adapt`/`gate`.

> Bonus, verified separately: `config.py:41` defaults `NODE_TREE_ROOT` to
> `/home/cc/Run-time-Manager-2/...`, but the actual symlink is
> `Run-time-Manager → /home/cc/RuntimeManager`. A **stale hardcoded path** — env
> override masks it today, but it is wrong as written.

### A3 · Collaborators typed as bare `object`; one annotation lies  · LOW
`EvalContext` types `deployer/monitor/gate` as `object` (`evaluator.py:29-36`);
`adapt()` and `RuntimeManager.__init__` leave them untyped. Worse,
`last_good: dict | None` (`evaluator.py:33`) is annotated `dict` but in production
holds a `ConfigSnapshot`. **Fix:** apply the A1 Protocols and correct `last_good`
to `ConfigSnapshot | dict | None`.

### A4 · `FakeMonitor` reaches into fixture privates  · LOW
`FakeMonitor.rebaseline` calls `self.model._flows(...)` and writes
`self.model.baseline` (`fakes.py:79-82`) — intimate knowledge of `ScenarioModel`
internals (`fixtures.py:55-67`). A fixture refactor silently desyncs the
rebaseline semantics the production `NetworkMonitor.rebaseline` is supposed to
mirror. **Fix:** give `ScenarioModel` a public `recompute_baseline(state)`.

---

## 2.2 Duplicate logic

### D1 · ~1.1 GB forked-duplicate archive trees  · HIGH
The single largest quality issue in the repository.

* `diff -rq network/newcodes/netprompt-milestone-II network/milestone-II/necodes`
  → **exit 0, zero differences** — a complete 199-file tree committed twice.
* Across the three archive trees, **184 `.py` files collapse to 53 distinct
  contents** (131 redundant copies).
* `du -sh`: 334M + 419M + 332M ≈ **1.1 GB** of largely duplicated archive in git;
  `.git` itself is 267 MB; **28 model-weight files** (`*.safetensors`/`*.pt`) were
  force-added past the `.gitignore` that lists them.
* The `llm_orchestrator` package exists in **six** copies, and they have
  *diverged*: `llm_runner.py` has three distinct contents across the top-level
  copies — a fix in one never reaches the others.

**Why it matters:** bloats clones, pollutes every `grep`/IDE search with dozens of
identical hits, and creates genuine ambiguity about which copy is live —
`config.py:36` has to spend a comment telling readers `newcodes/` is "a leftover."
**Fix:** delete the redundant trees, keeping only the `milestone-II-latest` copy
that `config.NODE_TREE_ROOT` already declares authoritative. No `runtime/` code
imports these trees. See [04 §D1](04-production-code.md).

### D2 · SLA-margin formula duplicated in two modules  · MEDIUM
The three-dimension normalized-margin computation is inlined in
`compute_flow_metrics` (`contracts.py:77-81`) **and** re-expressed in
`dimension_margins` (`adapt.py:43-50`). These define the system's *core SLA
semantics*. Change the formula in one place (new denominator, a 4th dimension) and
`diagnose()` silently disagrees with `FlowMetrics.met`/`margin`. **Fix:** one
shared `dimension_margins` helper in `contracts.py`; `compute_flow_metrics` sets
`margin = min(dims.values())`. Behavior-identical. See [04 §D2](04-production-code.md).

### D3 · 21 git-tracked backup shadow files  · MEDIUM
`*.bak`, `*.py.bak`, `*_phase3_backup.py`, `*.txt.bak` checked in next to live
source — version-control-by-filename inside a git repo. In one directory the same
old content is stored under *two different names* (`.bak` == `_phase3_backup.py`,
blob `f0db187`). **Fix:** `git rm` all 21; add patterns to `.gitignore`. History
already preserves them. See [04 §D3](04-production-code.md).

### D4 · Result-parse / KG-push scripts copy-pasted  · MEDIUM
33 `parse_*`/`push_*to_kg.py` files collapse to **9 distinct contents**;
`parse_llm_experiment_results.py` exists in 6 identical copies. The live
`controller/parse_results.py` is a 10th *fork* of the same iperf/RTT/loss scrape.
**Fix:** delete archive copies (D1); for the live path, factor the shared scrape
core into one importable module. See [04 §D4](04-production-code.md).

### D5 · `network/sfc_experiment.py` is a dead 5th copy  · MEDIUM
md5 `2275e80d…` is identical across 5 locations; nothing in `runtime/`/`controller`
imports it; the only callers run a *remote* copy on `network-node`. It is the
single-OVS-switch design, superseded by the 3-switch BMv2 fabric. **Fix:** delete
the stray top-level file; document that the controller SSHes a remote copy. See
[04 §D5](04-production-code.md).

### D6 · `_EPS = 1e-9` redeclared in three modules  · LOW
`contracts.py:23`, `adapt.py:22`, `pipeline.py:18` — same divide-by-zero floor,
part of the D2 numeric contract. **Fix:** one definition, imported.

---

## 2.3 Performance bottlenecks

### P1 · `observe_window()` subprocess storm per attempt  · HIGH
`observe_window()` is the most expensive inner-loop op, and it re-runs after
*every* applied candidate (`adapt.py:226`). One window = `M` probes
(`network_monitor.py:189`, M=5), each probe issues one `mnexec … ping` subprocess
**per field** serially (`network_monitor.py:171, 105`). With 2 fields that is
~10 blocking `ping -c 2 -W 1` subprocesses per window, ~6 windows per episode.
**Intrinsic to K-of-M semantics**, so the win is structural, not algorithmic:
issue the independent per-field pings **concurrently** and make `ping_count`/`-W`
configurable. See [04 §P1](04-production-code.md).

### P2 · Blocking, un-timed LLM inference in the loop  · HIGH
When `--with-regen` is on, each Tier-2 escalation runs up to 3 constrained-decoding
generations of 64 tokens, all blocking the episode under `torch.no_grad()`
(`llm_client.py:111-125`), and the **first call also pays the full model load**
(`_ensure_loaded`, `llm_client.py:84-98`). There is **no wall-clock timeout** — a
hung GPU call stalls the episode indefinitely, partially defeating the §7.4
fail-safe. **Fix:** warm-load the model before the soak loop; wrap `generate()` in
a deadline (worker thread + `future.result(timeout)`) that surfaces as a rejected
attempt → escalate. See [04 §P3](04-production-code.md).

### P3 · Per-statement KG sessions; double baseline write  · LOW
A non-rejected episode issues ~6–8 separate Neo4j round-trips
(`runtime_manager.py:74,77,79,81,87,90`), each opening a *fresh*
`with self.driver.session()` (`kg_client.py:131…`), against a remote `bolt://`
endpoint. `write_baseline` fires **twice per committed episode**
(`runtime_manager.py:75` and `:88`). **Fix:** collapse per-episode writes into one
session / `execute_write`; drop the redundant pre-evaluate baseline write.

### P4 · Other hot-path costs  · LOW (batch as one cleanup)
| Item | Evidence | Fix |
|------|----------|-----|
| `qdiscs()` read twice on the first window | `network_monitor.py:266,272` | reuse the just-captured baseline read |
| `table_state()` deep-copied every candidate, even for TUNE | `adapt.py:213`, `deployer.py:154` | only materialize for REGEN (TUNE/REROUTE don't read tables) |
| `_reset_switch` = 3 dumps + N per-entry delete round-trips | `deployer.py:323,410` | batch deletes into one `run_cli` (installs already batch) |
| `liveness()` un-timed `pgrep` per switch per window | `system_monitor.py:21` | add `timeout=` mirroring `_root_tc` |

---

## 2.4 Scalability risks

### S1 · In-memory-only rollback state  · MEDIUM
`last_good` (`runtime_manager.py:32`), the deployer's `_tables/_qos/_path`
(`deployer.py:144`), the `tried` set and `Budget` (`adapt.py:182`) all live in RAM
in one long-lived process. A restart (OOM, redeploy, node reboot) loses the
rung-3 rollback target — and `kg_client.read_last_good` exists but is documented
as **"not consumed by the live loop today"** (`kg_client.py:105`). **Fix:** make
`last_good` reconstructable from the KG on startup; key `LastKnownGood` on
`(sfc, target_field)`, not `sfc` alone. See [03](03-refactoring-strategy.md).

### S2 · Single-everything assumptions  · LOW (by design today, document the seams)
One episode at a time per process; single target field / single active path /
single access-switch `s1` baked into topology constants and the monitor's
`_switch_status` (`network_monitor.py:215-221`, which even comments that a
multi-field-per-relay topology "would need a per-relay verdict"); `cuda:1` pinned
with silent fallback. These are appropriate for the current testbed but are the
load-bearing assumptions to revisit before any multi-tenant/HA deployment.
**Action:** not a code change now — capture as explicit seams (Protocols + config)
so the assumptions are visible, not implicit.

---

## 2.5 Maintainability issues

### M1 · Magic-string vocabularies, no single source of truth  · HIGH
The six `Verdict.outcome` values, four switch statuses, and three diagnosis
metrics are re-typed as raw strings in 6+ files (`contracts.py:188` comment-only,
`evaluator.py:49`, `runtime_manager.py:49,83`, `soak.py:132`, `pipeline.py:185`,
`network_monitor.py:65`, `adapt.py:71`). A typo in any one literal is **silent**.
The codebase *already* centralizes the parallel action vocabulary
(`PRIMARY/TUNE/…` at `contracts.py:15`) — these three just weren't given the same
treatment. **Fix:** module string constants + closing `frozenset`s, mirroring the
existing `TIER_OF` pattern (deliberately **not** `enum.Enum` — values are compared
by bare `==`, JSON-persisted, and round-tripped through `jsonable()`). See
[04 §M1](04-production-code.md).

### M2 · `_kg_write` swallows all exceptions  · LOW
`except Exception: self.kg_write_failures += 1` (`runtime_manager.py:34-43`)
conflates a transient Neo4j blip (the intended case) with a programming error
(e.g. a bad payload), which then vanishes silently. **Fix:** keep best-effort, but
log the exception (and ideally narrow to driver/transient errors) so real bugs are
visible.

### M3 · `Candidate` carries untyped positional tuple params  · LOW
`Candidate{kind, params: tuple}` (`contracts.py:42`) means every consumer unpacks
positionally and kind-dependently (`knob, value = cand.params`, `(path,) = …`).
**Fix:** keep the frozen/hashable shape, but document/validate per-kind arity; a
helper constructor (`Candidate.tune(knob, v)`) removes positional guesswork.

### M4 · `FakeMonitor` is missing `capture_baseline`  · LOW
The real `NetworkMonitor` exposes `capture_baseline`/`baseline_snapshot`; the fake
implements only `observe_window`/`rebaseline` (`fakes.py:68-77`). The
`MonitorProto` from A1 should pin the full surface so the gap surfaces at type-check
time.

### M5 · Substring error detection in the deployer  · LOW
REGEN/dry-install success is decided by scanning CLI stdout for
`("invalid","error","exception",…)` (`deployer.py:231,313`). Pragmatic — BMv2
`simple_switch_CLI` exits 0 on per-line failure — but brittle to wording changes.
**Fix:** centralize the marker list as one constant with a comment tying it to the
verified CLI output; longer-term prefer structured exit signals where the CLI
offers them.

---

## Appendix · What is NOT a problem

The verification pass refuted these plausible-sounding claims. They are
**deliberate, documented design** — flagging them would be wrong:

1. **Shared mutable `Budget` across rungs** — *not* an undocumented coupling; it is
   the explicitly designed, correctness-critical mechanism that makes the retry
   budget per-episode (`adapt.py` docstring, design §7.6). A fresh `Budget` is
   constructed per `run_episode`. Correct as is.
2. **`policy_type` string-substring sniff in the deployer** (`deployer.py:194`) —
   `policy_type` is a controlled enum-like field from the planner, not free text;
   the `"backup" in …` check is the documented mechanism for selecting the active
   path (design §10.1). Correct as is.
3. **`FakeDeployer` accepts a REGEN tuple shape the real deployer rejects**
   (`fakes.py:31-42`) — a *documented* test-double affordance for off-node scenario
   models; the engine treats snapshots as opaque so it never observes the
   difference. It is a (minor) fake/real divergence worth noting under A4/M4, but
   not the defect the raw shape-mismatch made it look like.

The lesson: this codebase earns the benefit of the doubt. Its docstrings encode
real design rationale — read them before "fixing" something that looks odd.

---

## Key Takeaways

- **Four HIGH-severity problems dominate, all confirmed after verification.** They are: ~1.1 GB of forked byte-identical `network/` archive trees in git (D1), the `observe_window()` subprocess storm per adapt attempt (P1), blocking un-timed in-loop LLM inference (P2), and magic-string vocabularies duplicated across 6+ files (M1). Everything else lands at MEDIUM or LOW.

- **The 1.1 GB duplicate archive is the single largest quality issue.** `diff -rq` shows a 199-file tree committed twice with zero differences; 184 `.py` files collapse to 53 distinct contents, three trees total 334M + 419M + 332M, `.git` is 267 MB, and 28 model-weight files were force-added past `.gitignore`. The `llm_orchestrator` package exists in six diverged copies, so a fix in one never reaches the others. No `runtime/` code imports these trees, so the fix is to delete all but the authoritative `milestone-II-latest` copy.

- **Two performance HIGHs stall the live episode loop.** `observe_window()` re-runs after every applied candidate (`adapt.py:226`), firing ~10 blocking `ping` subprocesses per window across ~6 windows per episode — fixable by running per-field pings concurrently. With `--with-regen`, Tier-2 escalation runs up to 3 blocking 64-token GPU generations under `torch.no_grad()` (`llm_client.py:111-125`) with no wall-clock timeout, so a hung GPU call stalls the episode indefinitely and partially defeats the §7.4 fail-safe.

- **Closed vocabularies are unguarded raw strings.** The six `Verdict.outcome` values, four switch statuses, and three diagnosis metrics are re-typed as literals in 6+ files (`contracts.py:188`, `evaluator.py:49`, `soak.py:132`, and more), where a single typo fails silently. The codebase already centralizes the parallel action vocabulary (`PRIMARY/TUNE/…` via `TIER_OF`), so the fix mirrors that pattern with string constants plus `frozenset`s — deliberately *not* `enum.Enum`, since values are compared by bare `==` and JSON-persisted.

- **Severity is assigned after verification, not on first impression.** Several plausible findings were downgraded with explicit reasoning — e.g. the docstring/duck-typing protocols (A1) dropped from "high" to LOW because the loop is single-threaded and the `hasattr` guards are correct today, making it a safety-net gap rather than a live defect. Every item carries `file:line` evidence and a corrected severity.

- **The appendix documents what was *cleared*, not just what was flagged.** Three plausible claims were adversarially refuted as deliberate, documented design: the shared mutable `Budget` across rungs (the correctness-critical per-episode retry mechanism, freshly constructed per `run_episode`), the `policy_type` substring sniff in the deployer (a controlled enum-like field, not free text — design §10.1), and the `FakeDeployer`/real REGEN-shape divergence (a documented test-double affordance the engine never observes). The stated lesson: this codebase's docstrings encode real rationale, so read them before "fixing" something that looks odd.
