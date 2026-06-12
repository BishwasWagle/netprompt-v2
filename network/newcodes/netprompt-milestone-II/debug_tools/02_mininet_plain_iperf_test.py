from mininet.net import Mininet
from mininet.node import OVSBridge
from mininet.link import TCLink
from mininet.log import setLogLevel
import time
from pathlib import Path


OUT = Path("/home/cc/netprompt-milestone-II/debug_results/02_mininet_plain_iperf_test.log")
OUT.parent.mkdir(parents=True, exist_ok=True)


def write(label, value):
    with OUT.open("a") as f:
        f.write(f"\n=== {label} ===\n")
        f.write(str(value))
        f.write("\n")


def main():
    if OUT.exists():
        OUT.unlink()

    net = Mininet(switch=OVSBridge, link=TCLink, controller=None, autoSetMacs=True, autoStaticArp=True)

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    s1 = net.addSwitch("s1")

    net.addLink(h1, s1, bw=100, delay="5ms", loss=0)
    net.addLink(h2, s1, bw=100, delay="5ms", loss=0)

    net.start()
    time.sleep(1)

    write("h1 route", h1.cmd("ip route"))
    write("h2 route", h2.cmd("ip route"))
    write("h1 arp before", h1.cmd("arp -n"))
    write("ping h1 -> h2", h1.cmd("ping -c 3 10.0.0.2"))
    write("h1 arp after", h1.cmd("arp -n"))

    h2.cmd("pkill -f 'iperf -s' || true")
    time.sleep(1)
    h2.cmd("iperf -s -p 5001 > /tmp/h2_iperf_server.log 2>&1 &")
    time.sleep(2)

    write("h2 iperf process", h2.cmd("ps aux | grep '[i]perf -s' || true"))
    write("h2 port check", h2.cmd("ss -ltnp | grep 5001 || netstat -ltnp 2>/dev/null | grep 5001 || true"))
    write("tcp check h1 -> h2", h1.cmd("timeout 5 bash -c '</dev/tcp/10.0.0.2/5001' && echo TCP_OK || echo TCP_FAIL"))
    write("iperf h1 -> h2", h1.cmd("iperf -c 10.0.0.2 -p 5001 -t 5"))

    net.stop()

    print(f"[OK] Saved {OUT}")


if __name__ == "__main__":
    setLogLevel("info")
    main()
