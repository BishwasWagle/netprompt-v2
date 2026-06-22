"""The unified adaptation engine (design §7).

One engine, one goal — GOAL(report) = target_sla_met AND no displaced harm —
entered either from rung 4 (target failing) or stage 5 (neighbor harmed).
Cost-ordered tier ladder: 0 tune (deterministic), 1 reroute (deterministic),
2 regen (LLM — stubbed until M7; returning None makes the engine escalate
sooner, which is exactly the fail-safe property of §7.4).

Safety mechanics: domination guard (never accept a regression), hill-climb
(keep strict progress), per-attempt rollback, finite quantized candidate
grids + the `tried` set (termination), shared episode budget.
"""
from __future__ import annotations

from runtime import config
from runtime.contracts import (
    BACKUP, LATENCY, LOSS, PRIMARY, REROUTE, TARGET, THROUGHPUT, TUNE, _EPS,
    AdaptResult, AttemptRecord, Candidate, Diagnosis, DeploymentSpec,
    Envelope, MonitorReport, dimension_margins, goal,
)


class Budget:
    """Episode-scoped retry budget (design §7.6). Spent only on APPLIED
    attempts; gate-rejects and capacity-skips are bounded by the finite
    `tried` set instead (design §11)."""

    def __init__(self, n: int = config.BUDGET_N):
        self.n = n
        self.spent = 0

    def remaining(self) -> int:
        return self.n - self.spent

    def spend(self, k: int = 1) -> None:
        self.spent += k


# ---------------- diagnose ----------------
# dimension_margins now lives in contracts.py (one definition shared with
# compute_flow_metrics) — imported above; diagnose() is otherwise unchanged.


def diagnose(report: MonitorReport) -> Diagnosis | None:
    """Worst current violation; target outranks harm (design §7.3)."""
    if not report.target_sla_met:
        dims = dimension_margins(report.target)
        metric = min(dims, key=dims.get)
        return Diagnosis(TARGET, metric, -dims[metric])
    if report.displaced_harm:
        harmed = [f for f in report.non_target if f.field_id in report.displaced_harm]
        worst = min(harmed, key=lambda f: f.margin)
        dims = dimension_margins(worst)
        metric = min(dims, key=dims.get)
        return Diagnosis(worst.field_id, metric, -dims[metric])
    return None


# ---------------- propose ----------------

# Target violation -> which knob, which direction (knob-effect model §7.3).
_KNOB_FOR_TARGET = {
    LATENCY: ("pfifo_limit", "down"),
    THROUGHPUT: ("tbf_rate_mbit", "up"),
    LOSS: ("pfifo_limit", "up"),
}
# Harm relief acts only on the target's own grab (§7.3).
_HARM_KNOB = ("tbf_rate_mbit", "down")


def _grid(lo: float, hi: float, step: float) -> list:
    """Quantized values anchored at lo, INCLUSIVE of hi (design §7.3 impl note).
    Index-based (no repeated += to avoid float drift); appends hi when it isn't
    lo + k*step so the top of the knob range is always reachable."""
    n = int((hi - lo) / step + _EPS)
    vals = [lo + i * step for i in range(n + 1)]
    if vals[-1] < hi - _EPS:
        vals.append(hi)
    return vals


def _propose_tune(diag: Diagnosis, env: Envelope, state: dict, exclude: set):
    if TUNE not in env.legal_tiers:
        return None
    knob, direction = _HARM_KNOB if diag.who != TARGET else _KNOB_FOR_TARGET[diag.metric]
    if knob not in env.knob_ranges:
        return None                      # no lever for this diagnosis
    lo, hi = env.knob_ranges[knob]
    step = config.KNOB_STEPS.get(knob) or max((hi - lo) / 5, 1)
    grid = _grid(lo, hi, step)
    current = state["knobs"].get(knob)
    if current is None:
        # No recorded knob value: assume the extreme the direction steps AWAY
        # from, so the first proposal moves the RIGHT way (harm relief steps down
        # from the cap; a target fix steps up from the floor) — NOT to the
        # opposite end, which would worsen the very thing we're fixing.
        current = hi if direction == "down" else lo
    if direction == "down":              # most-conservative step first
        options = [v for v in reversed(grid) if v < current]
    else:
        options = [v for v in grid if v > current]
    for v in options:
        cand = Candidate(TUNE, (knob, v))
        if cand not in exclude:
            return cand
    return None


def _propose_reroute(env: Envelope, state: dict, exclude: set):
    if REROUTE not in env.legal_tiers:
        return None
    other = BACKUP if state["path"] == PRIMARY else PRIMARY
    if other not in env.legal_paths:
        return None
    cand = Candidate(REROUTE, (other,))
    return None if cand in exclude else cand


