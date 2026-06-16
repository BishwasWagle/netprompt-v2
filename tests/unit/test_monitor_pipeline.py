"""M5 (local half) — the monitor computation pipeline (design §5.2-§5.7)."""
import pytest

from runtime.contracts import Envelope, compute_flow_metrics
from runtime.fixtures import Healthy
from runtime.monitors.pipeline import (
    CounterSample, HysteresisTracker, aggregate_field_throughput,
    assemble_report, derive_switch_status, link_loss_percent, parse_ping,
    parse_qdisc, qdisc_shifted, read_interface_counters, summarize_window,
    throughput_mbps,
)

# ---------------- hysteresis ----------------

def test_single_bad_probe_does_not_flip():
    h = HysteresisTracker(k=3, m=5)
    assert h.update(True) and h.update(False) and h.update(True)
    assert h.met and h.state == "suspect"


def test_sustained_violation_flips_after_k_bad():
    h = HysteresisTracker(k=3, m=5)
    for _ in range(3):
        h.update(True)
    assert h.update(False) and h.update(False)      # 2 bad: still ok
    assert not h.update(False)                      # 3rd bad: violating
    assert h.state in ("violating", "recovering")


def test_recovery_needs_k_good():
    h = HysteresisTracker(k=3, m=5)
    for _ in range(5):
        h.update(False)
    assert not h.met
    assert not h.update(True) and not h.update(True)   # 2 good: still violating
    assert h.update(True)                               # 3rd good: ok
    assert h.met


def test_sparse_noise_never_flips():
    # 1 bad probe in every 5 (20% noise) never reaches 3-of-5 bad.
    h = HysteresisTracker(k=3, m=5)
    flips = [h.update(i % 5 != 0) for i in range(25)]
    assert all(flips)
    # Note: a 50% duty-cycle flap legitimately crosses 3-of-5 — that is a
    # half-violating flow, not noise, and SHOULD eventually trip the machine.


# ---------------- counters ----------------

def s(t, rx_b=0, tx_b=0, rx_p=0, tx_p=0):
    return CounterSample(t, rx_b, tx_b, rx_p, tx_p)


def test_throughput_from_byte_deltas():
    # 2.5 MB in 2s = 10 Mbit/s
    assert throughput_mbps(s(0, rx_b=0), s(2, rx_b=2_500_000)) == pytest.approx(10.0)


def test_throughput_rejects_bad_timestamps():
    with pytest.raises(ValueError):
        throughput_mbps(s(5, rx_b=0), s(5, rx_b=100))


def test_field_aggregation_sums_a_fields_drone_ports():
    port_map = {1: "F1", 2: "F1", 3: "F2"}           # d1,d2 -> F1; d3 -> F2
    prev = {p: s(0, rx_b=0) for p in port_map}
    cur = {1: s(1, rx_b=1_250_000), 2: s(1, rx_b=1_250_000), 3: s(1, rx_b=625_000)}
    rates = aggregate_field_throughput(prev, cur, port_map)
    assert rates["F1"] == pytest.approx(20.0)        # 10 + 10
    assert rates["F2"] == pytest.approx(5.0)


def test_link_loss_from_end_to_end_packet_deltas():
    loss = link_loss_percent(s(0, tx_p=0), s(1, tx_p=1000),    # 1000 sent
                             s(0, rx_p=0), s(1, rx_p=960))     # 960 arrived
    assert loss == pytest.approx(4.0)
    assert link_loss_percent(s(0, tx_p=0), s(1, tx_p=0),
                             s(0, rx_p=0), s(1, rx_p=0)) == 0.0


def test_read_interface_counters_from_sysfs_layout(tmp_path):
    stats = tmp_path / "s1-eth1" / "statistics"
    stats.mkdir(parents=True)
    for name, val in [("rx_bytes", 123), ("tx_bytes", 456),
                      ("rx_packets", 7), ("tx_packets", 8)]:
        (stats / name).write_text(f"{val}\n")
    c = read_interface_counters("s1-eth1", 1.5, base=str(tmp_path))
    assert (c.rx_bytes, c.tx_bytes, c.rx_packets, c.tx_packets) == (123, 456, 7, 8)
    assert c.timestamp == 1.5


# ---------------- parsers ----------------

PING_OK = """PING 10.0.0.100 (10.0.0.100) 56(84) bytes of data.
64 bytes from 10.0.0.100: icmp_seq=1 ttl=64 time=12.4 ms

--- 10.0.0.100 ping statistics ---
10 packets transmitted, 10 received, 0% packet loss, time 9012ms
rtt min/avg/max/mdev = 10.211/12.342/15.000/1.200 ms
"""

PING_LOSSY = """--- 10.0.0.100 ping statistics ---
10 packets transmitted, 8 received, 20% packet loss, time 9012ms
rtt min/avg/max/mdev = 40.1/45.5/60.0/5.0 ms
"""


