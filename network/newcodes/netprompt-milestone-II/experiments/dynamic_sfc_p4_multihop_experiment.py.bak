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
        device_id=0,
        **kwargs
    ):
        Switch.__init__(self, name, **kwargs)
        self.sw_path = sw_path
        self.json_path = json_path
        self.thrift_port = thrift_port
        self.device_id = device_id

    def start(self, controllers):
        if self.json_path is None:
            raise Exception("P4 JSON path required")

        port_args = []
        port_num = 1

        for intf in self.intfList():
            if intf.name != "lo":
                port_args.extend(["-i", f"{port_num}@{intf.name}"])
                port_num += 1

        nanolog_path = f"ipc:///tmp/bmv2-{self.name}-notifications.ipc"

        cmd = [
            self.sw_path,
            "--device-id",
            str(self.device_id),
            "--thrift-port",
            str(self.thrift_port),
            "--nanolog",
            nanolog_path,
            *port_args,
            self.json_path,
            f"> /tmp/{self.name}.log 2>&1 &"
        ]

        self.cmd(" ".join(cmd))
        time.sleep(3)

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


def wait_for_thrift(thrift_port, timeout=10):
    start = time.time()

    while time.time() - start < timeout:
        result = subprocess.run(
            f"nc -z 127.0.0.1 {thrift_port}",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if result.returncode == 0:
            return True

        time.sleep(1)

    return False


def run_cli_commands(commands_file, thrift_port):
    print(f"Waiting for BMv2 thrift port {thrift_port}...")

    if not wait_for_thrift(thrift_port, timeout=15):
        raise RuntimeError(
            f"BMv2 thrift port {thrift_port} is not available. "
            f"Check /tmp/s1.log, /tmp/s2.log, /tmp/s3.log."
        )

    subprocess.run(
        f"simple_switch_CLI --thrift-port {thrift_port} < {commands_file}",
        shell=True,
        check=True
    )

def apply_sfc_queue_policy(net, sfc):
    d4 = net.get("d4")
    d5 = net.get("d5")
    d6 = net.get("d6")

    for host in [d4, d5, d6]:
        host.cmd(f"tc qdisc del dev {host.name}-eth0 root 2>/dev/null")

    applied = []

    if sfc == "LowLatencyVideoSFC":
        d4.cmd("tc qdisc replace dev d4-eth0 root pfifo limit 20")
        d5.cmd("tc qdisc replace dev d5-eth0 root pfifo limit 20")
        applied.append("LowLatencyVideoSFC: d4/d5 pfifo limit 20")

    elif sfc == "ReliableRelaySFC":
        d4.cmd("tc qdisc replace dev d4-eth0 root netem delay 10ms loss 0.5%")
        d5.cmd("tc qdisc replace dev d5-eth0 root netem delay 10ms loss 0.5%")
        applied.append("ReliableRelaySFC: d4/d5 netem delay 10ms loss 0.5%")

    elif sfc == "EnergyAwareSFC":
        d4.cmd(
            "tc qdisc replace dev d4-eth0 root tbf "
            "rate 5mbit burst 8kbit latency 100ms"
        )
        d5.cmd(
            "tc qdisc replace dev d5-eth0 root tbf "
            "rate 5mbit burst 8kbit latency 100ms"
        )
        applied.append("EnergyAwareSFC: d4/d5 tbf rate 5mbit")

    elif sfc == "BandwidthOptimizedSFC":
        d6.cmd(
            "tc qdisc replace dev d6-eth0 root tbf "
            "rate 80mbit burst 32kbit latency 50ms"
        )
        applied.append("BandwidthOptimizedSFC: d6 tbf rate 80mbit")

    return applied


def run_experiment(
    scenario,
    sfc,
    p4_json,
    access_rules,
    relay_rules,
    backup_rules,
    policy_type
):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")

    for path in [p4_json, access_rules, relay_rules, backup_rules]:
        if not os.path.exists(path):
            raise FileNotFoundError(path)

    scenario_config = SCENARIOS[scenario]

    results_dir = "/home/cc/netprompt-milestone-II/results"
    os.makedirs(results_dir, exist_ok=True)

    result_file = os.path.join(
        results_dir,
        f"multihop_{scenario}_{sfc}.txt"
    )

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    print("=" * 70)
    print("Multi-Hop Dynamic SFC-P4 Experiment")
    print("=" * 70)
    print(f"Scenario: {scenario}")
    print(f"Selected SFC: {sfc}")
    print(f"P4 JSON: {p4_json}")
    print(f"Access Rules: {access_rules}")
    print(f"Relay Rules: {relay_rules}")
    print(f"Backup Rules: {backup_rules}")
    print(f"Policy Type: {policy_type}")
    print("=" * 70)

    # Three BMv2 switches with different thrift ports
    s1 = net.addSwitch(
        "s1",
        sw_path="simple_switch",
        json_path=p4_json,
        thrift_port=9090,
        device_id=1
    )

    s2 = net.addSwitch(
        "s2",
        sw_path="simple_switch",
        json_path=p4_json,
        thrift_port=9091,
        device_id=2
    )

    s3 = net.addSwitch(
        "s3",
        sw_path="simple_switch",
        json_path=p4_json,
        thrift_port=9092,
        device_id=3
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

    # Drones connected to access switch s1
    for i in range(10):
        if i < 3:
            net.addLink(
                drones[i],
                s1,
                bw=100,
                delay="5ms",
                loss=0
            )
        else:
            net.addLink(
                drones[i],
                s1,
                bw=scenario_config["bw"],
                delay=scenario_config["delay"],
                loss=scenario_config["loss"]
            )

    # Primary relay path: s1 -> s2 -> edge
    net.addLink(
        s1,
        s2,
        bw=60,
        delay="10ms",
        loss=0
    )

    net.addLink(
        s2,
        edge,
        bw=200,
        delay="2ms",
        loss=0
    )

    # Backup relay path: s1 -> s3 -> edge
    backup_loss = 0
    backup_delay = "15ms"

    if scenario == "relay_failure":
        # Make primary path worse under relay failure.
        # Backup remains stable.
        backup_delay = "8ms"
        backup_loss = 0

    net.addLink(
        s1,
        s3,
        bw=40,
        delay=backup_delay,
        loss=backup_loss
    )

    net.addLink(
        s3,
        edge,
        bw=150,
        delay="3ms",
        loss=0
    )
    os.system("rm -f /tmp/bmv2-*.ipc")
    os.system("rm -f /tmp/bmv2-*-notifications.ipc")
    net.start()
    time.sleep(2)

    print("Installing rules on access switch s1...")
    run_cli_commands(access_rules, 9090)

    print("Installing rules on primary relay switch s2...")
    run_cli_commands(relay_rules, 9091)

    print("Installing rules on backup relay switch s3...")
    run_cli_commands(backup_rules, 9092)

    print("Applying queue policy...")
    queue_policy = apply_sfc_queue_policy(net, sfc)

    print("Queue policy:")
    for q in queue_policy:
        print(f"  - {q}")

    print("Running pingall...")
    pingall_result = net.pingAll()

    d4 = net.get("d4")
    d5 = net.get("d5")
    d6 = net.get("d6")

    edge.cmd("pkill -f iperf")
    edge.cmd("iperf -s > /tmp/multihop_iperf_server.log 2>&1 &")
    time.sleep(1)

    throughput_d4 = d4.cmd("iperf -c 10.0.0.100 -t 10")
    throughput_d5 = d5.cmd("iperf -c 10.0.0.100 -t 10")
    throughput_d6 = d6.cmd("iperf -c 10.0.0.100 -t 10")

    latency_d4 = d4.cmd("ping -c 10 10.0.0.100")
    latency_d5 = d5.cmd("ping -c 10 10.0.0.100")
    latency_d6 = d6.cmd("ping -c 10 10.0.0.100")

    with open(result_file, "w") as f:
        f.write("Experiment Type: Multi-Hop Dynamic SFC-P4\n")
        f.write(f"Scenario: {scenario}\n")
        f.write(f"Scenario Description: {scenario_config['description']}\n")
        f.write(f"Selected SFC: {sfc}\n")
        f.write(f"P4 JSON: {p4_json}\n")
        f.write(f"Access Rules: {access_rules}\n")
        f.write(f"Relay Rules: {relay_rules}\n")
        f.write(f"Backup Rules: {backup_rules}\n")
        f.write(f"Policy Type: {policy_type}\n")
        f.write(f"Configured Bandwidth: {scenario_config['bw']} Mbps\n")
        f.write(f"Configured Delay: {scenario_config['delay']}\n")
        f.write(f"Configured Loss: {scenario_config['loss']}%\n\n")

        f.write("Applied Queue Policy:\n")
        for q in queue_policy:
            f.write(f"- {q}\n")

        f.write(f"\nPingAll Dropped Percent: {pingall_result}\n\n")

        f.write("Throughput Result d4 -> edge:\n")
        f.write(throughput_d4)
        f.write("\nThroughput Result d5 -> edge:\n")
        f.write(throughput_d5)
        f.write("\nThroughput Result d6 -> edge:\n")
        f.write(throughput_d6)

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
    parser.add_argument("--access-rules", required=True)
    parser.add_argument("--relay-rules", required=True)
    parser.add_argument("--backup-rules", required=True)
    parser.add_argument("--policy-type", required=True)

    args = parser.parse_args()

    run_experiment(
        scenario=args.scenario,
        sfc=args.sfc,
        p4_json=args.p4_json,
        access_rules=args.access_rules,
        relay_rules=args.relay_rules,
        backup_rules=args.backup_rules,
        policy_type=args.policy_type
    )
