import re
import json
import csv
from pathlib import Path

files = sorted(Path(".").glob("milestone2_*.txt"))
rows = []

for file in files:
    text = file.read_text()

    def get(pattern, default=None):
        m = re.search(pattern, text)
        return m.group(1).strip() if m else default

    scenario = get(r"Scenario:\s*(.+)")
    recommended_sfc = get(r"Recommended SFC:\s*(.+)")
    selected_sfc = get(r"Selected SFC:\s*(.+)")
    sfc_priority = get(r"SFC Priority:\s*(.+)")
    bw = float(get(r"Configured Bandwidth:\s*([\d.]+)", 0))
    delay = get(r"Configured Delay:\s*(.+)")
    loss = float(get(r"Configured Loss:\s*([\d.]+)", 0))

    throughput_match = re.findall(r"([\d.]+)\s+Mbits/sec", text)
    throughput = float(throughput_match[-1]) if throughput_match else None

    rtt_match = re.search(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        text
    )

    if rtt_match:
        rtt_min = float(rtt_match.group(1))
        rtt_avg = float(rtt_match.group(2))
        rtt_max = float(rtt_match.group(3))
        rtt_mdev = float(rtt_match.group(4))
    else:
        rtt_min = rtt_avg = rtt_max = rtt_mdev = None

    loss_match = re.search(r"(\d+) packets transmitted, (\d+) received", text)
    if loss_match:
        transmitted = int(loss_match.group(1))
        received = int(loss_match.group(2))
        measured_loss = round((transmitted - received) / transmitted * 100, 2)
    else:
        measured_loss = None

    rows.append({
        "scenario": scenario,
        "recommended_sfc": recommended_sfc,
        "selected_sfc": selected_sfc,
        "sfc_priority": sfc_priority,
        "configured_bandwidth_mbps": bw,
        "configured_delay": delay,
        "configured_loss_percent": loss,
        "throughput_mbps": throughput,
        "rtt_min_ms": rtt_min,
        "rtt_avg_ms": rtt_avg,
        "rtt_max_ms": rtt_max,
        "rtt_mdev_ms": rtt_mdev,
        "measured_packet_loss_percent": measured_loss,
        "source_file": file.name
    })

with open("milestone2_results_clean.json", "w") as f:
    json.dump(rows, f, indent=2)

with open("milestone2_results_clean.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print("Generated milestone2_results_clean.json and milestone2_results_clean.csv")
print(json.dumps(rows, indent=2))
