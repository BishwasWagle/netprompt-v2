# Unified Adaptation Engine (`runtime/adapt.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** One cost-ordered tier ladder (tune → reroute → regen) that hill-climbs to `GOAL = target_sla_met AND no displaced_harm`, or escalates.

## Responsibility
`adapt()` owns in-envelope recovery: diagnosing the worst violation, proposing
one candidate per tier, gating it, applying it, re-observing, and keeping it only
if it dominates + improves. One engine serves both callers — rung-4 (target
failing) and stage-5 (neighbour harmed) — chasing one goal. It does **not** own
attribution/when-to-enter (the evaluator, §6), live mutation (the deployer), the
gate's legality rules (§11), or the Tier-2 LLM itself (it calls an injected
`regen_proposer` seam, default `None`).

## Files
- `runtime/adapt.py` — `Budget`, `diagnose`, `propose`, `dominates`, `improves`, `adapt`
- `runtime/regen/proposer.py` — `RegenProposer`, the Tier-2 seam (`regen_proposer` callable)

## Interface
```python
class Budget:
    def __init__(self, n: int = config.BUDGET_N): ...
    def remaining(self) -> int: ...
    def spend(self, k: int = 1) -> None: ...

def diagnose(report: MonitorReport) -> Diagnosis | None: ...
def propose(diag, tier, env, state, exclude, regen_proposer=None): ...
def dominates(post: MonitorReport, pre: MonitorReport) -> bool: ...
def improves(post: MonitorReport, pre: MonitorReport) -> bool: ...
def adapt(spec, report, budget, deployer, monitor, gate,
          active_capacity_ok=None, current_tables=None,
          regen_proposer=None) -> AdaptResult: ...

# Tier-2 seam:
class RegenProposer:
    def __init__(self, client, table_state_fn, switch="s1",
                 max_rejects=config.REGEN_MAX_REJECTS): ...
    def __call__(self, diag, env, state, exclude) -> Candidate | None: ...
```

## How it works
- **Tiers (§7.1):** 0 tune (deterministic knob step), 1 reroute (deterministic primary↔backup flip), 2 regen (LLM — stubbed by default). `propose` returns `None` when a tier is exhausted → escalate cost tier; `None` at tier 2 → fail.
- **`diagnose`** picks the worst violation; **target outranks harm**. **`propose`** turns it into one directionally-correct candidate (knob-effect model, §7.3); the guard + re-observe catch mispredictions.
- **Guards (§7.2):** `dominates` never accepts a regression (target failing, or harm count rising); `improves` requires strict progress on some axis. Goal → `AdaptResult(success)`; partial gain → keep + hill-climb; else `deployer.rollback(pre_cfg)`.
- **Shared episode budget:** `budget.spend(1)` only on **applied** attempts; gate-rejects and capacity-skips are bounded instead by the finite `tried` set (termination). Loop exits on `budget.remaining() == 0`.
- Reroute onto an `inferred` (idle) path runs the one sanctioned active capacity check before applying.

## Gotchas & lessons
- **`regen_proposer` defaults to `None` → escalate-sooner.** This is the §7.4 fail-safe: an unavailable/hanging LLM degrades capability, not safety. `RegenProposer` itself catches any `client.generate` exception and treats it as a rejected attempt.
- **The `improves` harm-relief subtlety:** a headroom rise that coincides with the target's *own* margin dropping is **intended** — harm relief gives back the target's grab to lift a neighbour. Gating headroom on target-not-worse breaks the hill-climb (caught by the M6 contention scenario).
- `propose` needs the **current applied config** (`deployer.state`) — `MonitorReport` carries no config state. Grids are anchored at `lo`, inclusive of `hi`, index-based to avoid float drift.
- Tier 2's K-cap is computed statelessly from the `exclude` set, so one proposer instance serves many episodes.

## Usage
In-loop only — not a CLI. Invoked by the evaluator (rung 4 and stage 5).

## See also
- `../runtime-manager-design.md` §7.1 (tier ladder), §7.2 (engine + guards), §7.3 (diagnose/propose), §7.4 (why safe), §7.6 (budget boundary)
- `../usage.md` §2 (Runtime Manager), §4 (Tier-2 regen LLM)
- `./runtime-manager.md`, `./evaluator.md`
