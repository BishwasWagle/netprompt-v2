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


def apply_sfc_queue_policy(net, sfc):
    """
    Applies Linux tc queue/rate policy inside Mininet hosts.
    This makes SFCs affect not only P4 program/rules, but also
    runtime traffic behavior.
    """

    d4 = net.get("d4")
    d5 = net.get("d5")
    d6 = net.get("d6")
    d7 = net.get("d7")

    applied = []

    # Clear old qdisc state where possible.
    for host in [d4, d5, d6, d7]:
        host.cmd(f"tc qdisc del dev {host.name}-eth0 root 2>/dev/null")

    if sfc == "LowLatencyVideoSFC":
        # Small FIFO queue to reduce buffering delay.
        d4.cmd("tc qdisc replace dev d4-eth0 root pfifo limit 20")
        d5.cmd("tc qdisc replace dev d5-eth0 root pfifo limit 20")

        applied.append("d4-eth0: pfifo limit 20")
        applied.append("d5-eth0: pfifo limit 20")

    elif sfc == "BandwidthOptimizedSFC":
        # Higher token bucket rate for bulk imaging traffic.
        d6.cmd(
            "tc qdisc replace dev d6-eth0 root tbf "
            "rate 80mbit burst 32kbit latency 50ms"
        )

        applied.append("d6-eth0: tbf rate 80mbit burst 32kbit latency 50ms")

    elif sfc == "EnergyAwareSFC":
        # Energy saving: allow only reduced-rate essential traffic.
        d4.cmd(
            "tc qdisc replace dev d4-eth0 root tbf "
            "rate 5mbit burst 8kbit latency 100ms"
        )
        d5.cmd(
            "tc qdisc replace dev d5-eth0 root tbf "
            "rate 5mbit burst 8kbit latency 100ms"
        )

        applied.append("d4-eth0: tbf rate 5mbit burst 8kbit latency 100ms")
        applied.append("d5-eth0: tbf rate 5mbit burst 8kbit latency 100ms")

    elif sfc == "ReliableRelaySFC":
        # Reliability mode: mild extra delay, low loss to emulate stable path.
        d4.cmd("tc qdisc replace dev d4-eth0 root netem delay 10ms loss 0.5%")
        d5.cmd("tc qdisc replace dev d5-eth0 root netem delay 10ms loss 0.5%")

        applied.append("d4-eth0: netem delay 10ms loss 0.5%")
        applied.append("d5-eth0: netem delay 10ms loss 0.5%")

    else:
        applied.append("No SFC-specific queue policy applied")

    return applied


