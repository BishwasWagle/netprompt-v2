from mininet.net import Mininet
from mininet.node import Host, Switch
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
    "baseline": {
        "bw": 40,
        "delay": "15ms",
        "loss": 1
    },
    "congestion": {
        "bw": 20,
        "delay": "25ms",
        "loss": 2
    },
    "ddil": {
        "bw": 5,
        "delay": "80ms",
        "loss": 5
    },
    "low_latency": {
        "bw": 80,
        "delay": "5ms",
        "loss": 0
    }
}


def install_p4_rules(commands_file, thrift_port):
    subprocess.run(
        f"simple_switch_CLI --thrift-port {thrift_port} < {commands_file}",
        shell=True,
        check=True
    )


def run_experiment(scenario):
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")

    config = SCENARIOS[scenario]

    json_path = "/home/cc/netprompt-milestone-II/p4/basic_forward_out/basic_forward.json"
    commands_file = "/home/cc/netprompt-milestone-II/p4/s1_runtime_commands.txt"
    results_dir = "/home/cc/netprompt-milestone-II/results"

    os.makedirs(results_dir, exist_ok=True)

    result_file = os.path.join(
        results_dir,
        f"p4_bmv2_results_{scenario}.txt"
    )

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    print(f"Starting P4/BMv2 experiment: {scenario}")
    print(f"Scenario config: {config}")

    s1 = net.addSwitch(
        "s1",
        sw_path="simple_switch",
        json_path=json_path,
        thrift_port=9090
    )

    drones = []

    for i in range(1, 11):
        d = net.addHost(f"d{i}", ip=f"10.0.0.{i}/24")
        drones.append(d)

    edge = net.addHost("edge", ip="10.0.0.100/24")

    # Relay drones
    for i in range(3):
        net.addLink(
            drones[i],
            s1,
            bw=100,
            delay="5ms",
            loss=0
        )

    # Search/sensing drones use scenario-specific degraded links
    for i in range(3, 10):
        net.addLink(
            drones[i],
            s1,
            bw=config["bw"],
            delay=config["delay"],
            loss=config["loss"]
        )

    net.addLink(
        edge,
        s1,
        bw=200,
        delay="2ms",
        loss=0
    )

    net.start()

    time.sleep(2)

    print("Installing P4 forwarding rules...")
    install_p4_rules(commands_file, 9090)

    print("Running pingall...")
    pingall_result = net.pingAll()

    d4 = net.get("d4")
    edge = net.get("edge")

    print("Starting iperf server on edge...")
    edge.cmd("iperf -s > /tmp/p4_iperf_server.log 2>&1 &")
    time.sleep(1)

    print("Running throughput test d4 -> edge...")
    throughput = d4.cmd("iperf -c 10.0.0.100 -t 10")

    print("Running latency test d4 -> edge...")
    latency = d4.cmd("ping -c 10 10.0.0.100")

    with open(result_file, "w") as f:
        f.write(f"Scenario: {scenario}\n")
        f.write(f"Bandwidth: {config['bw']} Mbps\n")
        f.write(f"Delay: {config['delay']}\n")
        f.write(f"Loss: {config['loss']}%\n\n")
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
    parser.add_argument(
        "--scenario",
        required=True,
        choices=list(SCENARIOS.keys())
    )

    args = parser.parse_args()
    run_experiment(args.scenario)
