# Post-Deploy Evaluator (`runtime/evaluator.py`)

**Subsystem:** Runtime Manager (inner loop)
**One-liner:** Attributes a post-deploy `MonitorReport` through a 6-stage ladder and emits a terminal `Verdict` (+ optional `EscalationTicket`).

## Responsibility
`evaluate()` owns post-deploy **attribution and the commit ladder**: deciding
whether the system is sound, whether the SLA is met, whether *our* change caused
a regression (rollback) or the environment did (adapt), and finally whether to
commit healthy/marginal or escalate. It decides *when* to adapt; it does **not**
own the adapt mechanism itself (it delegates to `adapt`, §7), live config
mutation (the deployer), observation (the monitor), or KG persistence (the RM
writes the returned `Verdict`/`ticket`).

## Files
- `runtime/evaluator.py` — `evaluate`, `EvalContext`, `EvalResult`, `commit_outcome`

## Interface
```python
@dataclass
class EvalContext:
    deployer: object; monitor: object; gate: object; budget: Budget
    last_good: dict | None = None
    active_capacity_ok: object = None      # callable(Candidate) -> bool, or None
    current_tables: dict | None = None
    regen_proposer: object = None          # Tier-2 seam (M7); None = stubbed
    timestamp: str = ""

@dataclass
class EvalResult:
    verdict: Verdict
    ticket: EscalationTicket | None = None

def commit_outcome(headroom: float, tier_reached: int) -> str: ...
def evaluate(spec: DeploymentSpec, report: MonitorReport, ctx: EvalContext) -> EvalResult: ...
```

## How it works
The six stages (design §6), in order:
1. **system sound?** `not r.system_sound` → `system_fault` verdict (tiered fault fix is the RM's job).
2. **SLA met, sustained?** `r.target_sla_met` → jump to the commit path (stage 5).
3. **caused-it (rung-3 rollback)?** `regressed = vs_baseline[target] < -REGRESSION_EPS` **and** `not r.exogenous_shift` → `deployer.rollback(last_good)` → `rollback` verdict.
4. **in-envelope fix left?** else `adapt(...)`; `not res.success` → `_escalate` ("wrong SFC"). Success replaces `r` with the goal report.
5. **displaced harm?** on the commit path, `r.displaced_harm` re-enters `adapt` (Option B); failure → escalate "no harm-free config".
6. **headroom commit.** `commit_outcome(r.headroom, tier_reached)` → `healthy` if `headroom >= HEADROOM_TAU`, else `marginal`.

Invariants: every terminal path returns exactly one `Verdict`; escalations also
carry an `EscalationTicket` (observed metrics + envelope + trace + reason).

## Gotchas & lessons
- **`REGRESSION_EPS` (0.02) deadband on stage 3.** The real monitor jitters
  `vs_baseline` ~1e-7 on idle flows; without the deadband, measurement noise
  triggered spurious rollbacks (node-verified). Conservative by design.
- **Exogenous-shift guard.** A regression that coincides with an environment
  change (§5.7) is attributed *outward* — skip rollback, fall through to adapt.
- **`commit_outcome` forces `marginal` whenever `tier_reached >= 2`** (§7.5):
  reaching the goal only via Tier-2 LLM rule-synthesis is inherently precarious,
  so it is flagged regardless of headroom.
- The §13b.A deadband on the headroom/`improves` paths is still owed (design backlog).

## Usage
In-loop only — not a CLI. Invoked by `RuntimeManager.run_episode`.

## See also
- `../runtime-manager-design.md` §6 (Post-Deploy Evaluator), §1b (System Workflow), §5.7 (exogenous shift), §7.5 (on exit)
- `../usage.md` §2 (Runtime Manager)
- `./runtime-manager.md`, `./adaptation-engine.md`
