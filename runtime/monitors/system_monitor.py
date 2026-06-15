"""System-health sampler (M5, design §5.5).

Per-switch process + thrift liveness — the inputs `pipeline.derive_switch_status`
needs alongside the network monitor's traffic/SLA observations. This replaces the
old hardcoded SCENARIO_STATE: switch status becomes observed, not declared.

The two probes are injectable so the logic is unit-tested off-node; the defaults
do the real checks (pgrep for the process, a TCP connect for thrift).
"""
from __future__ import annotations

import socket
import subprocess

from runtime import config


def _process_alive(thrift_port: int) -> bool:
    """A simple_switch is running for this thrift port (matches launch_network's
    `--thrift-port N` arg)."""
    out = subprocess.run(["pgrep", "-f", f"thrift-port {thrift_port}"],
                         capture_output=True, text=True).stdout
    return bool(out.strip())


def _thrift_reachable(thrift_port: int, host: str = "127.0.0.1",
                      timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, thrift_port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class SystemMonitor:
    def __init__(self, thrift_ports: dict | None = None, *,
                 process_probe=_process_alive, thrift_probe=_thrift_reachable):
        self.thrift_ports = dict(thrift_ports or config.THRIFT_PORTS)
        self._proc = process_probe
        self._thrift = thrift_probe

    def liveness(self) -> dict:
        """{switch: (process_alive, thrift_reachable)} — a dead/crashed BMv2
        (seen under churn with no watchdog) surfaces here as (False, False),
        which derive_switch_status maps to 'Failed' -> rung-1 system fault."""
        return {sw: (self._proc(port), self._thrift(port))
                for sw, port in self.thrift_ports.items()}
