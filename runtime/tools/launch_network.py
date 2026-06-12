"""Persistent Mininet/BMv2 topology holder (design §10.6; M0 spike + M4 node).

Brings up the milestone-II multihop topology and STAYS RESIDENT — no teardown
after install. The Runtime Manager then mutates switches out-of-band via
simple_switch_CLI (thrift 9090/9091/9092) and host qos via mnexec; this
process only owns the network's lifetime.

Run on network-node (paths per the existing milestone-II layout):

    sudo python3 launch_network.py \
        --p4-json  /home/cc/netprompt-milestone-II/compiled_p4/low_latency.json \
        --rules-dir /home/cc/netprompt-milestone-II/p4_multihop_rules \
        --sfc low_latency  [--scenario baseline]  [--cli]

--cli drops into the Mininet CLI (handy for the spike); without it the
process blocks until SIGINT/SIGTERM, then stops the net cleanly.

Topology mirrors dynamic_sfc_p4_multihop_experiment.py exactly:
  s1 access  (ports 1-10 = d1..d10, port 11 -> s2, port 12 -> s3)
  s2 primary relay -> edge          s3 backup relay -> edge
  edge = 10.0.0.100
"""
import argparse
import os
import signal
import subprocess
import threading
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import Switch

THRIFT = {"s1": 9090, "s2": 9091, "s3": 9092}

# Same scenario link-shape table as the milestone-II experiment.
SCENARIOS = {
    "low_latency":       {"bw": 80, "delay": "5ms",  "loss": 0},
    "baseline":          {"bw": 40, "delay": "15ms", "loss": 1},
    "congestion":        {"bw": 20, "delay": "25ms", "loss": 2},
    "ddil":              {"bw": 5,  "delay": "80ms", "loss": 5},
    "relay_failure":     {"bw": 30, "delay": "35ms", "loss": 4},
    "battery_depletion": {"bw": 15, "delay": "30ms", "loss": 2},
}


class P4Switch(Switch):
    """Identical mechanics to the milestone-II experiment's P4Switch."""

    def __init__(self, name, sw_path="simple_switch", json_path=None,
                 thrift_port=9090, device_id=0, **kwargs):
        Switch.__init__(self, name, **kwargs)
        self.sw_path = sw_path
        self.json_path = json_path
        self.thrift_port = thrift_port
        self.device_id = device_id

    def start(self, controllers):
        if self.json_path is None:
            raise Exception("P4 JSON path required")
        port_args, port_num = [], 1
        for intf in self.intfList():
            if intf.name != "lo":
                port_args.extend(["-i", f"{port_num}@{intf.name}"])
                port_num += 1
        nanolog = f"ipc:///tmp/bmv2-{self.name}-notifications.ipc"
        cmd = [self.sw_path, "--device-id", str(self.device_id),
               "--thrift-port", str(self.thrift_port),
               "--nanolog", nanolog, *port_args, self.json_path,
               f"> /tmp/{self.name}.log 2>&1 &"]
        self.cmd(" ".join(cmd))
        time.sleep(3)

    def stop(self):
        self.cmd("kill %simple_switch")
        Switch.stop(self)


def wait_for_thrift(port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        if subprocess.run(f"nc -z 127.0.0.1 {port}", shell=True,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return True
        time.sleep(1)
    return False


def install_rules(rules_file, port):
    if not wait_for_thrift(port):
        raise RuntimeError(f"thrift port {port} not reachable; see /tmp/s*.log")
    subprocess.run(f"simple_switch_CLI --thrift-port {port} < {rules_file}",
                   shell=True, check=True)


def build_net(p4_json, scenario):
    cfg = SCENARIOS[scenario]
    net = Mininet(switch=P4Switch, link=TCLink, controller=None,
                  autoSetMacs=True, autoStaticArp=True)
    s1 = net.addSwitch("s1", json_path=p4_json, thrift_port=THRIFT["s1"], device_id=1)
    s2 = net.addSwitch("s2", json_path=p4_json, thrift_port=THRIFT["s2"], device_id=2)
    s3 = net.addSwitch("s3", json_path=p4_json, thrift_port=THRIFT["s3"], device_id=3)

    drones = [net.addHost(f"d{i}", ip=f"10.0.0.{i}/24") for i in range(1, 11)]
    edge = net.addHost("edge", ip="10.0.0.100/24")

    for i, d in enumerate(drones):       # s1 ports 1..10
        if i < 3:
            net.addLink(d, s1, bw=100, delay="5ms", loss=0)
        else:
            net.addLink(d, s1, bw=cfg["bw"], delay=cfg["delay"], loss=cfg["loss"])

    net.addLink(s1, s2, bw=60, delay="10ms", loss=0)    # s1 port 11 (primary)
    net.addLink(s2, edge, bw=200, delay="2ms", loss=0)
    net.addLink(s1, s3, bw=40, delay="15ms", loss=0)    # s1 port 12 (backup)
    net.addLink(s3, edge, bw=150, delay="3ms", loss=0)
    return net


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--p4-json", required=True)
    ap.add_argument("--rules-dir", required=True)
    ap.add_argument("--sfc", required=True,
                    help="rule-file prefix, e.g. low_latency or reliable_relay")
    ap.add_argument("--scenario", default="baseline", choices=sorted(SCENARIOS))
    ap.add_argument("--cli", action="store_true",
                    help="drop into the Mininet CLI instead of blocking")
    args = ap.parse_args()

    setLogLevel("info")
    os.system("rm -f /tmp/bmv2-*.ipc /tmp/bmv2-*-notifications.ipc")

    net = build_net(args.p4_json, args.scenario)
    net.start()
    try:
        time.sleep(2)
        for switch, suffix in [("s1", "s1"), ("s2", "s2"), ("s3", "s3")]:
            rules = os.path.join(args.rules_dir, f"{args.sfc}_{suffix}_rules.txt")
            print(f"Installing {rules} on {switch} (thrift {THRIFT[switch]})")
            install_rules(rules, THRIFT[switch])

        print("\nNetwork is up and PERSISTENT.")
        print("  thrift: s1=9090 s2=9091 s3=9092")
        print("  host namespaces: pgrep -f 'mininet:d4' -> mnexec -a <pid> <cmd>")
        print("  stop: Ctrl-C (or SIGTERM) stops the net cleanly\n")

        if args.cli:
            CLI(net)
        else:
            # Handler-based wait: signal.sigwait() without pre-blocking races
            # with Python's default SIGINT handling (KeyboardInterrupt would
            # bypass cleanup and orphan switches/namespaces).
            stop_evt = threading.Event()
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: stop_evt.set())
            stop_evt.wait()
            print("stop signal received, stopping network")
    finally:
        net.stop()


if __name__ == "__main__":
    main()
