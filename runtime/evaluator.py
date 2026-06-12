"""Post-deploy evaluator — the 6-stage attribution + commit ladder (design §6).

  1  system healthy?          no -> system_fault (tiered fix is the RM's job)
  2  SLA met, sustained?      yes -> commit path (5)
  3  our change caused it?    = regressed-vs-baseline AND NOT exogenous_shift
                              yes -> rollback to last-good
  4  in-envelope fix left?    adapt; failure -> escalate (wrong SFC)
  5  no displaced harm?       harm -> adapt to relieve (Option B);
                              no harm-free config -> escalate
  6  headroom check           margin ok -> healthy; thin or Tier-2 -> marginal

Every terminal outcome is a Verdict; escalations also carry an
EscalationTicket. Persistence to the KG is the kg_client's job (M5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from runtime import config
from runtime.adapt import Budget, adapt
from runtime.contracts import (
    AdaptResult, DeploymentSpec, EscalationTicket, MonitorReport, Verdict,
)


@dataclass
class EvalContext:
    """Collaborators + episode state the ladder needs."""
    deployer: object
    monitor: object
    gate: object
    budget: Budget
    last_good: dict | None = None         # config snapshot to roll back to
    active_capacity_ok: object = None     # callable(Candidate) -> bool, or None
    current_tables: dict | None = None    # for gate L2 on regen candidates
    regen_proposer: object = None         # Tier-2 seam (M7); None = stubbed
    timestamp: str = ""


@dataclass
class EvalResult:
    verdict: Verdict
    ticket: EscalationTicket | None = None


def commit_outcome(headroom: float, tier_reached: int) -> str:
    """Stage 6 + the §7.5 rule: Tier-2 recovery is marginal regardless."""
    if tier_reached >= 2:
        return "marginal"
    return "healthy" if headroom >= config.HEADROOM_TAU else "marginal"


def _observed(report: MonitorReport) -> dict:
    flows = [report.target] + list(report.non_target)
    return {f.field_id: {"rtt_avg_ms": f.rtt_avg_ms,
                         "throughput_mbps": f.throughput_mbps,
                         "loss_percent": f.loss_percent,
                         "met": f.met, "margin": f.margin}
            for f in flows}


def _escalate(spec: DeploymentSpec, res: AdaptResult, reason: str,
              ctx: EvalContext, trace: list) -> EvalResult:
    report = res.final_report
    ticket = EscalationTicket(
        correlation_id=spec.correlation_id, sfc=spec.sfc,
        observed=_observed(report), envelope=spec.envelope,
        trace=trace, reason=reason,
    )
    verdict = Verdict(spec.correlation_id, "escalated", res.tier_reached,
                      report.headroom, trace, ctx.timestamp)
    return EvalResult(verdict, ticket)


def evaluate(spec: DeploymentSpec, report: MonitorReport, ctx: EvalContext) -> EvalResult:
    trace: list = []
    tier_reached = 0
    r = report

    # 1 · system healthy? (sound)
    if not r.system_sound:
        return EvalResult(Verdict(spec.correlation_id, "system_fault", 0,
                                  r.headroom, trace, ctx.timestamp))

    # 2 · SLA met, sustained?
    if not r.target_sla_met:
        # 3 · our change caused it? (regressed vs baseline AND not exogenous, §5.7)
        regressed = r.vs_baseline.get(spec.target_field, 0.0) < 0.0
        if regressed and not r.exogenous_shift:
            if ctx.last_good is not None:
                ctx.deployer.rollback(ctx.last_good)
            return EvalResult(Verdict(spec.correlation_id, "rollback", 0,
                                      r.headroom, trace, ctx.timestamp))
        # 4 · in-envelope fix left?
        res = adapt(spec, r, ctx.budget, ctx.deployer, ctx.monitor, ctx.gate,
                    ctx.active_capacity_ok, ctx.current_tables, ctx.regen_proposer)
        trace = list(res.trace)
        tier_reached = res.tier_reached
        if not res.success:
            return _escalate(spec, res, res.reason, ctx, trace)
        r = res.final_report                          # goal report

    # COMMIT PATH ----------------------------------------------------------
    # 5 · no displaced harm? (Option B: adapt to relieve before escalating)
    if r.displaced_harm:
        res = adapt(spec, r, ctx.budget, ctx.deployer, ctx.monitor, ctx.gate,
                    ctx.active_capacity_ok, ctx.current_tables, ctx.regen_proposer)
        trace = trace + list(res.trace)
        tier_reached = max(tier_reached, res.tier_reached)
        if not res.success:
            # engine reason (tiers/budget) maps to the contract vocabulary:
            # the joint-feasible set is empty (design §7.4).
            return _escalate(spec, res, "no harm-free config", ctx, trace)
        r = res.final_report

    # 6 · headroom check
    outcome = commit_outcome(r.headroom, tier_reached)
    return EvalResult(Verdict(spec.correlation_id, outcome, tier_reached,
                              r.headroom, trace, ctx.timestamp))
