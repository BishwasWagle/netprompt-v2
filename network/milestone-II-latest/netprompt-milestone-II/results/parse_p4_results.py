import re
import json
import csv
from pathlib import Path

files = sorted(Path(".").glob("p4_bmv2_results_*.txt"))

rows = []

for file in files:
    text = file.read_text()

    scenario = re.search(r"Scenario:\s*(.+)", text).group(1).strip()
    bandwidth = float(re.search(r"Bandwidth:\s*([\d.]+)", text).group(1))
    delay = re.search(r"Delay:\s*(.+)", text).group(1).strip()
    configured_loss = float(re.search(r"Loss:\s*([\d.]+)", text).group(1))

    throughput_match = re.search(r"([\d.]+)\s+Mbits/sec", text)
    throughput_mbps = float(throughput_match.group(1)) if throughput_match else None

    rtt_match = re.search(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        text
    )

    if rtt_match:
        rtt_min_ms = float(rtt_match.group(1))
        rtt_avg_ms = float(rtt_match.group(2))
        rtt_max_ms = float(rtt_match.group(3))
        rtt_mdev_ms = float(rtt_match.group(4))
    else:
        rtt_min_ms = rtt_avg_ms = rtt_max_ms = rtt_mdev_ms = None

    loss_match = re.search(r"(\d+) packets transmitted, (\d+) received", text)
    if loss_match:
        transmitted = int(loss_match.group(1))
        received = int(loss_match.group(2))
        measured_loss = round((transmitted - received) / transmitted * 100, 2)
    else:
        transmitted = received = measured_loss = None

    rows.append({
        "scenario": scenario,
        "configured_bandwidth_mbps": bandwidth,
        "configured_delay": delay,
        "configured_loss_percent": configured_loss,
        "throughput_mbps": throughput_mbps,
        "rtt_min_ms": rtt_min_ms,
        "rtt_avg_ms": rtt_avg_ms,
        "rtt_max_ms": rtt_max_ms,
        "rtt_mdev_ms": rtt_mdev_ms,
        "measured_packet_loss_percent": measured_loss,
        "source_file": file.name
    })

with open("p4_bmv2_results_clean.json", "w") as f:
    json.dump(rows, f, indent=2)

with open("p4_bmv2_results_clean.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print("Generated p4_bmv2_results_clean.json and p4_bmv2_results_clean.csv")
print(json.dumps(rows, indent=2))
