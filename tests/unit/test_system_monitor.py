"""M5 — SystemMonitor liveness, with injected probes (off-node)."""
from runtime.monitors.system_monitor import SystemMonitor


def test_liveness_maps_each_switch_to_process_and_thrift():
    alive = {9090: True, 9091: True, 9092: False}      # s3 process gone
    thrift = {9090: True, 9091: False, 9092: False}    # s2 thrift down
    sm = SystemMonitor(thrift_ports={"s1": 9090, "s2": 9091, "s3": 9092},
                       process_probe=lambda p: alive[p],
                       thrift_probe=lambda p: thrift[p])
    assert sm.liveness() == {"s1": (True, True),
                             "s2": (True, False),       # degraded link to thrift
                             "s3": (False, False)}      # crashed -> Failed downstream
