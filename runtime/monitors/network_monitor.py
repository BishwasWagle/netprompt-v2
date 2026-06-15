"""Network monitor — the node half of M5 (design §5.2–§5.7).

Wires real samplers (ping, sysfs counters, tc qdisc, switch liveness) to the pure
pipeline.py computations and presents the monitor protocol the loop consumes:
`observe_window() -> MonitorReport` and `rebaseline()`.

Split in two so the orchestration is testable off-node:
  * NetworkMonitor — the window loop, hysteresis, status derivation, report
    assembly. Takes an injectable `sampler`; unit-tested with a fake.
  * NodeSampler   — the real I/O: ping via the namespace runner, counters via
    sysfs (root ns), qdisc via tc, liveness via SystemMonitor. Thin; the pieces
    are M0/M5-spike-verified on the node.

Field metrics (Model B, Issue-2): RTT/loss come from a drone-namespace ping;
upstream throughput and the *environment* qdisc come from the SWITCH-side veths
(s1-eth{port}) in the root namespace — which the deployer never writes — so a
TUNE on a drone never perturbs the measured environment.
"""
from __future__ import annotations

import subprocess
import time

from runtime import config
from runtime.contracts import (
    BACKUP, PRIMARY, BaselineSnapshot, compute_flow_metrics,
)
from runtime.monitors.pipeline import (
    HysteresisTracker, aggregate_field_throughput, assemble_report,
    derive_switch_status, parse_ping, parse_qdisc, qdisc_shifted,
    read_interface_counters, summarize_window, throughput_mbps,
)
from runtime.monitors.system_monitor import SystemMonitor
from runtime.node_runner import RunnerError

# s1 relay egress ports -> the relay switch they feed (config.PORT_PRIMARY/BACKUP).
_RELAY_PORT_SWITCH = {config.PORT_PRIMARY: "s2", config.PORT_BACKUP: "s3"}
_TRAFFIC_FLOOR_MBPS = 0.01            # below this a port counts as idle (Standby)
# An unreachable ping (None rtt) must read as a latency VIOLATION, not 0 ms
# (which would look perfect); None loss reads as full loss.
_UNREACHABLE_RTT_MS = 10_000.0


def _safe_rate(prev: dict, port, direction="tx") -> float:
    """throughput_mbps for one port, but never raises — a missing port or a
    degenerate dt (<=0) yields 0.0 so a rate computation can't crash the loop."""
    a, b = prev
    if port not in a or port not in b:
        return 0.0
    try:
        return throughput_mbps(a[port], b[port], direction)
    except ValueError:
        return 0.0


def _system_sound(switch_status: dict) -> bool:
    """Rung-1 system fault (design §5.5) = the fabric genuinely cannot carry
    traffic: the access switch s1 died, OR BOTH relays died (no path exists).
    A SINGLE relay death is recoverable — it stays sound so the loop reaches
    rung-4 and reroutes to the surviving relay rather than bailing. A 'Failed'
    means a dead process/thrift; 'Degraded' (alive, SLA-bad) is NOT a fault and
    is handled by normal adaptation."""
    if switch_status.get("s1") == "Failed":
        return False
    relays = [switch_status.get("s2"), switch_status.get("s3")]
    return not all(s == "Failed" for s in relays)


def port_map_from_hosts(host_map: dict) -> dict:
    """{port: field_id} derived from {field: [drone, ...]} — the single source
    of truth is host_map (drone dN sits on s1 port N), so the counter attribution
    never drifts from the deployer's TUNE targeting."""
    return {int(host[1:]): field
            for field, hosts in host_map.items() for host in hosts}


# ---------------- real node I/O ----------------

class NodeSampler:
    def __init__(self, runner, host_map, *, system_monitor=None,
                 edge_ip=config.EDGE_IP, ping_count=2):
        self.runner = runner
        self.host_map = host_map
        self.port_map = port_map_from_hosts(host_map)
        self.system = system_monitor or SystemMonitor()
        self.edge_ip = edge_ip
        self.ping_count = ping_count
        # ports we read counters/qdisc on: every field drone port + both relays.
        self.counter_ports = sorted(self.port_map) + [config.PORT_PRIMARY,
                                                       config.PORT_BACKUP]

    def clock(self) -> float:
        return time.monotonic()

    def ping(self, field: str) -> tuple:
        """RTT/loss from the field's first drone (drone ns -> edge). `|| true`
        because ping exits non-zero on loss — which is data, not a failure the
        runner should raise on. A namespace that briefly can't be resolved (e.g.
        just after a switch restart) raises RunnerError -> report unreachable so
        one bad probe never aborts the episode (design §5.6 hysteresis absorbs it)."""
        drone = self.host_map[field][0]
        try:
            out = self.runner.run_host(
                drone, f"ping -c {self.ping_count} -W 1 {self.edge_ip} || true")
        except RunnerError:
            return (None, None)
        return parse_ping(out)

    def counters(self) -> dict:
        """{port: CounterSample} for all drone + relay ports (switch-side veths,
        root ns — read directly, no mnexec). A veth that vanished (switch down)
        is dropped from the dict — the aggregators tolerate missing ports."""
        t = self.clock()
        out = {}
        for p in self.counter_ports:
            try:
                out[p] = read_interface_counters(f"s1-eth{p}", t)
            except OSError:
                pass
        return out

    def qdiscs(self) -> dict:
        """{link: parse_qdisc} for the UNMANAGED switch-side impairment links
        (the scenario env lives here, Issue-2) -> exogenous-shift detection.
        Includes BOTH the field drone links AND the relay links (s1-eth11/12):
        a path-quality fault degrades a relay link, not a drone link, and rung-3
        must see that as exogenous or it would wrongly roll back instead of
        rerouting. The deployer never writes any of these qdiscs."""
        out = {}
        for p in list(self.port_map) + [config.PORT_PRIMARY, config.PORT_BACKUP]:
            intf = f"s1-eth{p}"
            out[intf] = parse_qdisc(_root_tc(intf))
        return out

    def liveness(self) -> dict:
        return self.system.liveness()


