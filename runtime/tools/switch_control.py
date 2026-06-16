"""Single-switch kill / restart on the resident topology.

Two uses: the **M6 watchdog** primitive (a BMv2 process can die under churn —
M0 finding — and restarting it + reinstalling its rules is a rung-1 self-heal),
and the recovery mechanism the M5 induced-fault tests use to put `s2` back after
killing it.

No Mininet dependency — the switch veths live in the root namespace, so a switch
is just `simple_switch -i port@<name>-ethK … <json>` plus a rules re-install over
thrift. pid lookup is by command name (`pgrep -x simple_switch`) + a /proc
cmdline check, so it never self-matches the caller's command line.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time

from runtime import config

_DEVICE_ID = {"s1": 1, "s2": 2, "s3": 3}


def switch_pids(name: str) -> list:
    """ALL simple_switch pids for `name` (matched by binary name + the device id
    in /proc, so the caller's own argv can't match). Usually one — but a failed
    restart can orphan a duplicate on the same thrift port, so callers must see
    and reap every one."""
    out = subprocess.run(["pgrep", "-x", "simple_switch"],
                         capture_output=True, text=True, timeout=10).stdout
    needle = f"--device-id {_DEVICE_ID[name]} "
    pids = []
    for pid in out.split():
        try:
            with open(f"/proc/{pid}/cmdline") as fh:
                cmd = fh.read().replace("\0", " ")
        except OSError:
            continue
        if needle in cmd:
            pids.append(int(pid))
    return pids


def switch_pid(name: str) -> int | None:
    pids = switch_pids(name)
    return pids[0] if pids else None


def _thrift_up(port: int, timeout: float = 1.0) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def kill_switch(name: str, *, deadline: float = 5.0) -> None:
    """SIGKILL EVERY simple_switch for this device and WAIT until they are all
    gone — so a restart can rebind the thrift port / nanolog IPC without racing
    a corpse, and so orphaned duplicates from a prior failed restart are reaped."""
    pids = switch_pids(name)
    if not pids:
        return
    for pid in pids:
        subprocess.run(["sudo", "kill", "-9", str(pid)], timeout=10)
    waited = 0.0
    while switch_pids(name) and waited < deadline:
        time.sleep(0.2)
        waited += 0.2


def _interfaces(name: str) -> list:
    """(port_number, intf) pairs from the switch's veths, port taken from the
    veth's own suffix (s1-eth11 -> port 11) so the mapping reproduces
    launch_network's exactly even when numbering is non-contiguous."""
    eths = [i for i in os.listdir("/sys/class/net") if i.startswith(f"{name}-eth")]
    return sorted((int(i.rsplit("eth", 1)[-1]), i) for i in eths)


def restart_switch(name: str, p4_json: str, rules_file: str, *,
                   deadline: float = 12.0, attempts: int = 3) -> bool:
    """Relaunch the switch on its existing veths (port = the veth suffix), wait
    for thrift, then reinstall `rules_file`. Returns whether it came back up.

    RETRIES (M6 finding): a single relaunch is flaky — the thrift port can be in
    TIME_WAIT just after the kill, or BMv2 can crash on a churned host — and a
    relaunch that doesn't bind in time ORPHANS a duplicate on the same port. So
    each attempt first reaps EVERY existing process for this device, then waits
    longer for thrift, and we retry a few times.

    CAVEAT (M6): `rules_file` is a STATIC ruleset — fine for recovering to a known
    baseline (the M5 tests), but a mid-episode watchdog must reinstall the CURRENT
    config (the deployer's last_good ConfigSnapshot via _restore_tables), or it
    silently reverts a committed reroute/tune. Do not wire this raw into the loop."""
    port = config.THRIFT_PORTS[name]
    port_args = []
    for port_num, intf in _interfaces(name):
        port_args += ["-i", f"{port_num}@{intf}"]
    cmd = ["sudo", "simple_switch", "--device-id", str(_DEVICE_ID[name]),
           "--thrift-port", str(port),
           "--nanolog", f"ipc:///tmp/bmv2-{name}-notifications.ipc",
           *port_args, p4_json]
    for _ in range(attempts):
        kill_switch(name)                            # reap orphans from a prior try
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        waited = 0.0
        while not _thrift_up(port) and waited < deadline:
            time.sleep(0.3)
            waited += 0.3
        if _thrift_up(port):
            if not os.path.exists(rules_file):
                break                                # nothing to reinstall -> fail
            r = subprocess.run(
                f"simple_switch_CLI --thrift-port {port} < {rules_file}",
                shell=True, capture_output=True, text=True, timeout=30)
            with open(rules_file) as fh:
                n_add = sum(1 for l in fh if l.strip().startswith("table_add"))
            # only count a recovery if EVERY rule re-installed (simple_switch_CLI
            # exits 0 on a per-line failure) — else retry, then fall through to
            # False so the caller never treats a half-configured switch as healed.
            if (r.returncode == 0 and
                    (r.stdout or "").count("Entry has been added with handle") == n_add):
                return True
    kill_switch(name)                                # don't leave a dud orphan
    return False
