"""Shared data contracts for the Runtime Manager (inner loop).

DeploymentSpec and EscalationTicket cross the planner boundary and are agreed
in docs/runtime-planner-contracts.md; everything else is runtime-internal.
Design: docs/runtime-manager-design.md (sections referenced per class).

Timestamps are caller-supplied strings, never generated here, so every type
is deterministic under test.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

PRIMARY = "primary"
BACKUP = "backup"

TUNE = "tune"
REROUTE = "reroute"
REGEN = "regen"
TIER_OF = {TUNE: 0, REROUTE: 1, REGEN: 2}

# Terminal Verdict.outcome vocabulary (design §6/§8). Bare-str constants, NOT an
# enum: the value is compared by == (the §7.6 commit check, tests), keyed in
# soak's counter dict, persisted to the KG, and round-tripped through jsonable()
# — exactly like PRIMARY/TUNE above.
HEALTHY = "healthy"
MARGINAL = "marginal"
ROLLBACK = "rollback"
ESCALATED = "escalated"
SYSTEM_FAULT = "system_fault"
REJECTED = "rejected"                  # gate-refused, never deployed
OUTCOMES = frozenset((HEALTHY, MARGINAL, ROLLBACK, ESCALATED, SYSTEM_FAULT, REJECTED))
COMMIT_OUTCOMES = frozenset((HEALTHY, MARGINAL))   # the §7.6 promote/re-baseline pair

# Switch status vocabulary (design §5.5): written by derive_switch_status, read
# by the monitor's _system_sound / the watchdog.
SW_FAILED = "Failed"                   # dead process or thrift
SW_STANDBY = "Standby"                 # alive, idle (not carrying)
SW_ACTIVE = "Active"                   # alive, carrying, SLA ok
SW_DEGRADED = "Degraded"               # alive, carrying, SLA bad (not a fault)
SWITCH_STATUSES = frozenset((SW_FAILED, SW_STANDBY, SW_ACTIVE, SW_DEGRADED))

# Diagnosis vocabulary (design §7.3): the per-metric axes + the target sentinel.
# These are also the keys of dimension_margins() below, which become
# Diagnosis.metric via diagnose() — keep both ends on the same constants.
TARGET = "target"                      # Diagnosis.who when the target itself violates
LATENCY = "latency"
THROUGHPUT = "throughput"
LOSS = "loss"
DIAGNOSIS_METRICS = frozenset((LATENCY, THROUGHPUT, LOSS))

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
      regen:   (switch, rules_text)   — the real shape used by the proposer, gate,
               and deployer. (FakeDeployer ALSO accepts simulation-effect tuples
               (("path", v), ...) for the off-node scenario models.)
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


def dimension_margins(fm: FlowMetrics) -> dict:
    """Per-metric normalized SLA margins for one flow (design §5.4/§7.3).

    Each entry is the signed distance INSIDE the bound, normalized by the bound,
    so margins are comparable across dimensions; <0 means that dimension is
    violating. FlowMetrics.margin is exactly min(this.values()). This is the one
    definition both the evaluator (via compute_flow_metrics) and the adapt
    engine's diagnose() consume — keeping them from diverging (the keys are the
    DIAGNOSIS_METRICS constants, so diagnose()'s metric flows back unchanged)."""
    r = fm.requirement
    return {
        LATENCY: (r.max_latency_ms - fm.rtt_avg_ms) / max(r.max_latency_ms, _EPS),
        THROUGHPUT: (fm.throughput_mbps - r.min_bandwidth_mbps) / max(r.min_bandwidth_mbps, _EPS),
        LOSS: (r.max_loss_percent - fm.loss_percent) / max(r.max_loss_percent, _EPS),
    }


def compute_flow_metrics(field_id: str, rtt_avg_ms: float, throughput_mbps: float,
                         loss_percent: float, requirement: Envelope) -> FlowMetrics:
    # Build first, then derive margin/met from the single shared formula:
    # min(dimension_margins) is arithmetically identical to the old inline min().
    fm = FlowMetrics(field_id, rtt_avg_ms, throughput_mbps, loss_percent,
                     requirement, met=False, margin=0.0)
    fm.margin = min(dimension_margins(fm).values())
    fm.met = fm.margin >= 0.0
    return fm


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
class TableEntry:
    """One installed table entry — the unit of ConfigSnapshot.switch_table_dumps
    and the state the gate's L2 simulation runs against (design §10.5, §11)."""
    table: str
    key: str              # match key: MAC (forward_table) or IPv4 (policy tables)
    action: str
    args: tuple           # e.g. (egress_port,) for forward; () for marker actions
    handle: int


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
    who: str                      # TARGET, or a harmed field_id
    metric: str                   # one of DIAGNOSIS_METRICS (LATENCY | THROUGHPUT | LOSS)
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
    final_report: object = None   # MonitorReport at exit: goal report on success,
                                  # best-achieved (dominating) state on failure