def _root_tc(intf: str) -> str:
    """`tc qdisc show dev <intf>` in the root namespace (switch-side veth).
    Bounded + non-raising: a hung or failed tc yields "" (no shift detected)
    rather than blocking or crashing the monitor loop."""
    try:
        return subprocess.run(["sudo", "tc", "qdisc", "show", "dev", intf],
                              capture_output=True, text=True, timeout=5).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


# ---------------- orchestration ----------------

class NetworkMonitor:
    def __init__(self, sampler, requirements: dict, target_field: str,
                 correlation_id: str, *, k=config.HYSTERESIS_K,
                 m=config.HYSTERESIS_M):
        self.sampler = sampler
        self.requirements = requirements
        self.target_field = target_field
        self.correlation_id = correlation_id
        self.k, self.m = k, m
        self.port_map = getattr(sampler, "port_map", None) or {}
        self.baseline: dict | None = None          # {field: FlowMetrics}
        self.baseline_qdisc: dict = {}

    # --- one probe = counters around a ping burst ---

    def _probe(self):
        c0 = self.sampler.counters()
        pings = {f: self.sampler.ping(f) for f in self.requirements}
        c1 = self.sampler.counters()
        try:
            tput = aggregate_field_throughput(c0, c1, self.port_map)
        except ValueError:                       # degenerate dt — no rate this probe
            tput = {}
        probe = {}
        for f in self.requirements:
            rtt, loss = pings[f]
            # unreachable: None rtt reads as a latency violation (not a perfect
            # 0 ms), None loss as full loss — so a dead path is never "healthy".
            probe[f] = (rtt if rtt is not None else _UNREACHABLE_RTT_MS,
                        tput.get(f, 0.0),
                        loss if loss is not None else 100.0)
        return probe, c0, c1

    def _sample_window(self):
        samples, first_c, last_c = [], None, None
        for _ in range(self.m):
            probe, c0, c1 = self._probe()
            samples.append(probe)
            first_c = first_c or c0
            last_c = c1
        return samples, first_c, last_c

    # --- per-field raw met for hysteresis ---

    def _smoothed(self, samples: list) -> dict:
        out = {}
        for f, req in self.requirements.items():
            tr = HysteresisTracker(self.k, self.m)
            for s in samples:
                tr.update(compute_flow_metrics(f, *s[f], req).met)
            out[f] = tr.met
        return out

    # --- switch status from window-level relay throughput + liveness ---

    def _switch_status(self, first_c, last_c, smoothed: dict) -> dict:
        live = self.sampler.liveness()
        target_ok = smoothed.get(self.target_field, False)
        carrying = {"s1": False, "s2": False, "s3": False}
        for port, switch in _RELAY_PORT_SWITCH.items():
            carrying[switch] = _safe_rate((first_c, last_c), port) > _TRAFFIC_FLOOR_MBPS
        carrying["s1"] = carrying["s2"] or carrying["s3"]
        status = {}
        for sw, (alive, thrift) in live.items():
            status[sw] = derive_switch_status(alive, thrift,
                                              carrying.get(sw, False), target_ok)
        return status

    def _path_confidence(self, first_c, last_c) -> dict:
        conf = {}
        for port, switch in _RELAY_PORT_SWITCH.items():
            path = PRIMARY if port == config.PORT_PRIMARY else BACKUP
            moved = _safe_rate((first_c, last_c), port) > _TRAFFIC_FLOOR_MBPS
            conf[path] = "observed" if moved else "inferred"
        return conf

    # --- baseline (design §5.3) ---

    def capture_baseline(self) -> None:
        samples, _, _ = self._sample_window()
        raw = summarize_window(samples)
        self.baseline = {f: compute_flow_metrics(f, *raw[f], req)
                         for f, req in self.requirements.items()}
        self.baseline_qdisc = self.sampler.qdiscs()

    def rebaseline(self) -> None:
        """§7.6: on commit the current state becomes the new baseline."""
        self.capture_baseline()

    def baseline_snapshot(self, correlation_id: str, switch_status: dict,
                          timestamp: str):
        """The current baseline as a KG-persistable BaselineSnapshot (design
        §5.3), or None if no baseline has been captured yet."""
        if self.baseline is None:
            return None
        per_flow = {f: {"rtt_avg_ms": fm.rtt_avg_ms,
                        "throughput_mbps": fm.throughput_mbps,
                        "loss_percent": fm.loss_percent}
                    for f, fm in self.baseline.items()}
        return BaselineSnapshot(correlation_id, per_flow, dict(switch_status),
                                self.m, timestamp)

    # --- the protocol surface the loop consumes ---

    def observe_window(self):
        if self.baseline is None:
            self.capture_baseline()
        samples, first_c, last_c = self._sample_window()
        raw = summarize_window(samples)
        smoothed = self._smoothed(samples)
        switch_status = self._switch_status(first_c, last_c, smoothed)
        system_sound = _system_sound(switch_status)
        exogenous = qdisc_shifted(self.baseline_qdisc, self.sampler.qdiscs())
        return assemble_report(
            correlation_id=self.correlation_id, target_field=self.target_field,
            raw=raw, requirements=self.requirements, baseline=self.baseline,
            switch_status=switch_status, system_sound=system_sound,
            exogenous_shift=exogenous,
            path_confidence=self._path_confidence(first_c, last_c),
            smoothed_met=smoothed)