def run_iperf_pair(src, dst, dst_ip, label):
    """
    Run iperf2 with one fresh server per client and detailed diagnostics.
    """
    dst.cmd("pkill -f 'iperf -s' || true")
    time.sleep(1)

    server_log_path = f"/tmp/{label}_iperf_server.log"
    dst.cmd(f"rm -f {server_log_path}")
    dst.cmd(f"iperf -s -p 5001 > {server_log_path} 2>&1 &")
    time.sleep(2)

    server_process = dst.cmd("ps aux | grep '[i]perf -s' || true")
    listen_check = dst.cmd("ss -ltnp | grep 5001 || netstat -ltnp 2>/dev/null | grep 5001 || true")
    tcp_check = src.cmd(f"timeout 5 bash -c '</dev/tcp/{dst_ip}/5001' && echo TCP_OK || echo TCP_FAIL")

    if "TCP_OK" in tcp_check:
        client_output = src.cmd(f"iperf -c {dst_ip} -p 5001 -t 5")
    else:
        client_output = "SKIPPED_IPERF_CLIENT_BECAUSE_TCP_CHECK_FAILED\n"

    server_log = dst.cmd(f"cat {server_log_path} || true")

    dst.cmd("pkill -f 'iperf -s' || true")
    time.sleep(1)

    return {
        "server_process": server_process,
        "listen_check": listen_check,
        "tcp_check": tcp_check,
        "client_output": client_output,
        "server_log": server_log,
    }


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
    print("Dynamic SFC-P4 Experiment with Queue Policy")
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

    print("Applying SFC-specific queue policy...")
    queue_policy = apply_sfc_queue_policy(net, sfc)

    print("Queue policy applied:")
    for item in queue_policy:
        print(f"  - {item}")

    print("Running pingall...")
    pingall_result = net.pingAll()

    d4 = net.get("d4")
    d5 = net.get("d5")
    d6 = net.get("d6")
    edge = net.get("edge")

    edge.cmd("pkill -f iperf")
    edge.cmd("iperf -s > /tmp/dynamic_p4_iperf_server.log 2>&1 &")
    time.sleep(1)

    print("Running throughput test d4 -> edge...")
    iperf_d4 = run_iperf_pair(d4, edge, "10.0.0.100", "singlehop_d4_edge")
    throughput_d4 = iperf_d4["client_output"]

    print("Running throughput test d5 -> edge...")
    iperf_d5 = run_iperf_pair(d5, edge, "10.0.0.100", "singlehop_d5_edge")
    throughput_d5 = iperf_d5["client_output"]

    print("Running throughput test d6 -> edge...")
    iperf_d6 = run_iperf_pair(d6, edge, "10.0.0.100", "singlehop_d6_edge")
    throughput_d6 = iperf_d6["client_output"]

    print("Running latency test d4 -> edge...")
    latency_d4 = d4.cmd("ping -c 10 10.0.0.100")

    print("Running latency test d5 -> edge...")
    latency_d5 = d5.cmd("ping -c 10 10.0.0.100")

    print("Running latency test d6 -> edge...")
    latency_d6 = d6.cmd("ping -c 10 10.0.0.100")

    with open(result_file, "w") as f:
        f.write(f"Experiment Type: Dynamic SFC-P4 with Queue Policy\n")
        f.write(f"Scenario: {scenario}\n")
        f.write(f"Scenario Description: {scenario_config['description']}\n")
        f.write(f"Selected SFC: {sfc}\n")
        f.write(f"P4 JSON: {p4_json}\n")
        f.write(f"Rule File: {rule_file}\n")
        f.write(f"Policy Type: {policy_type}\n")
        f.write(f"Configured Bandwidth: {scenario_config['bw']} Mbps\n")
        f.write(f"Configured Delay: {scenario_config['delay']}\n")
        f.write(f"Configured Loss: {scenario_config['loss']}%\n\n")

        f.write("Applied Queue Policy:\n")
        for item in queue_policy:
            f.write(f"- {item}\n")

        f.write(f"\nPingAll Dropped Percent: {pingall_result}\n\n")

        f.write("Throughput Result d4 -> edge:\n")
        f.write(throughput_d4)
        f.write("\nTCP Port Check d4 -> edge:\n")
        f.write(iperf_d4["tcp_check"])
        f.write("\nIperf Listen Check d4 -> edge:\n")
        f.write(iperf_d4["listen_check"])
        f.write("\nIperf Server Process d4 -> edge:\n")
        f.write(iperf_d4["server_process"])
        f.write("\nIperf Server Log d4 -> edge:\n")
        f.write(iperf_d4["server_log"])

        f.write("\nThroughput Result d5 -> edge:\n")
        f.write(throughput_d5)
        f.write("\nTCP Port Check d5 -> edge:\n")
        f.write(iperf_d5["tcp_check"])
        f.write("\nIperf Listen Check d5 -> edge:\n")
        f.write(iperf_d5["listen_check"])
        f.write("\nIperf Server Process d5 -> edge:\n")
        f.write(iperf_d5["server_process"])
        f.write("\nIperf Server Log d5 -> edge:\n")
        f.write(iperf_d5["server_log"])

        f.write("\nThroughput Result d6 -> edge:\n")
        f.write(throughput_d6)
        f.write("\nTCP Port Check d6 -> edge:\n")
        f.write(iperf_d6["tcp_check"])
        f.write("\nIperf Listen Check d6 -> edge:\n")
        f.write(iperf_d6["listen_check"])
        f.write("\nIperf Server Process d6 -> edge:\n")
        f.write(iperf_d6["server_process"])
        f.write("\nIperf Server Log d6 -> edge:\n")
        f.write(iperf_d6["server_log"])

        f.write("\nLatency Result d4 -> edge:\n")
        f.write(latency_d4)
        f.write("\nLatency Result d5 -> edge:\n")
        f.write(latency_d5)
        f.write("\nLatency Result d6 -> edge:\n")
        f.write(latency_d6)

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
