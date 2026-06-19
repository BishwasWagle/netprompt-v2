# Runtime Manager (`runtime/runtime_manager.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** Orchestrates one deployment's episode — gate → deploy → observe → evaluate → adapt — and records the verdict and snapshots to the KG.

## Responsibility
`RuntimeManager.run_episode` owns the episode lifecycle: it runs the pre-deploy
binding gate, hands a fresh per-episode `Budget` and an `EvalContext` to the
evaluator, and on a commit promotes last-known-good + re-baselines (§7.6). It
also owns best-effort KG persistence of the episode's records. It does **not**
own attribution logic (that is the evaluator, §6), the tier ladder or guards
(the adapt engine, §7), live re-install mechanics (the deployer, §10), or
observation/hysteresis (the monitor, §5). It is **mostly deterministic** — the
only non-determinism is the optional Tier-2 regen LLM, which defaults to `None`
(stubbed → escalate-sooner).

## Files
- `runtime/runtime_manager.py` — the `RuntimeManager` class / episode loop
- `runtime/tools/run_episode.py` — assembles the real node stack and drives one episode (CLI)
- `runtime/tools/run_from_planner.py` — drives an episode from a planner artifact (`--deploy`)

## Interface
```python
class RuntimeManager:
    def __init__(self, deployer, monitor, gate,
                 budget_n: int = config.BUDGET_N,
                 active_capacity_ok=None, current_tables: dict | None = None,
                 regen_proposer=None, kg=None): ...

    def run_episode(self, spec: DeploymentSpec, timestamp: str = "") -> EvalResult: ...
```
Construction captures `self.last_good = deployer.capture()` (pre-deploy state is
the first rung-3 rollback target).

## How it works
- Pre-deploy gate: `gate.check_binding(spec)`; a refusal returns a `rejected`
  `Verdict` (also written to the KG) and never touches the network.
- Refreshes `current_tables` from `deployer.table_state()` each episode so the
  Tier-2 gate L2 sim sees the **live** switch tables, not a construction-time
  value [M7 #6] — unless a static override was supplied (tests).
- Builds `EvalContext` with a **fresh `Budget(self.budget_n)` per episode**,
  observes one window (`monitor.observe_window()`), writes switch status +
  baseline snapshot, then calls `evaluate(spec, report, ctx)`.
- Writes the resulting `Verdict` (and `EscalationTicket`, if any) to the KG.
- Invariant — **any commit ends the episode** (§7.6): on `healthy`/`marginal`
  it re-captures `last_good`, calls `monitor.rebaseline()`, and writes the new
  last-good + re-baselined snapshot (same KG key).

## Gotchas & lessons
- **KG writes are best-effort.** `_kg_write` swallows and counts exceptions
  (`kg_write_failures`) — a transient Neo4j failure must never abort an episode
  (critical for the M6 soak; recording is not the loop's job).
- `_baseline_snapshot` returns `None` when the monitor exposes no
  `baseline_snapshot` (the real `NetworkMonitor` does; `FakeMonitor` doesn't),
  and the write is skipped — not an error.
- The budget is per-episode *by construction* (a new `Budget` each call), which
  is what makes the §7.4 budget-bounded escalation argument hold.

## Usage
- Scenario-driven: `sudo -E python -m runtime.tools.run_episode --scenario relay_failure --monitor real` (`--monitor model` for the instant scenario monitor).
- Planner-driven (live): `python -m runtime.tools.run_from_planner --artifact <path> --target-field Field_2 --deploy`.
- Repeated episodes + injected kills: `sudo -E python -m runtime.tools.soak --minutes 60 --kill-every 20 --kill s3` (add `--with-regen` for Tier-2).

## See also
- `../runtime-manager-design.md` §1b (System Workflow), §6 (Evaluator), §7.6 (Episode + budget boundary)
- `../usage.md` §2 (Runtime Manager — the inner-loop control system)
- `./evaluator.md`, `./adaptation-engine.md`
