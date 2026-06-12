"""Scenario fixtures — synthetic network models (implementation plan M1/M3).

Each ScenarioModel is a tiny deterministic physics model: deployer state in,
MonitorReport out. The six scenarios are the behavioral specification of the
evaluator + adapt engine (M3 exits), the CI regression suite, and the paper's
evaluation matrix.

Conventions: target field F1 unless stated; neighbor F2. Numbers are chosen so
margins are unambiguous (no zero-margin knife edges).
"""
from __future__ import annotations

from runtime.contracts import (
    BACKUP, PRIMARY, REGEN, REROUTE, TUNE,
    Envelope, MonitorReport, compute_flow_metrics,
)

# Field requirements (mirrors generate_kg.py: F1 high/low-latency, F2 medium).
F1_REQ = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2)
F2_REQ = Envelope(max_latency_ms=50, min_bandwidth_mbps=20, max_loss_percent=3)

OK_STATUS = {"s1": "Active", "s2": "Active", "s3": "Standby"}


class ScenarioModel:
    """Subclasses define metrics(state) -> {field_id: (rtt, tput, loss)} and
    the scenario's environment facts. Baseline = the model evaluated at the
    pre-cutover state (design §5.3)."""

    correlation_id = "fixture"
    target_field = "F1"
    fields = {"F1": F1_REQ, "F2": F2_REQ}
    exogenous_shift = False
    system_sound = True
    switch_status = OK_STATUS
    path_confidence = {PRIMARY: "observed", BACKUP: "inferred"}

    def __init__(self, baseline_state: dict | None = None):
        self.baseline_state = baseline_state or {"path": PRIMARY, "knobs": {}}
        self.baseline = self._flows(self.baseline_state)

    def metrics(self, state: dict) -> dict:
        raise NotImplementedError

    def _flows(self, state: dict) -> dict:
        out = {}
        for field_id, req in self.fields.items():
            rtt, tput, loss = self.metrics(state)[field_id]
            out[field_id] = compute_flow_metrics(field_id, rtt, tput, loss, req)
        return out

    def report(self, state: dict) -> MonitorReport:
        flows = self._flows(state)
        target = flows[self.target_field]
        non_target = [f for k, f in flows.items() if k != self.target_field]
        harm = [f.field_id for f in non_target
                if not f.met and self.baseline[f.field_id].met]
        satisfied = [f for f in flows.values() if f.met]
        headroom = min((f.margin for f in satisfied), default=0.0)
        return MonitorReport(
            correlation_id=self.correlation_id,
            switch_status=dict(self.switch_status),
            system_sound=self.system_sound,
            target=target,
            non_target=non_target,
            target_sla_met=target.met,
            vs_baseline={k: flows[k].margin - self.baseline[k].margin for k in flows},
            exogenous_shift=self.exogenous_shift,
            displaced_harm=harm,
            headroom=headroom,
            path_confidence=dict(self.path_confidence),
        )


class Healthy(ScenarioModel):
    """Deploy lands cleanly: everyone comfortably inside bounds.
    Expected (M3): rung 2 yes -> stage 5 clean -> stage 6 margin ok -> Commit-healthy."""

    def metrics(self, state):
        return {"F1": (10, 55, 0.5), "F2": (25, 30, 1.0)}


class CausalRegression(ScenarioModel):
    """Our own binding shipped a bad queue tune (pfifo too shallow -> loss).
    Environment unchanged. Expected: rung 3 yes -> rollback to last-good."""

    def metrics(self, state):
        shallow = state["knobs"].get("pfifo_limit", 20) < 10
        f1_loss = 5.0 if shallow else 0.5
        return {"F1": (12, 50, f1_loss), "F2": (25, 30, 1.0)}