def test_parse_ping_clean_and_lossy():
    assert parse_ping(PING_OK) == (12.342, 0.0)
    assert parse_ping(PING_LOSSY) == (45.5, 20.0)
    assert parse_ping("no output at all") == (None, None)


def test_parse_qdisc_variants():
    netem = "qdisc netem 8001: root refcnt 2 limit 1000 delay 25.0ms loss 2%"
    tbf = "qdisc tbf 8002: root refcnt 2 rate 5Mbit burst 8Kb lat 100.0ms"
    pfifo = "qdisc pfifo 8003: root refcnt 2 limit 20p"
    assert parse_qdisc(netem) == {"delay_ms": 25.0, "loss_percent": 2.0,
                                  "limit_pkts": 1000.0}
    assert parse_qdisc(tbf) == {"rate_mbit": 5.0}
    assert parse_qdisc(pfifo) == {"limit_pkts": 20.0}


# ---------------- exogenous shift ----------------

def test_qdisc_shift_detection():
    base = {"s1-s2": {"delay_ms": 10.0, "loss_percent": 0.0}}
    same = {"s1-s2": {"delay_ms": 10.0, "loss_percent": 0.0}}
    drift = {"s1-s2": {"delay_ms": 11.0, "loss_percent": 0.0}}   # 10% — within tol
    shifted = {"s1-s2": {"delay_ms": 80.0, "loss_percent": 0.0}}  # ddil knob turned
    assert not qdisc_shifted(base, same)
    assert not qdisc_shifted(base, drift)
    assert qdisc_shifted(base, shifted)


def test_qdisc_param_appearing_is_a_shift():
    base = {"s1-s2": {"delay_ms": 10.0}}
    cur = {"s1-s2": {"delay_ms": 10.0, "loss_percent": 4.0}}     # loss injected
    assert qdisc_shifted(base, cur)


# ---------------- status derivation (replaces SCENARIO_STATE) ----------------

@pytest.mark.parametrize("alive,thrift,carrying,sla_ok,expected", [
    (False, True, True, True, "Failed"),
    (True, False, True, True, "Failed"),
    (True, True, False, True, "Standby"),
    (True, True, True, True, "Active"),
    (True, True, True, False, "Degraded"),
])
def test_derive_switch_status(alive, thrift, carrying, sla_ok, expected):
    assert derive_switch_status(alive, thrift, carrying, sla_ok) == expected


# ---------------- window summary ----------------

def test_summarize_window_averages_per_field():
    window = [{"F1": (10.0, 50.0, 0.0), "F2": (20.0, 30.0, 2.0)},
              {"F1": (14.0, 46.0, 1.0), "F2": (24.0, 26.0, 0.0)}]
    avg = summarize_window(window)
    assert avg["F1"] == pytest.approx((12.0, 48.0, 0.5))
    assert avg["F2"] == pytest.approx((22.0, 28.0, 1.0))
    with pytest.raises(ValueError):
        summarize_window([])


# ---------------- assembly ----------------

REQ = Envelope(max_latency_ms=20, min_bandwidth_mbps=40, max_loss_percent=2)


def test_assembler_matches_fixture_reports():
    """The fixtures delegate to assemble_report — one assembly semantics."""
    m = Healthy()
    r = m.report({"path": "primary", "knobs": {}})
    assert r.target_sla_met and r.displaced_harm == [] and r.headroom > 0


def test_smoothed_met_overrides_verdict_but_not_margin():
    baseline = {"F1": compute_flow_metrics("F1", 10, 55, 0.5, REQ)}
    raw = {"F1": (10, 55, 0.5)}                       # raw says met
    r = assemble_report(correlation_id="t", target_field="F1", raw=raw,
                        requirements={"F1": REQ}, baseline=baseline,
                        switch_status={}, smoothed_met={"F1": False})
    assert not r.target_sla_met                       # hysteresis says violating
    assert r.target.margin > 0                        # margin stays raw
    assert r.headroom == 0.0                          # no satisfied flows


def test_parse_ping_clamps_negative_loss_on_duplicates():
    # duplicate replies (rx > tx) must NOT yield negative loss (review C2)
    assert parse_ping("10 packets transmitted, 12 received, +2 duplicates")[1] == 0.0
    assert parse_ping("10 packets transmitted, 8 received, 20% loss")[1] == 20.0


def test_parse_qdisc_normalizes_rate_units():
    assert parse_qdisc("rate 5Mbit")["rate_mbit"] == 5.0
    assert parse_qdisc("rate 500Kbit")["rate_mbit"] == 0.5     # was invisible (review C4)
    assert parse_qdisc("rate 1Gbit")["rate_mbit"] == 1000.0    # was invisible
