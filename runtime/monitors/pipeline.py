"""Network-monitor computation pipeline — the pure-logic half of M5
(design §5.2–§5.7). Everything here is deterministic and testable off-testbed;
the node half (network_monitor.py, M5) only wires samplers to these functions.

Sections: hysteresis · counters · parsers · exogenous shift · status
derivation · window summary · report assembly.
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from runtime import config
from runtime.contracts import FlowMetrics, MonitorReport, compute_flow_metrics

_EPS = 1e-9


# ---------------- hysteresis (design §5.6) ----------------

class HysteresisTracker:
    """K-of-M sliding window per flow:
        ok -> suspect -> violating     (>= K bad probes in the last M)
        violating -> recovering -> ok  (>= K good probes in the last M)
    `met` flips only on the sustained condition — noise never triggers action
    or a premature success call.
    """

    def __init__(self, k: int = config.HYSTERESIS_K, m: int = config.HYSTERESIS_M):
        assert 0 < k <= m
        self.k, self.m = k, m
        self.window: deque = deque(maxlen=m)
        self._violating = False

    def update(self, raw_met: bool) -> bool:
        """Feed one probe; returns the post-hysteresis met."""
        self.window.append(raw_met)
        bad = sum(1 for x in self.window if not x)
        good = len(self.window) - bad
        if not self._violating and bad >= self.k:
            self._violating = True
        elif self._violating and good >= self.k:
            self._violating = False
        return not self._violating

    @property
    def met(self) -> bool:
        return not self._violating

    @property
    def state(self) -> str:
        bad = sum(1 for x in self.window if not x)
        good = len(self.window) - bad
        if self._violating:
            return "recovering" if good else "violating"
        return "suspect" if bad else "ok"


# ---------------- passive counters (design §5.2) ----------------

@dataclass(frozen=True)
class CounterSample:
    """One reading of an interface's cumulative OS counters.
    timestamp is caller-supplied monotonic seconds (determinism under test)."""
    timestamp: float
    rx_bytes: int
    tx_bytes: int
    rx_packets: int
    tx_packets: int


def read_interface_counters(intf: str, timestamp: float,
                            base: str = "/sys/class/net") -> CounterSample:
    """Read /sys/class/net/<intf>/statistics — the zero-P4-change passive
    channel (§5.2). `base` is injectable for tests; on the node it defaults
    to the real sysfs."""
    stats = Path(base) / intf / "statistics"
    read = lambda name: int((stats / name).read_text().strip())
    return CounterSample(timestamp, read("rx_bytes"), read("tx_bytes"),
                         read("rx_packets"), read("tx_packets"))


def throughput_mbps(prev: CounterSample, cur: CounterSample,
                    direction: str = "rx") -> float:
    """Rate from two cumulative samples. direction 'rx' = traffic INTO the
    switch port (upstream at s1 drone ports)."""
    dt = cur.timestamp - prev.timestamp
    if dt <= 0:
        raise ValueError("non-increasing sample timestamps")
    delta = (cur.rx_bytes - prev.rx_bytes if direction == "rx"
             else cur.tx_bytes - prev.tx_bytes)
    return max(0.0, delta * 8 / (dt * 1e6))


def aggregate_field_throughput(prev: dict, cur: dict, port_map: dict) -> dict:
    """Per-field upstream throughput: sum each field's drone-port rates.
    prev/cur: {port: CounterSample}; port_map: {port: field_id} (port->drone
    is the topology order, drone->field comes from the KG at M5)."""
    out: dict = {}
    for port, field_id in port_map.items():
        if port in prev and port in cur:
            out[field_id] = out.get(field_id, 0.0) + throughput_mbps(prev[port], cur[port])
    return out


def link_loss_percent(tx_prev: CounterSample, tx_cur: CounterSample,
                      rx_prev: CounterSample, rx_cur: CounterSample) -> float:
    """Loss on one link: packets sent into it (tx of the upstream port) vs
    packets out the far end (rx of the downstream port), same interval."""
    sent = tx_cur.tx_packets - tx_prev.tx_packets
    got = rx_cur.rx_packets - rx_prev.rx_packets
    if sent <= 0:
        return 0.0
    return max(0.0, (sent - got) / sent * 100.0)


# ---------------- parsers ----------------

_RTT_RE = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)")
_PKTS_RE = re.compile(r"(\d+) packets transmitted, (\d+) received")


def parse_ping(text: str) -> tuple:
    """(rtt_avg_ms, loss_percent) from ping output, (None, None) if unparseable.
    Same regexes as the existing results parser."""
    rtt = _RTT_RE.search(text)
    pkts = _PKTS_RE.search(text)
    rtt_avg = float(rtt.group(2)) if rtt else None
    loss = None
    if pkts:
        tx, rx = int(pkts.group(1)), int(pkts.group(2))
        loss = round((tx - rx) / tx * 100, 2) if tx else None
    return rtt_avg, loss


_QDISC_PATTERNS = {
    "delay_ms": re.compile(r"delay ([\d.]+)ms"),
    "loss_percent": re.compile(r"loss ([\d.]+)%"),
    "rate_mbit": re.compile(r"rate ([\d.]+)Mbit"),
    "limit_pkts": re.compile(r"limit (\d+)p?\b"),
}


def parse_qdisc(text: str) -> dict:
    """Numeric impairment/shaping params from `tc qdisc show dev <intf>`."""
    out = {}
    for key, pat in _QDISC_PATTERNS.items():
        m = pat.search(text)
        if m:
            out[key] = float(m.group(1))
    return out


# ---------------- exogenous shift (design §5.7) ----------------

def qdisc_shifted(baseline: dict, current: dict, rel_tol: float = 0.25) -> bool:
    """Did link impairments change vs baseline? baseline/current:
    {link_name: parse_qdisc(...) dict} for links WE don't manage — on the
    testbed the scenario knobs live there, so a change is exogenous by
    definition. A param appearing/disappearing, or moving by more than
    rel_tol relative, counts as a shift."""
    for link in set(baseline) | set(current):
        b, c = baseline.get(link, {}), current.get(link, {})
        for key in set(b) | set(c):
            if (key in b) != (key in c):
                return True
            ref = max(abs(b[key]), _EPS)
            if abs(c[key] - b[key]) / ref > rel_tol:
                return True
    return False


# ---------------- switch status derivation (design §5.5) ----------------

def derive_switch_status(process_alive: bool, thrift_reachable: bool,
                         carrying_traffic: bool, path_sla_ok: bool) -> str:
    """Replaces the hardcoded SCENARIO_STATE table with observation.
    path_sla_ok must be the SUSTAINED (post-hysteresis) verdict for the
    path through this switch; it is ignored for idle switches."""
    if not (process_alive and thrift_reachable):
        return "Failed"
    if not carrying_traffic:
        return "Standby"
    return "Active" if path_sla_ok else "Degraded"


# ---------------- window summary (baseline capture, design §5.3) ----------------

def summarize_window(samples: list) -> dict:
    """Average per-field (rtt, tput, loss) tuples over a steady-state window.
    samples: [{field_id: (rtt, tput, loss)}, ...] — one dict per probe."""
    if not samples:
        raise ValueError("empty window")
    fields = samples[0].keys()
    out = {}
    for f in fields:
        vals = [s[f] for s in samples]
        n = len(vals)
        out[f] = tuple(sum(v[i] for v in vals) / n for i in range(3))
    return out


# ---------------- report assembly (design §5.4) ----------------

def assemble_report(*, correlation_id: str, target_field: str,
                    raw: dict, requirements: dict, baseline: dict,
                    switch_status: dict, system_sound: bool = True,
                    exogenous_shift: bool = False,
                    path_confidence: dict | None = None,
                    smoothed_met: dict | None = None) -> MonitorReport:
    """The single MonitorReport builder — shared by the scenario fixtures and
    the real monitor, so fixture behavior cannot drift from production.

    raw:          {field_id: (rtt_ms, tput_mbps, loss_pct)} this window
    requirements: {field_id: Envelope} (each field's own bounds)
    baseline:     {field_id: FlowMetrics} captured pre-cutover (§5.3)
    smoothed_met: optional {field_id: bool} post-hysteresis overrides —
                  margins stay raw; only the met verdict is smoothed (§5.6)
    """
    flows: dict = {}
    for field_id, req in requirements.items():
        rtt, tput, loss = raw[field_id]
        fm = compute_flow_metrics(field_id, rtt, tput, loss, req)
        if smoothed_met is not None and field_id in smoothed_met:
            fm.met = smoothed_met[field_id]
        flows[field_id] = fm

    target = flows[target_field]
    non_target = [f for k, f in flows.items() if k != target_field]
    harm = [f.field_id for f in non_target
            if not f.met and baseline[f.field_id].met]
    satisfied = [f for f in flows.values() if f.met]
    headroom = min((f.margin for f in satisfied), default=0.0)

    return MonitorReport(
        correlation_id=correlation_id,
        switch_status=dict(switch_status),
        system_sound=system_sound,
        target=target,
        non_target=non_target,
        target_sla_met=target.met,
        vs_baseline={k: flows[k].margin - baseline[k].margin for k in flows},
        exogenous_shift=exogenous_shift,
        displaced_harm=harm,
        headroom=headroom,
        path_confidence=dict(path_confidence or {}),
    )
