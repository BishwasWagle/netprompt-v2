from mininet.net import Mininet
from mininet.node import Switch
from mininet.link import TCLink
from mininet.log import setLogLevel
from pathlib import Path
import subprocess
import time
import os


BASE = "/home/cc/netprompt-milestone-II"
OUT = Path(f"{BASE}/debug_results/05_multihop_low_latency_iperf_debug.log")


class P4Switch(Switch):
    def __init__(self, name, json_path=None, thrift_port=9090, device_id=0, **kwargs):
        Switch.__init__(self, name, **kwargs)
        self.json_path = json_path
        self.thrift_port = thrift_port
        self.device_id = device_id

    def start(self, controllers):
        port_args = []
        port_num = 1

        for intf in self.intfList():
            if intf.name != "lo":
                port_args.extend(["-i", f"{port_num}@{intf.name}"])
                port_num += 1

        cmd = [
            "simple_switch",
            "--device-id", str(self.device_id),
            "--thrift-port", str(self.thrift_port),
            *port_args,
            self.json_path,
            f"> /tmp/{self.name}_debug.log 2>&1 &"
        ]

        self.cmd(" ".join(cmd))
        time.sleep(2)

    def stop(self):
        self.cmd("kill %simple_switch")
        Switch.stop(self)


def write(label, value):
    with OUT.open("a") as f:
        f.write(f"\n=== {label} ===\n")
        f.write(str(value))
        f.write("\n")


def install_rules(rule_file, thrift_port):
    cmd = f"simple_switch_CLI --thrift-port {thrift_port} < {rule_file}"
    result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
    write(f"install rules {rule_file} port {thrift_port} stdout", result.stdout)
    write(f"install rules {rule_file} port {thrift_port} stderr", result.stderr)
    return result.returncode


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()

    os.system("sudo mn -c > /dev/null 2>&1")
    os.system("sudo pkill -9 simple_switch 2>/dev/null || true")

    p4_json = f"{BASE}/compiled_p4/low_latency.json"
    s1_rules = f"{BASE}/p4_multihop_rules/low_latency_s1_rules.txt"
    s2_rules = f"{BASE}/p4_multihop_rules/low_latency_s2_rules.txt"
    s3_rules = f"{BASE}/p4_multihop_rules/low_latency_s3_rules.txt"

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    s1 = net.addSwitch("s1", json_path=p4_json, thrift_port=9090, device_id=1)
    s2 = net.addSwitch("s2", json_path=p4_json, thrift_port=9091, device_id=2)
    s3 = net.addSwitch("s3", json_path=p4_json, thrift_port=9092, device_id=3)

    drones = []
    for i in range(1, 11):
        drones.append(net.addHost(f"d{i}", ip=f"10.0.0.{i}/24"))

    edge = net.addHost("edge", ip="10.0.0.100/24")

    for d in drones:
        net.addLink(d, s1, bw=80, delay="5ms", loss=0)

    net.addLink(s1, s2, bw=60, delay="10ms", loss=0)
    net.addLink(s1, s3, bw=40, delay="15ms", loss=0)
    net.addLink(s2, edge, bw=200, delay="2ms", loss=0)
    net.addLink(s3, edge, bw=150, delay="3ms", loss=0)

    net.start()
    time.sleep(2)

    install_rules(s1_rules, 9090)
    install_rules(s2_rules, 9091)
    install_rules(s3_rules, 9092)

    time.sleep(2)

    d4 = net.get("d4")
    d5 = net.get("d5")
    d6 = net.get("d6")
    edge = net.get("edge")

    write("interfaces d4", d4.cmd("ip addr"))
    write("interfaces edge", edge.cmd("ip addr"))
    write("route d4", d4.cmd("ip route"))
    write("route edge", edge.cmd("ip route"))
    write("arp d4 before", d4.cmd("arp -n"))
    write("arp edge before", edge.cmd("arp -n"))

    write("ping d4 -> edge", d4.cmd("ping -c 5 10.0.0.100"))
    write("ping edge -> d4", edge.cmd("ping -c 5 10.0.0.4"))

    write("arp d4 after ping", d4.cmd("arp -n"))
    write("arp edge after ping", edge.cmd("arp -n"))

    edge.cmd("pkill -f 'iperf -s' || true")
    time.sleep(1)

    edge.cmd("iperf -s -p 5001 > /tmp/edge_iperf_server_debug.log 2>&1 &")
    time.sleep(2)

    write("edge iperf process", edge.cmd("ps aux | grep '[i]perf -s' || true"))
    write("edge listen 5001", edge.cmd("ss -ltnp | grep 5001 || netstat -ltnp 2>/dev/null | grep 5001 || true"))
    write("edge iperf server log before", edge.cmd("cat /tmp/edge_iperf_server_debug.log || true"))

    write("tcp check d4 -> edge", d4.cmd("timeout 5 bash -c '</dev/tcp/10.0.0.100/5001' && echo TCP_OK || echo TCP_FAIL"))
    write("tcp check d5 -> edge", d5.cmd("timeout 5 bash -c '</dev/tcp/10.0.0.100/5001' && echo TCP_OK || echo TCP_FAIL"))
    write("tcp check d6 -> edge", d6.cmd("timeout 5 bash -c '</dev/tcp/10.0.0.100/5001' && echo TCP_OK || echo TCP_FAIL"))

    write("iperf d4 -> edge", d4.cmd("iperf -c 10.0.0.100 -p 5001 -t 5"))
    write("iperf d5 -> edge", d5.cmd("iperf -c 10.0.0.100 -p 5001 -t 5"))
    write("iperf d6 -> edge", d6.cmd("iperf -c 10.0.0.100 -p 5001 -t 5"))

    write("edge iperf server log after", edge.cmd("cat /tmp/edge_iperf_server_debug.log || true"))

    net.stop()

    print(f"[OK] Saved {OUT}")


if __name__ == "__main__":
    setLogLevel("info")
    main()
