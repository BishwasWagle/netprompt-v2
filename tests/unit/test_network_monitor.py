"""M5 — NetworkMonitor orchestration (window, hysteresis, status, assembly)
tested off-node with a programmable FakeSampler. The pipeline primitives are
unit-tested separately (test_monitor_pipeline.py); here we pin that the monitor
wires them into a correct MonitorReport."""
from runtime.contracts import BACKUP, Envelope, PRIMARY
from runtime.monitors.network_monitor import NetworkMonitor, port_map_from_hosts
from runtime.monitors.pipeline import CounterSample

REQ = {"F1": Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2),
       "F2": Envelope(max_latency_ms=50, min_bandwidth_mbps=20, max_loss_percent=3)}


class FakeSampler:
    """Programmable I/O. Counters are synthesized so each port's throughput
    equals a configured mbps rate (bytes = rate*1e6*t/8 -> dt cancels)."""
    def __init__(self):
        self.port_map = {4: "F1", 5: "F1", 7: "F2"}
        self.port_rates = {4: 25.0, 5: 25.0, 7: 25.0, 11: 100.0, 12: 0.0}
        self.ping_values = {"F1": (10.0, 0.0), "F2": (25.0, 1.0)}
        self.qdisc = {"s1-eth4": {"delay_ms": 15.0, "loss_percent": 1.0}}
        self.live = {"s1": (True, True), "s2": (True, True), "s3": (True, True)}
        self.drop_ports = set()        # simulate veths that vanished (switch down)
        self._t = 0.0

    def clock(self):
        self._t += 1.0
        return self._t

    def ping(self, field):
        return self.ping_values[field]

    def counters(self):
        t = self.clock()
        out = {}
        for p, rate in self.port_rates.items():
            if p in self.drop_ports:
                continue
            b = int(rate * 1e6 * t / 8)
            out[p] = CounterSample(t, b, b, b, b)
        return out

    def qdiscs(self):
        return {k: dict(v) for k, v in self.qdisc.items()}

    def liveness(self):
        return dict(self.live)


def monitor(sampler=None, target="F1"):
    s = sampler or FakeSampler()
    m = NetworkMonitor(s, REQ, target, "ep1", k=2, m=3)
    return m, s


# ---------------- derivation ----------------

def test_port_map_from_hosts_derives_from_host_map():
    assert port_map_from_hosts({"F1": ["d4", "d5", "d6"],
                                "F2": ["d7", "d10"]}) == \
        {4: "F1", 5: "F1", 6: "F1", 7: "F2", 10: "F2"}


# ---------------- healthy report ----------------

def test_observe_window_healthy_report():
    m, s = monitor()
    r = m.observe_window()                              # lazily captures baseline
    assert r.target_sla_met is True
    assert r.displaced_harm == []
    assert r.exogenous_shift is False
    # s1 + s2 carry traffic (port 11 active), s3 idle -> Standby
    assert r.switch_status["s1"] == "Active"
    assert r.switch_status["s2"] == "Active"
    assert r.switch_status["s3"] == "Standby"
    assert r.path_confidence == {PRIMARY: "observed", BACKUP: "inferred"}
    assert r.headroom > 0


# ---------------- hysteresis smoothing ----------------

def test_target_violation_is_smoothed_to_unmet():
    m, s = monitor()
    m.capture_baseline()
    s.ping_values["F1"] = (45.0, 0.0)                   # latency blows the 20ms bound
    r = m.observe_window()
    assert r.target_sla_met is False                   # sustained over the window


# ---------------- harm detection vs baseline ----------------

def test_harm_lists_neighbor_that_flips_met_to_unmet():
    m, s = monitor()
    m.capture_baseline()                               # F2 met at baseline
    s.ping_values["F2"] = (25.0, 9.0)                  # 9% loss > F2's 3% bound
    r = m.observe_window()
    assert "F2" in r.displaced_harm
    assert r.target_sla_met is True                    # target itself still fine


# ---------------- exogenous shift ----------------

def test_exogenous_shift_when_env_qdisc_changes():
    m, s = monitor()
    m.capture_baseline()
    s.qdisc["s1-eth4"]["delay_ms"] = 60.0              # env impairment jumped
    assert m.observe_window().exogenous_shift is True


# ---------------- switch liveness -> Failed ----------------

def test_single_relay_death_is_failed_but_still_sound():
    """A dead relay is Failed in switch_status, but the fabric can still carry
    traffic via the other relay -> sound, so the loop reroutes (rung 4) instead
    of bailing with system_fault."""
    m, s = monitor()
    m.capture_baseline()
    s.live["s2"] = (False, False)                      # primary relay crashed
    r = m.observe_window()
    assert r.switch_status["s2"] == "Failed"
    assert r.system_sound is True                      # recoverable via s3


def test_dead_s1_is_system_fault():
    """s1 is always on the path: its death breaks the whole fabric -> unsound
    (the case the old 'any switch alive' rule wrongly called sound)."""
    m, s = monitor()
    m.capture_baseline()
    s.live["s1"] = (False, False)
    assert m.observe_window().system_sound is False


def test_both_relays_dead_is_system_fault():
    m, s = monitor()
    m.capture_baseline()
    s.live["s2"] = (False, False)
    s.live["s3"] = (False, False)
    assert m.observe_window().system_sound is False


# ---------------- rebaseline ----------------

def test_unreachable_ping_reads_as_violation_not_perfect():
    """A None rtt (unreachable / unparseable ping) must NOT read as a perfect
    0 ms — it maps to a latency violation + full loss, so a dead path is unmet."""
    m, s = monitor()
    m.capture_baseline()
    s.ping_values["F1"] = (None, None)                 # ping totally failed
    r = m.observe_window()
    assert r.target_sla_met is False
    assert r.target.rtt_avg_ms >= 100                  # not 0.0
    assert r.target.loss_percent == 100.0


def test_missing_counter_ports_do_not_crash():
    """A veth that vanished (switch down) drops out of the counter dict; the
    window still completes (throughput 0 for that field, no exception)."""
    m, s = monitor()
    m.capture_baseline()
    s.drop_ports = {4, 5, 11, 12}                      # F1 ports + both relays gone
    r = m.observe_window()                             # must not raise
    assert r.target.throughput_mbps == 0.0
    assert r.switch_status["s2"] == "Standby"          # no relay traffic seen


def test_safe_rate_handles_degenerate_and_missing():
    from runtime.monitors.network_monitor import _safe_rate
    same_ts = {1: CounterSample(5.0, 100, 100, 1, 1)}
    assert _safe_rate((same_ts, same_ts), 1) == 0.0    # dt == 0 -> 0, no raise
    assert _safe_rate(({}, {}), 1) == 0.0              # missing port -> 0


def test_rebaseline_resets_harm_reference():
    m, s = monitor()
    m.capture_baseline()
    s.ping_values["F2"] = (25.0, 9.0)                  # F2 now bad
    assert "F2" in m.observe_window().displaced_harm   # harm vs old baseline
    m.rebaseline()                                     # accept current as normal
    assert m.observe_window().displaced_harm == []     # no longer "harm"
