"""Runtime Manager — episode orchestration (design §7.6).

An episode runs from cutover to a terminal verdict. ANY commit (healthy or
marginal) ends it: promote last-known-good, re-baseline, reset the budget.
Rollback and escalation also end it; the budget is per-episode by
construction (a fresh Budget per run_episode call).
"""
from __future__ import annotations

from runtime import config
from runtime.adapt import Budget
from runtime.contracts import DeploymentSpec, Verdict
from runtime.evaluator import EvalContext, EvalResult, evaluate


class RuntimeManager:

    def __init__(self, deployer, monitor, gate,
                 budget_n: int = config.BUDGET_N,
                 active_capacity_ok=None, current_tables: dict | None = None):
        self.deployer = deployer
        self.monitor = monitor
        self.gate = gate
        self.budget_n = budget_n
        self.active_capacity_ok = active_capacity_ok
        self.current_tables = current_tables
        # Pre-deploy state is the first last-known-good (rung-3 rollback target).
        self.last_good = deployer.capture()

    def run_episode(self, spec: DeploymentSpec, timestamp: str = "") -> EvalResult:
        # Pre-deploy gate: a refused binding never reaches the network.
        g = self.gate.check_binding(spec)
        if not g.ok:
            return EvalResult(Verdict(spec.correlation_id, "rejected", 0, 0.0,
                                      [g.reason], timestamp))

        ctx = EvalContext(
            deployer=self.deployer, monitor=self.monitor, gate=self.gate,
            budget=Budget(self.budget_n),            # fresh budget per episode
            last_good=self.last_good,
            active_capacity_ok=self.active_capacity_ok,
            current_tables=self.current_tables,
            timestamp=timestamp,
        )
        result = evaluate(spec, self.monitor.observe_window(), ctx)

        if result.verdict.outcome in ("healthy", "marginal"):
            # §7.6: any commit ends the episode — promote, re-baseline.
            self.last_good = self.deployer.capture()
            self.monitor.rebaseline()
        return result