def propose(diag: Diagnosis, tier: int, env: Envelope, state: dict, exclude: set,
            regen_proposer=None):
    """One candidate at the given tier, or None when the tier is exhausted.
    `state` is the deployer's current applied config (path + knob values) —
    see the §7.3 implementation note.

    regen_proposer: optional callable(diag, env, state, exclude) ->
    Candidate(REGEN, (switch, rules_text)) | None — the Tier-2 seam the LLM
    plumbing (M7) plugs into. The gate remains the authority on whatever it
    returns; None (the default) keeps Tier 2 stubbed (fail-safe, §7.4)."""
    if tier == 0:
        return _propose_tune(diag, env, state, exclude)
    if tier == 1:
        return _propose_reroute(env, state, exclude)
    if regen_proposer is not None:
        return regen_proposer(diag, env, state, exclude)
    return None


# ---------------- guards ----------------

def dominates(post: MonitorReport, pre: MonitorReport) -> bool:
    """Never accept a regression (design §7.2/§7.4)."""
    if pre.target_sla_met and not post.target_sla_met:
        return False
    if len(post.displaced_harm) > len(pre.displaced_harm):
        return False
    return True


def improves(post: MonitorReport, pre: MonitorReport) -> bool:
    """Strict progress on some axis (design §7.2).

    NB: a headroom rise that coincides with the target's own margin dropping is
    INTENDED, not a bug — harm relief (§7.3) deliberately gives back the target's
    grab (lowering its margin) to lift a harmed neighbor. It only fires when
    displaced_harm is present, so it can't 'improve' by trading target margin for
    an already-satisfied neighbour. (Gating headroom on target-not-worse breaks
    the harm-relief hill-climb — caught by the M6 contention scenario.)"""
    return ((post.target_sla_met and not pre.target_sla_met)
            or len(post.displaced_harm) < len(pre.displaced_harm)
            or post.headroom > pre.headroom + config.EPS_IMPROVE)


# ---------------- engine ----------------

def adapt(spec: DeploymentSpec, report: MonitorReport, budget: Budget,
          deployer, monitor, gate,
          active_capacity_ok=None, current_tables=None,
          regen_proposer=None) -> AdaptResult:
    """The §7.2 engine. Success hands a goal report to the evaluator's commit
    path; failure leaves the best-achieved dominating config applied (§7.5)."""
    capacity_ok = active_capacity_ok or (lambda cand: True)
    env = spec.envelope
    tried: set = set()
    tier = 0
    cur = report
    trace: list = []

    while budget.remaining() > 0:
        diag = diagnose(cur)
        if diag is None:                              # already at goal
            return AdaptResult(True, tier, trace, final_report=cur)

        cand = propose(diag, tier, env, deployer.state, tried, regen_proposer)
        if cand is None:
            if tier < 2:
                tier += 1                             # escalate cost tier
                continue
            return AdaptResult(False, tier, trace,
                               reason="all tiers exhausted", final_report=cur)

        # Idle-path capacity check: the one sanctioned active probe (§5.5).
        if (cand.kind == REROUTE
                and cur.path_confidence.get(cand.params[0]) == "inferred"
                and not capacity_ok(cand)):
            tried.add(cand)
            trace.append(AttemptRecord(cand, gate_ok=True, applied=False,
                                       goal_after=False, kept=False,
                                       note="capacity check failed"))
            continue

        # Gate L2 needs the LIVE table state — a prior applied candidate may
        # have changed it. Prefer the deployer's fresh view (real deployer,
        # M4) over any static snapshot passed in.
        tables = (deployer.table_state() if hasattr(deployer, "table_state")
                  else current_tables)
        verdict = gate.check(cand, env, tables)
        if not verdict.ok:
            tried.add(cand)
            trace.append(AttemptRecord(cand, gate_ok=False, applied=False,
                                       goal_after=False, kept=False,
                                       note=verdict.reason))
            continue

        pre_cfg = deployer.capture()
        deployer.apply(cand)
        budget.spend(1)
        post = monitor.observe_window()               # full window + hysteresis
        tried.add(cand)

        if goal(post):
            trace.append(AttemptRecord(cand, True, True, True, kept=True))
            return AdaptResult(True, cand.tier, trace, final_report=post)
        if dominates(post, cur) and improves(post, cur):
            cur = post                                # keep gain, hill-climb
            trace.append(AttemptRecord(cand, True, True, False, kept=True,
                                       note="partial progress"))
        else:
            deployer.rollback(pre_cfg)                # regression / no-op
            trace.append(AttemptRecord(cand, True, True, False, kept=False,
                                       note="dominated or no progress"))

    return AdaptResult(False, tier, trace, reason="budget spent", final_report=cur)
