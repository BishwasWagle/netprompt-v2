from mininet.net import Mininet
from mininet.node import Switch
from mininet.link import TCLink
from mininet.log import setLogLevel
import subprocess
import time
import os
import argparse


class P4Switch(Switch):
    def __init__(
        self,
        name,
        sw_path="simple_switch",
        json_path=None,
        thrift_port=9090,
        **kwargs
    ):
        Switch.__init__(self, name, **kwargs)
        self.sw_path = sw_path
        self.json_path = json_path
        self.thrift_port = thrift_port

    def start(self, controllers):
        if self.json_path is None:
            raise Exception("P4 JSON path required")

        port_args = []
        port_num = 1

        for intf in self.intfList():
            if intf.name != "lo":
                port_args.extend(["-i", f"{port_num}@{intf.name}"])
                port_num += 1

        cmd = [
            self.sw_path,
            "--thrift-port",
            str(self.thrift_port),
            *port_args,
            self.json_path,
            f"> /tmp/{self.name}.log 2>&1 &"
        ]

        self.cmd(" ".join(cmd))
        time.sleep(1)

    def stop(self):
        self.cmd("kill %simple_switch")
        Switch.stop(self)


SCENARIOS = {
    "low_latency": {
        "bw": 80,
        "delay": "5ms",
        "loss": 0,
        "description": "Optimized low-latency condition"
    },
    "baseline": {
        "bw": 40,
        "delay": "15ms",
        "loss": 1,
        "description": "Normal baseline condition"
    },
    "congestion": {
        "bw": 20,
        "delay": "25ms",
        "loss": 2,
        "description": "Congested network condition"
    },
    "ddil": {
        "bw": 5,
        "delay": "80ms",
        "loss": 5,
        "description": "Disconnected, degraded, intermittent, limited condition"
    },
    "relay_failure": {
        "bw": 30,
        "delay": "35ms",
        "loss": 4,
        "description": "Primary relay degraded or unavailable"
    },
    "battery_depletion": {
        "bw": 15,
        "delay": "30ms",
        "loss": 2,
        "description": "Low-energy drone condition"
    }
}


def install_p4_rules(commands_file, thrift_port):
    subprocess.run(
        f"simple_switch_CLI --thrift-port {thrift_port} < {commands_file}",
        shell=True,
        check=True
    )


def run_experiment(scenario, sfc, p4_json, rule_file, policy_type):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")

    if not os.path.exists(p4_json):
        raise FileNotFoundError(f"P4 JSON not found: {p4_json}")

    if not os.path.exists(rule_file):
        raise FileNotFoundError(f"Rule file not found: {rule_file}")

    scenario_config = SCENARIOS[scenario]

    results_dir = "/home/cc/netprompt-milestone-II/results"
    os.makedirs(results_dir, exist_ok=True)

    result_file = os.path.join(
        results_dir,
        f"dynamic_p4_{scenario}_{sfc}.txt"
    )

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    print("=" * 70)
    print("Dynamic SFC-P4 Experiment")
    print("=" * 70)
    print(f"Scenario: {scenario}")
    print(f"Scenario Description: {scenario_config['description']}")
    print(f"Selected SFC: {sfc}")
    print(f"P4 JSON: {p4_json}")
    print(f"Rule File: {rule_file}")
    print(f"Policy Type: {policy_type}")
    print(f"Scenario Config: {scenario_config}")
    print("=" * 70)

    s1 = net.addSwitch(
        "s1",
        sw_path="simple_switch",
        json_path=p4_json,
        thrift_port=9090
    )

    drones = []
    for i in range(1, 11):
        d = net.addHost(
            f"d{i}",
            ip=f"10.0.0.{i}/24"
        )
        drones.append(d)

    edge = net.addHost(
        "edge",
        ip="10.0.0.100/24"
    )

    # Relay drones: d1-d3
    for i in range(3):
        net.addLink(
            drones[i],
            s1,
            bw=100,
            delay="5ms",
            loss=0
        )

    # Search/compute drones: d4-d10 scenario-specific
    for i in range(3, 10):
        net.addLink(
            drones[i],
            s1,
            bw=scenario_config["bw"],
            delay=scenario_config["delay"],
            loss=scenario_config["loss"]
        )

    # Edge link
    net.addLink(
        edge,
        s1,
        bw=200,
        delay="2ms",
        loss=0
    )

    net.start()
    time.sleep(2)

    print("Installing SFC-specific P4 runtime rules...")
    install_p4_rules(rule_file, 9090)

    print("Running pingall...")
    pingall_result = net.pingAll()

    d4 = net.get("d4")
    edge = net.get("edge")

    edge.cmd("pkill -f iperf")
    edge.cmd("iperf -s > /tmp/dynamic_p4_iperf_server.log 2>&1 &")
    time.sleep(1)

    print("Running throughput test d4 -> edge...")
    throughput = d4.cmd("iperf -c 10.0.0.100 -t 10")

    print("Running latency test d4 -> edge...")
    latency = d4.cmd("ping -c 10 10.0.0.100")

    with open(result_file, "w") as f:
        f.write(f"Experiment Type: Dynamic SFC-P4\n")
        f.write(f"Scenario: {scenario}\n")
        f.write(f"Scenario Description: {scenario_config['description']}\n")
        f.write(f"Selected SFC: {sfc}\n")
        f.write(f"P4 JSON: {p4_json}\n")
        f.write(f"Rule File: {rule_file}\n")
        f.write(f"Policy Type: {policy_type}\n")
        f.write(f"Configured Bandwidth: {scenario_config['bw']} Mbps\n")
        f.write(f"Configured Delay: {scenario_config['delay']}\n")
        f.write(f"Configured Loss: {scenario_config['loss']}%\n\n")
        f.write(f"PingAll Dropped Percent: {pingall_result}\n\n")
        f.write("Throughput Result:\n")
        f.write(throughput)
        f.write("\nLatency Result:\n")
        f.write(latency)

    print(f"Results saved to {result_file}")

    net.stop()


if __name__ == "__main__":
    setLogLevel("info")

    parser = argparse.ArgumentParser()

    parser.add_argument("--scenario", required=True)
    parser.add_argument("--sfc", required=True)
    parser.add_argument("--p4-json", required=True)
    parser.add_argument("--rules", required=True)
    parser.add_argument("--policy-type", required=True)

    args = parser.parse_args()

    run_experiment(
        scenario=args.scenario,
        sfc=args.sfc,
        p4_json=args.p4_json,
        rule_file=args.rules,
        policy_type=args.policy_type
    )
