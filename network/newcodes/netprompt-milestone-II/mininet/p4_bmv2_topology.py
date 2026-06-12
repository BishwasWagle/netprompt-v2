from mininet.net import Mininet
from mininet.node import Host, Switch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
import os
import time


class P4Switch(Switch):
    def __init__(self, name, sw_path="simple_switch", json_path=None,
                 thrift_port=9090, pcap_dump=False, **kwargs):
        Switch.__init__(self, name, **kwargs)
        self.sw_path = sw_path
        self.json_path = json_path
        self.thrift_port = thrift_port
        self.pcap_dump = pcap_dump
        self.interfaces = []

    def start(self, controllers):
        if self.json_path is None:
            raise Exception("P4 JSON path required")

        intfs = self.intfList()
        port_args = []

        port_num = 1
        for intf in intfs:
            if intf.name != "lo":
                port_args.extend(["-i", f"{port_num}@{intf.name}"])
                port_num += 1

        cmd = [
            self.sw_path,
            "--thrift-port",
            str(self.thrift_port)
        ]

        if self.pcap_dump:
            cmd.append("--pcap")

        cmd.extend(port_args)
        cmd.append(self.json_path)
        cmd.append("> /tmp/{}.log 2>&1 &".format(self.name))

        self.cmd(" ".join(cmd))
        time.sleep(1)

    def stop(self):
        self.cmd("kill %simple_switch")
        Switch.stop(self)


def run():
    json_path = "/home/cc/netprompt-milestone-II/p4/basic_forward_out/basic_forward.json"
    if not os.path.exists(json_path):
        raise FileNotFoundError(json_path)

    net = Mininet(
        switch=P4Switch,
        link=TCLink,
        controller=None,
        autoSetMacs=True,
        autoStaticArp=True
    )

    print("Creating P4 BMv2 switch...")
    s1 = net.addSwitch(
        "s1",
        sw_path="simple_switch",
        json_path=json_path,
        thrift_port=9090
    )

    print("Creating drone hosts...")
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

    print("Creating links...")

    for i in range(3):
        net.addLink(
            drones[i],
            s1,
            bw=100,
            delay="5ms",
            loss=0
        )

    for i in range(3, 10):
        net.addLink(
            drones[i],
            s1,
            bw=40,
            delay="15ms",
            loss=1
        )

    net.addLink(
        edge,
        s1,
        bw=200,
        delay="2ms",
        loss=0
    )

    print("Starting network...")
    net.start()

    print("Network started.")
    print("BMv2 switch log: /tmp/s1.log")

    CLI(net)

    print("Stopping network...")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    run()
