"""Runtime Manager — episode orchestration (design §7.6).

An episode runs from cutover to a terminal verdict. ANY commit (healthy or
marginal) ends it: promote last-known-good, re-baseline, reset the budget.
Rollback and escalation also end it; the budget is per-episode by
construction (a fresh Budget per run_episode call).
"""
from __future__ import annotations

from runtime import config
from runtime.adapt import Budget
from runtime.contracts import (
    COMMIT_OUTCOMES, REJECTED,
    DeployerProto, DeploymentSpec, GateProto, MonitorProto, Verdict,
)
from runtime.evaluator import EvalContext, EvalResult, evaluate


class RuntimeManager:

    def __init__(self, deployer: DeployerProto, monitor: MonitorProto,
                 gate: GateProto, budget_n: int = config.BUDGET_N,
                 active_capacity_ok=None, current_tables: dict | None = None,
                 regen_proposer=None, kg=None):
        self.deployer = deployer
        self.monitor = monitor
        self.gate = gate
        self.budget_n = budget_n
        self.active_capacity_ok = active_capacity_ok
        self.current_tables = current_tables
        self.regen_proposer = regen_proposer
        self.kg = kg                                 # optional KGClient (§8 writes)
        self.kg_write_failures = 0                    # best-effort write counter
        # Pre-deploy state is the first last-known-good (rung-3 rollback target).
        self.last_good = deployer.capture()

    def _kg_write(self, method: str, *args) -> None:
        """KG writes are best-effort: a transient Neo4j failure must never abort
        an episode (critical for the M6 soak — recording is not the loop's job).
        Failures are swallowed and counted."""
        if self.kg is None:
            return
        try:
            getattr(self.kg, method)(*args)
        except Exception:
            self.kg_write_failures += 1

    def run_episode(self, spec: DeploymentSpec, timestamp: str = "") -> EvalResult:
        # Pre-deploy gate: a refused binding never reaches the network.
        g = self.gate.check_binding(spec)
        if not g.ok:
            result = EvalResult(Verdict(spec.correlation_id, REJECTED, 0, 0.0,
                                        [g.reason], timestamp))
            self._kg_write("write_verdict", result.verdict)   # a refusal is a verdict
            return result

        # Tier-2 regen's gate check (L2 simulation) must see the LIVE switch
        # tables, not a value frozen at construction. Refresh from the deployer
        # each episode unless a static override was supplied (tests). [M7 #6]
        current_tables = self.current_tables
        if current_tables is None and hasattr(self.deployer, "table_state"):
            current_tables = self.deployer.table_state()

        ctx = EvalContext(
            deployer=self.deployer, monitor=self.monitor, gate=self.gate,
            budget=Budget(self.budget_n),            # fresh budget per episode
            last_good=self.last_good,
            active_capacity_ok=self.active_capacity_ok,
            current_tables=current_tables,
            regen_proposer=self.regen_proposer,
            timestamp=timestamp,
        )
        report = self.monitor.observe_window()
        # Monitor-computed switch status replaces the hardcoded SCENARIO_STATE;
        # the pre-cutover baseline (§5.3) is visible regardless of outcome (a
        # commit overwrites it below with the re-baselined one, same key).
        self._kg_write("write_switch_status", report.switch_status, timestamp)
        snap = self._baseline_snapshot(spec, report, timestamp)
        if snap is not None:
            self._kg_write("write_baseline", snap)
        result = evaluate(spec, report, ctx)
        self._kg_write("write_verdict", result.verdict)
        if result.ticket is not None:
            self._kg_write("write_escalation", result.ticket, timestamp)

        if result.verdict.outcome in COMMIT_OUTCOMES:
            # §7.6: any commit ends the episode — promote, re-baseline.
            self.last_good = self.deployer.capture()
            self.monitor.rebaseline()
            self._kg_write("write_last_good", self.last_good, timestamp)
            snap = self._baseline_snapshot(spec, report, timestamp)
            if snap is not None:
                self._kg_write("write_baseline", snap)
        return result

    def _baseline_snapshot(self, spec, report, timestamp):
        """The monitor's post-commit baseline as a BaselineSnapshot, if it
        exposes one (the real NetworkMonitor does; the fake doesn't)."""
        make = getattr(self.monitor, "baseline_snapshot", None)
        if make is None:
            return None
        return make(spec.correlation_id, report.switch_status, timestamp)
