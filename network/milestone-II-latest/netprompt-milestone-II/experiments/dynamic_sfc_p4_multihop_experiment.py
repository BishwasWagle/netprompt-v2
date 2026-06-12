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


def configure_edge_interface_for_path(edge, policy_type, scenario):
    """
    The edge host has two interfaces:
      edge-eth0: connected to primary relay s2
      edge-eth1: connected to backup relay s3

    For primary-path policies, bind 10.0.0.100/MAC 00:...:0b to edge-eth0.
    For backup-path policies, bind 10.0.0.100/MAC 00:...:0b to edge-eth1.

    This prevents backup-path traffic from arriving on an interface that does
    not own the expected IP/MAC identity.
    """
    edge.cmd("ip addr flush dev edge-eth0 || true")
    edge.cmd("ip addr flush dev edge-eth1 || true")

    edge.cmd("ip link set dev edge-eth0 up || true")
    edge.cmd("ip link set dev edge-eth1 up || true")

    use_backup = (
        "backup" in str(policy_type).lower()
        or scenario in ["congestion", "relay_failure", "ddil"]
    )

    if use_backup:
        edge.cmd("ip link set dev edge-eth1 address 00:00:00:00:00:0c || true")
        edge.cmd("ip addr add 10.0.0.100/24 dev edge-eth1")
        edge.cmd("ip route replace 10.0.0.0/24 dev edge-eth1 src 10.0.0.100")
        return "backup_edge_eth1"
    else:
        edge.cmd("ip link set dev edge-eth0 address 00:00:00:00:00:0b || true")
        edge.cmd("ip addr add 10.0.0.100/24 dev edge-eth0")
        edge.cmd("ip route replace 10.0.0.0/24 dev edge-eth0 src 10.0.0.100")
        return "primary_edge_eth0"


def configure_static_arp_for_path(drones, edge, edge_interface_mode):
    """
    Refresh static ARP after changing edge interface/MAC.

    Mininet autoStaticArp runs before we move the edge IP/MAC to edge-eth1
    for backup-path scenarios. Therefore drones may still map 10.0.0.100
    to the old edge-eth0 MAC. This function fixes that.
    """
    if edge_interface_mode == "backup_edge_eth1":
        edge_mac = "00:00:00:00:00:0c"
    else:
        edge_mac = "00:00:00:00:00:0b"

    for i, d in enumerate(drones, start=1):
        d.cmd("arp -d 10.0.0.100 2>/dev/null || true")
        d.cmd(f"arp -s 10.0.0.100 {edge_mac}")

        edge.cmd(f"arp -d 10.0.0.{i} 2>/dev/null || true")
        edge.cmd(f"arp -s 10.0.0.{i} 00:00:00:00:00:{i:02x}")


def disable_host_offloads(hosts):
    """
    Disable NIC checksum/segmentation offloads inside Mininet hosts.
    BMv2/simple_switch often fails with TCP/UDP traffic when checksum offload
    leaves checksums incomplete for software switches.
    """
    for h in hosts:
        intf = h.defaultIntf().name
        h.cmd(f"ethtool -K {intf} tx off rx off sg off tso off gso off gro off lro off 2>/dev/null || true")
        h.cmd(f"ip link set dev {intf} up")


def run_iperf_pair(src, dst, dst_ip, label):
    """
    Run UDP iperf with one fresh server per client.

    TCP iperf handshakes can fail in this BMv2/mininet setup even when ICMP
    forwarding is healthy. UDP gives us a stable throughput/loss metric for
    network evaluation.
    """
    dst.cmd("pkill -f 'iperf -s' || true")
    time.sleep(1)

    server_log_path = f"/tmp/{label}_iperf_udp_server.log"
    dst.cmd(f"rm -f {server_log_path}")
    dst.cmd(f"iperf -s -u -p 5001 > {server_log_path} 2>&1 &")
    time.sleep(2)

    server_process = dst.cmd("ps aux | grep '[i]perf -s' || true")
    listen_check = dst.cmd("ss -lunp | grep 5001 || netstat -lunp 2>/dev/null | grep 5001 || true")
    tcp_check = "UDP_MODE_NO_TCP_CHECK\n"

    client_output = src.cmd(
        f"timeout 20 iperf -u -c {dst_ip} -p 5001 -b 10M -t 5 2>&1 || true"
    )

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

    edge_interface_mode = configure_edge_interface_for_path(edge, policy_type, scenario)
    configure_static_arp_for_path(drones, edge, edge_interface_mode)

    disable_host_offloads(drones + [edge])
    time.sleep(1)

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

    tcp_debug = {
        "d4_ip_addr": d4.cmd("ip addr"),
        "d4_route": d4.cmd("ip route"),
        "d4_arp": d4.cmd("arp -n"),
        "edge_ip_addr": edge.cmd("ip addr"),
        "edge_route": edge.cmd("ip route"),
        "edge_arp": edge.cmd("arp -n"),
        "d4_ping_edge": d4.cmd("ping -c 3 10.0.0.100"),
        "edge_ping_d4": edge.cmd("ping -c 3 10.0.0.4"),
        "d4_tcp_5001_before_server": d4.cmd("timeout 3 bash -c '</dev/tcp/10.0.0.100/5001' && echo TCP_OK || echo TCP_FAIL"),
    }

    iperf_d4 = run_iperf_pair(d4, edge, "10.0.0.100", "multihop_d4_edge")
    iperf_d5 = run_iperf_pair(d5, edge, "10.0.0.100", "multihop_d5_edge")
    iperf_d6 = run_iperf_pair(d6, edge, "10.0.0.100", "multihop_d6_edge")

    throughput_d4 = iperf_d4["client_output"]
    throughput_d5 = iperf_d5["client_output"]
    throughput_d6 = iperf_d6["client_output"]

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
        f.write(f"Edge Interface Mode: {edge_interface_mode}\n")
        f.write(f"Configured Bandwidth: {scenario_config['bw']} Mbps\n")
        f.write(f"Configured Delay: {scenario_config['delay']}\n")
        f.write(f"Configured Loss: {scenario_config['loss']}%\n\n")

        f.write("Applied Queue Policy:\n")
        for q in queue_policy:
            f.write(f"- {q}\n")

        f.write(f"\nPingAll Dropped Percent: {pingall_result}\n\n")

        f.write("===== TCP DEBUG SNAPSHOT =====\n")
        for k, v in tcp_debug.items():
            f.write(f"\n--- {k} ---\n")
            f.write(str(v))
            f.write("\n")
        f.write("===== END TCP DEBUG SNAPSHOT =====\n\n")

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