@dataclass
class Verdict:
    """Terminal evaluator outcome, recorded to the KG (design §8)."""
    correlation_id: str
    outcome: str                  # one of OUTCOMES (HEALTHY | MARGINAL | ROLLBACK
                                  #   | ESCALATED | SYSTEM_FAULT | REJECTED — gate-refused)
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


def jsonable(obj):
    """Recursively convert any contracts object to JSON-serializable
    structures — dataclasses to dicts, sets/frozensets to sorted lists,
    tuples to lists. The kg_client persists Verdicts/tickets/snapshots as
    JSON properties; raw dataclasses with frozensets are not dumpable."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name))
                for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Deployer protocol (design §9/§10) — the surface the engine, evaluator, and
# RuntimeManager actually consume. The real deployer (M4) implements ALL of it;
# FakeDeployer implements the subset the off-node engine/evaluator tests need
# (state, capture, apply, rollback) — deploy/re_push/table_state/recover_switch
# are node-only and exercised by the integration tests, so engine callers guard
# the optional ones (`hasattr(deployer, "table_state")`).
#
#   state -> {"path": PRIMARY|BACKUP, "knobs": {knob: value}}
#       current applied config; propose() reads it to step knobs / skip
#       no-op reroutes.
#   capture() -> opaque snapshot      accepted back by rollback(); the real
#       deployer returns a ConfigSnapshot, the fake a dict — the engine
#       never looks inside.
#   apply(Candidate) -> None          TUNE: resolve which hosts to `tc` from
#       the ACTIVE DEPLOYMENT's target field (candidates are host-agnostic);
#       REROUTE: flip the s1 edge-MAC entry; REGEN: install rules text.
#   rollback(snapshot) -> None        deterministic restore of a capture().
#   re_push(snapshot) -> None         same revision, fresh install (rung 1).
#   deploy(spec) -> ConfigSnapshot    episode start; the BaselineSnapshot is
#       captured by the MONITOR pre-cutover (RM orchestrates) — the deployer
#       owns config state only.
#   table_state() -> {switch: [TableEntry]}   OPTIONAL but required once
#       regen is live: fresh installed-entry state for gate L2; the engine
#       prefers it over any static snapshot.
# ---------------------------------------------------------------------------
#
# The prose above is the authority on semantics; the Protocols below make the
# *required* surface checkable (real Deployer + FakeDeployer both satisfy
# DeployerProto). The node-only methods stay in their own optional Protocols so
# the existing `hasattr(deployer, "table_state")` guards remain correct — the
# engine narrows to TableStateCapable only when it needs the live view. These
# annotations are erased at runtime (`from __future__ import annotations`), so
# adding them changes nothing the program does; they exist for the type-checker.


@runtime_checkable
class DeployerProto(Protocol):
    """The surface the engine/evaluator/RuntimeManager require of ANY deployer."""
    @property
    def state(self) -> dict: ...
    def capture(self): ...                         # ConfigSnapshot | dict (opaque)
    def apply(self, cand: "Candidate") -> None: ...
    def rollback(self, snapshot) -> None: ...


@runtime_checkable
class TableStateCapable(Protocol):
    """Optional node-only seam (real Deployer): live installed-entry state for
    gate L2 on regen candidates. Guarded by hasattr in the engine/RM."""
    def table_state(self) -> dict: ...


class MonitorProto(Protocol):
    """The monitor surface the loop consumes every episode/attempt."""
    def observe_window(self) -> "MonitorReport": ...
    def rebaseline(self) -> None: ...


class BaselineCapable(Protocol):
    """Optional: the real NetworkMonitor exposes a KG-persistable baseline; the
    RM probes for it via getattr(monitor, 'baseline_snapshot', None)."""
    def baseline_snapshot(self, correlation_id: str, switch_status: dict,
                          timestamp: str): ...


class GateProto(Protocol):
    """The validation-gate surface: a pre-deploy binding check and a per-candidate
    check (design §11)."""
    def check_binding(self, spec: "DeploymentSpec") -> "GateResult": ...
    def check(self, cand: "Candidate", env: "Envelope",
              current_tables: dict | None = None) -> "GateResult": ...
