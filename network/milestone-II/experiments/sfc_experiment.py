from mininet.net import Mininet
from mininet.node import OVSController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel
import time
import sys

SFC_MODES = {
    "LowLatencyVideoSFC": {
        "drone_bw": 80,
        "drone_delay": "5ms",
        "drone_loss": 0,
        "description": "Prioritize low-latency video traffic"
    },
    "BandwidthOptimizedSFC": {
        "drone_bw": 120,
        "drone_delay": "20ms",
        "drone_loss": 0,
        "description": "Prioritize high-throughput transfer"
    },
    "ReliableRelaySFC": {
        "drone_bw": 60,
        "drone_delay": "10ms",
        "drone_loss": 0,
        "description": "Reliable relay path with low packet loss"
    },
    "EnergyAwareSFC": {
        "drone_bw": 20,
        "drone_delay": "25ms",
        "drone_loss": 2,
        "description": "Reduced bandwidth to emulate energy saving"
    }
}

def run_experiment(mode):
    if mode not in SFC_MODES:
        print("Invalid SFC mode.")
        print("Available modes:", list(SFC_MODES.keys()))
        return

    config = SFC_MODES[mode]

    print(f"\nSelected SFC Mode: {mode}")
    print(config["description"])
    print("=" * 60)

    net = Mininet(
        controller=OVSController,
        switch=OVSSwitch,
        link=TCLink
    )

    c0 = net.addController("c0")
    s1 = net.addSwitch("s1")

    drones = []
    for i in range(1, 11):
        d = net.addHost(f"d{i}", ip=f"10.0.0.{i}/24")
        drones.append(d)

    edge = net.addHost("edge", ip="10.0.0.100/24")

    # Relay drones d1-d3: better links
    for i in range(3):
        net.addLink(
            drones[i],
            s1,
            bw=100,
            delay="5ms",
            loss=0
        )

    # Search drones d4-d10: SFC-dependent links
    for i in range(3, 10):
        net.addLink(
            drones[i],
            s1,
            bw=config["drone_bw"],
            delay=config["drone_delay"],
            loss=config["drone_loss"]
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

    print("\nTesting connectivity...")
    net.pingAll()

    d4 = net.get("d4")
    edge = net.get("edge")

    print("\nStarting iperf server on edge...")
    edge.cmd("iperf -s > /tmp/iperf_server.log 2>&1 &")
    time.sleep(1)

    print("\nRunning throughput test: d4 -> edge")
    throughput = d4.cmd("iperf -c 10.0.0.100 -t 10")
    print(throughput)

    print("\nRunning latency test: d4 -> edge")
    latency = d4.cmd("ping -c 10 10.0.0.100")
    print(latency)

    result_file = f"results_{mode}.txt"
    with open(result_file, "w") as f:
        f.write(f"SFC Mode: {mode}\n")
        f.write(f"Description: {config['description']}\n\n")
        f.write("Throughput Result:\n")
        f.write(throughput)
        f.write("\nLatency Result:\n")
        f.write(latency)

    print(f"\nResults saved to {result_file}")

    net.stop()

if __name__ == "__main__":
    setLogLevel("info")

    if len(sys.argv) != 2:
        print("Usage:")
        print("sudo python3 sfc_experiment.py <SFC_MODE>")
        print("\nAvailable modes:")
        for mode in SFC_MODES:
            print("-", mode)
        sys.exit(1)

    run_experiment(sys.argv[1])
