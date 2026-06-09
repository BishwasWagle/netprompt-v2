from mininet.net import Mininet
from mininet.node import OVSController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel

def run():
    net = Mininet(controller=OVSController, switch=OVSSwitch, link=TCLink)

    print("Creating controller...")
    c0 = net.addController("c0", controller=OVSController)

    print("Creating switch...")
    s1 = net.addSwitch("s1")

    print("Creating drone hosts...")
    drones = []
    for i in range(1, 11):
        d = net.addHost(f"d{i}", ip=f"10.0.0.{i}/24")
        drones.append(d)

    edge = net.addHost("edge", ip="10.0.0.100/24")

    print("Creating links...")

    # Drones 1-3 are relay drones
    for i in range(3):
        net.addLink(drones[i], s1, bw=100, delay="5ms", loss=0)

    # Drones 4-10 are sensing/search drones
    for i in range(3, 10):
        net.addLink(drones[i], s1, bw=40, delay="15ms", loss=1)

    # Edge node
    net.addLink(edge, s1, bw=200, delay="2ms", loss=0)

    print("Starting network...")
    net.start()

    print("Testing connectivity...")
    net.pingAll()

    print("Starting CLI...")
    CLI(net)

    print("Stopping network...")
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    run()
