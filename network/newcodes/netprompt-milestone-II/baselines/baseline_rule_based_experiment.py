from mininet.net import Mininet
from mininet.node import Switch
from mininet.link import TCLink
from mininet.log import setLogLevel
import argparse
import subprocess
import time
import os


BASE = "/home/cc/netprompt-milestone-II"

SCENARIOS = {
    "low_latency": {"bw": 80, "delay": "5ms", "loss": 0},
    "baseline": {"bw": 40, "delay": "15ms", "loss": 1},
    "congestion": {"bw": 20, "delay": "25ms", "loss": 2},
    "ddil": {"bw": 5, "delay": "80ms", "loss": 5},
    "relay_failure": {"bw": 30, "delay": "35ms", "loss": 4},
    "battery_depletion": {"bw": 15, "delay": "30ms", "loss": 2},
}


def select_sfc_if_else(scenario):
    if scenario == "battery_depletion":
        return "EnergyAwareSFC"
    elif scenario in ["congestion", "ddil", "relay_failure"]:
        return "ReliableRelaySFC"
    elif scenario == "baseline":
        return "BandwidthOptimizedSFC"
    else:
        return "LowLatencyVideoSFC"


def map_sfc_to_p4(sfc):
    mapping = {
        "LowLatencyVideoSFC": {
            "json": f"{BASE}/compiled_p4/low_latency.json",
            "rules": f"{BASE}/p4_rules/low_latency_rules.txt"
        },
        "ReliableRelaySFC": {
            "json": f"{BASE}/compiled_p4/reliable_relay.json",
            "rules": f"{BASE}/p4_rules/reliable_relay_rules.txt"
        },
        "EnergyAwareSFC": {
            "json": f"{BASE}/compiled_p4/energy_aware.json",
            "rules": f"{BASE}/p4_rules/energy_aware_rules.txt"
        },
        "BandwidthOptimizedSFC": {
            "json": f"{BASE}/compiled_p4/bandwidth_optimized.json",
            "rules": f"{BASE}/p4_rules/bandwidth_optimized_rules.txt"
        }
    }
    return mapping[sfc]


class P4Switch(Switch):
    def __init__(self, name, json_path=None, thrift_port=9090, **kwargs):
        Switch.__init__(self, name, **kwargs)
        self.json_path = json_path
        self.thrift_port = thrift_port

    def start(self, controllers):
        port_args = []
        port_num = 1

        for intf in self.intfList():
            if intf.name != "lo":
                port_args += ["-i", f"{port_num}@{intf.name}"]
                port_num += 1

        cmd = [
            "simple_switch",
            "--device-id", "2",
            "--thrift-port", str(self.thrift_port),
            *port_args,
            self.json_path,
            f"> /tmp/{self.name}_rule_based.log 2>&1 &"
        ]

        self.cmd(" ".join(cmd))
        time.sleep(2)

    def stop(self):
        self.cmd("kill %simple_switch")
        Switch.stop(self)


def install_rules(rule_file):
    subprocess.run(
        f"simple_switch_CLI --thrift-port 9090 < {rule_file}",
        shell=True,
        check=True
    )


def run_experiment(scenario):
    cfg = SCENARIOS[scenario]

    t_select_start = time.time()
    selected_sfc = select_sfc_if_else(scenario)
    p4_info = map_sfc_to_p4(selected_sfc)
    t_select_end = time.time()

    result_file = f"{BASE}/comparative_results/baseline_rule_based_{scenario}.txt"

    os.system("sudo mn -c > /dev/null 2>&1")
    os.system("sudo pkill -9 simple_switch 2>/dev/null")

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    s1 = net.addSwitch("s1", json_path=p4_info["json"])

    drones = []
    for i in range(1, 11):
        drones.append(net.addHost(f"d{i}", ip=f"10.0.0.{i}/24"))

    edge = net.addHost("edge", ip="10.0.0.100/24")

    for d in drones:
        net.addLink(d, s1, bw=cfg["bw"], delay=cfg["delay"], loss=cfg["loss"])

    net.addLink(edge, s1, bw=200, delay="2ms", loss=0)

    t0 = time.time()
    net.start()
    time.sleep(2)

    t_rule_start = time.time()
    install_rules(p4_info["rules"])
    t_rule_end = time.time()

    t_exp_start = time.time()

    pingall_loss = net.pingAll()

    edge.cmd("pkill -f iperf")
    edge.cmd("iperf -s > /tmp/rule_based_iperf_server.log 2>&1 &")
    time.sleep(1)

    d4 = net.get("d4")

    throughput = d4.cmd("iperf -c 10.0.0.100 -t 10")
    latency = d4.cmd("ping -c 10 10.0.0.100")

    t1 = time.time()

    with open(result_file, "w") as f:
        f.write("Method: Rule-Based No-KG\n")
        f.write(f"Scenario: {scenario}\n")
        f.write(f"Selected SFC: {selected_sfc}\n")
        f.write("KG Used: No\n")
        f.write("Path Reasoning Used: No\n")
        f.write(f"Configured Bandwidth Mbps: {cfg['bw']}\n")
        f.write(f"Configured Delay: {cfg['delay']}\n")
        f.write(f"Configured Loss Percent: {cfg['loss']}\n")
        f.write(f"SFC Selection Seconds: {t_select_end - t_select_start:.6f}\n")
        f.write(f"Rule Install Seconds: {t_rule_end - t_rule_start:.6f}\n")
        f.write(f"Experiment Seconds: {t1 - t_exp_start:.6f}\n")
        f.write(f"Total Seconds: {t1 - t0:.6f}\n")
        f.write(f"PingAll Dropped Percent: {pingall_loss}\n\n")
        f.write("Throughput Output:\n")
        f.write(throughput)
        f.write("\nLatency Output:\n")
        f.write(latency)

    print(f"[OK] Saved {result_file}")

    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()
    run_experiment(args.scenario)