class PathQualityFault(ScenarioModel):
    """relay_failure analog: primary relay (s2) degrades under us — exogenous.
    Backup path is healthy. Reroute is fabric-global (design §10.1): both
    fields move together, and both are fine on backup.
    Expected: rung 3 no (exogenous) -> rung 4 -> Tier-1 reroute -> GOAL -> commit."""

    exogenous_shift = True
    switch_status = {"s1": "Active", "s2": "Degraded", "s3": "Active"}

    def __init__(self):
        # Baseline was captured BEFORE the relay degraded (design §5.7) —
        # that is what makes the regression real but exogenous.
        super().__init__(baseline_state={"path": PRIMARY, "knobs": {}, "env": "pre"})

    def metrics(self, state):
        if state.get("env") == "pre":          # pre-shift environment (baseline)
            return {"F1": (12, 50, 0.5), "F2": (25, 28, 1.0)}
        if state["path"] == PRIMARY:           # degraded primary hurts everyone
            return {"F1": (45, 42, 4.0), "F2": (60, 22, 4.0)}
        return {"F1": (14, 48, 0.5), "F2": (22, 26, 0.8)}


class ContentionHarmWithKnob(ScenarioModel):
    """Bandwidth-shaped target grabs the shared link and starves F2.
    Physics: shared capacity 70; target takes min(rate, 70); F2 gets the rest
    (demand 25, requirement 20). Feasible window exists around rate ~45.
    Expected: rung 2 yes -> stage 5 harm -> Tier-0 rate-down steps -> harm-free commit."""

    target_field = "F1"
    fields = {"F1": Envelope(max_latency_ms=60, min_bandwidth_mbps=40, max_loss_percent=2),
              "F2": F2_REQ}

    def __init__(self):
        # Baseline: before our deploy the target wasn't grabbing (rate modest).
        super().__init__(baseline_state={"path": PRIMARY, "knobs": {"tbf_rate_mbit": 30}})

    def metrics(self, state):
        rate = state["knobs"].get("tbf_rate_mbit", 80)
        f1_tput = min(rate, 70)
        f2_tput = min(25, max(0, 70 - f1_tput))
        return {"F1": (15, f1_tput, 0.5), "F2": (25, f2_tput, 1.0)}


class ContentionHarmNoKnob(ScenarioModel):
    """Target has no shaping knob (grab is fixed at 70) and harms F2.
    Reroute moves BOTH flows to the 40-capacity backup, where the target
    itself violates — dominated, rolled back. No harm-free config exists.
    Expected: stage 5 harm -> adapt exhausts -> escalate 'no harm-free config'."""

    target_field = "F1"
    fields = {"F1": Envelope(max_latency_ms=60, min_bandwidth_mbps=40, max_loss_percent=2,
                             legal_tiers=frozenset((REROUTE,)),
                             legal_paths=frozenset((PRIMARY, BACKUP))),
              "F2": F2_REQ}

    def __init__(self):
        super().__init__(baseline_state={"path": PRIMARY, "knobs": {"grab": 30}})

    def metrics(self, state):
        if state["path"] == BACKUP:            # backup capacity 40, shared
            return {"F1": (30, 30, 1.0), "F2": (35, 10, 1.0)}
        grab = state["knobs"].get("grab", 70)  # no legal knob reaches this
        f2_tput = min(25, max(0, 70 - grab))
        return {"F1": (15, min(grab, 70), 0.5), "F2": (25, f2_tput, 1.0)}


class Ddil(ScenarioModel):
    """Everything degraded, both paths, exogenous. Nothing in-envelope helps.
    Expected: rung 3 no (exogenous) -> rung 4 adapt -> all tiers fail ->
    escalate 'budget spent' with full trace."""

    exogenous_shift = True
    switch_status = {"s1": "Active", "s2": "Degraded", "s3": "Degraded"}

    def __init__(self):
        super().__init__(baseline_state={"path": PRIMARY, "knobs": {}, "env": "pre"})

    def metrics(self, state):
        if state.get("env") == "pre":          # pre-ddil environment (baseline)
            return {"F1": (12, 50, 0.5), "F2": (25, 28, 1.0)}
        return {"F1": (80, 8, 5.0), "F2": (90, 6, 5.0)}


ALL_SCENARIOS = {
    "healthy": Healthy,
    "causal_regression": CausalRegression,
    "path_quality_fault": PathQualityFault,
    "contention_harm_with_knob": ContentionHarmWithKnob,
    "contention_harm_no_knob": ContentionHarmNoKnob,
    "ddil": Ddil,
}
