"""Shared data contracts for the Runtime Manager (inner loop).

DeploymentSpec and EscalationTicket cross the planner boundary and are agreed
in docs/runtime-planner-contracts.md; everything else is runtime-internal.
Design: docs/runtime-manager-design.md (sections referenced per class).

Timestamps are caller-supplied strings, never generated here, so every type
is deterministic under test.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PRIMARY = "primary"
BACKUP = "backup"

TUNE = "tune"
REROUTE = "reroute"
REGEN = "regen"
TIER_OF = {TUNE: 0, REROUTE: 1, REGEN: 2}

_EPS = 1e-9


@dataclass
class Envelope:
    """SLA bounds + legal action space (design §9).

    Bounds come from the KG (SFCTemplate / AgriculturalField). The action
    space is runtime-owned and empty when this Envelope only expresses a
    field's requirements (FlowMetrics.requirement).
    """
    max_latency_ms: float
    min_bandwidth_mbps: float
    max_loss_percent: float
    legal_tiers: frozenset = frozenset()          # subset of {TUNE, REROUTE, REGEN}
    legal_paths: frozenset = frozenset((PRIMARY,))
    knob_ranges: dict = field(default_factory=dict)  # knob -> (lo, hi); tune levers only


@dataclass(frozen=True)
class Candidate:
    """One proposed adaptation step. Frozen + tuple params => usable in the
    engine's `tried` set (design §7.2).

    params by kind:
      tune:    (knob_name, new_value)
      reroute: (target_path,)
      regen:   (rules_text,) or simulation effects (("path", v), ...) in fakes
    """
    kind: str            # TUNE | REROUTE | REGEN
    params: tuple

    @property
    def tier(self) -> int:
        return TIER_OF[self.kind]


@dataclass
class FlowMetrics:
    """One field's flow judged against its own requirements (design §5.4)."""
    field_id: str
    rtt_avg_ms: float
    throughput_mbps: float
    loss_percent: float
    requirement: Envelope
    met: bool
    margin: float        # min normalized distance inside the bounds; <0 = violating


def compute_flow_metrics(field_id: str, rtt_avg_ms: float, throughput_mbps: float,
                         loss_percent: float, requirement: Envelope) -> FlowMetrics:
    r = requirement
    margin = min(
        (r.max_latency_ms - rtt_avg_ms) / max(r.max_latency_ms, _EPS),
        (throughput_mbps - r.min_bandwidth_mbps) / max(r.min_bandwidth_mbps, _EPS),
        (r.max_loss_percent - loss_percent) / max(r.max_loss_percent, _EPS),
    )
    return FlowMetrics(field_id, rtt_avg_ms, throughput_mbps, loss_percent,
                       r, margin >= 0.0, margin)


@dataclass
class MonitorReport:
    """The contract every evaluator rung and the adapt engine read (design §5.4)."""
    correlation_id: str
    switch_status: dict           # {"s1": "Active"|"Degraded"|"Failed"|"Standby", ...}
    system_sound: bool            # rung 1
    target: FlowMetrics
    non_target: list              # [FlowMetrics]
    target_sla_met: bool          # rung 2 / adapt goal half
    vs_baseline: dict             # field_id -> margin delta vs baseline
    exogenous_shift: bool         # rung 3 guard (design §5.7)
    displaced_harm: list          # field_ids newly below their own req vs baseline
    headroom: float               # min margin across satisfied flows (rung 6)
    path_confidence: dict         # {"primary"|"backup": "observed"|"inferred"}


def goal(report: MonitorReport) -> bool:
    """GOAL(report) — design §7: target healthy and nobody harmed."""
    return report.target_sla_met and not report.displaced_harm


@dataclass
class ConfigSnapshot:
    """Exact installed state for deterministic rollback (design §5.3, §10.5)."""
    correlation_id: str
    sfc: str
    binding: dict
    switch_table_dumps: dict      # switch -> [{table, key, handle, action, args}]
    qos_state: dict               # host -> tc settings
    active_path: str              # PRIMARY | BACKUP


@dataclass
class BaselineSnapshot:
    """Pre-cutover steady-state window of ALL flows (design §5.3)."""
    correlation_id: str
    per_flow: dict                # field_id -> {rtt_avg_ms, throughput_mbps, loss_percent}
    switch_status: dict
    captured_over_window: int     # number of probes in the window
    timestamp: str


@dataclass
class DeploymentSpec:
    """Planner -> runtime handoff, normalized (docs/runtime-planner-contracts.md §1)."""
    sfc: str                      # already chosen — runtime never selects
    binding: dict
    envelope: Envelope
    correlation_id: str
    target_field: str


@dataclass
class Diagnosis:
    """Worst current violation (design §7.3)."""
    who: str                      # "target" or a harmed field_id
    metric: str                   # "latency" | "throughput" | "loss"
    severity: float               # |margin| of the violation


@dataclass
class GateResult:
    ok: bool
    reason: str = ""


@dataclass
class AttemptRecord:
    """One adapt attempt, for the trace (design §7.5)."""
    candidate: Candidate
    gate_ok: bool
    applied: bool
    goal_after: bool
    kept: bool
    note: str = ""


@dataclass
class AdaptResult:
    success: bool
    tier_reached: int
    trace: list                   # [AttemptRecord]
    reason: str = ""              # on failure: "all tiers exhausted" | "budget spent" | ...


@dataclass
class Verdict:
    """Terminal evaluator outcome, recorded to the KG (design §8)."""
    correlation_id: str
    outcome: str                  # "healthy" | "marginal" | "rollback" | "escalated" | "system_fault"
    tier_reached: int
    headroom: float
    trace: list
    timestamp: str


@dataclass
class EscalationTicket:
    """Runtime -> planner wrong-SFC handoff (docs/runtime-planner-contracts.md §2)."""
    correlation_id: str
    sfc: str
    observed: dict
    envelope: Envelope
    trace: list
    reason: str                   # "all tiers exhausted" | "budget spent" | "no harm-free config"
