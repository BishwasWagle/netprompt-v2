import re
import json
import csv
from pathlib import Path

result_files = list(Path(".").glob("results_Field_*_*_from_network_node.txt"))

rows = []

for file in result_files:
    text = file.read_text()

    filename = file.name

    # Expected filename:
    # results_Field_1_LowLatencyVideoSFC_from_network_node.txt
    match = re.match(
        r"results_(Field_\d+)_(.+)_from_network_node\.txt",
        filename
    )

    if not match:
        continue

    field_id = match.group(1)
    filename_template = match.group(2)

    mode_match = re.search(r"SFC Mode:\s*(.+)", text)
    sfc_mode = mode_match.group(1).strip() if mode_match else filename_template

    bandwidth_match = re.search(r"([\d.]+)\s+Mbits/sec", text)
    throughput_mbps = float(bandwidth_match.group(1)) if bandwidth_match else None

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
        packet_loss_percent = round(
            (transmitted - received) / transmitted * 100,
            2
        )
    else:
        transmitted = received = packet_loss_percent = None

    rows.append({
        "field_id": field_id,
        "sfc_mode": sfc_mode,
        "throughput_mbps": throughput_mbps,
        "rtt_min_ms": rtt_min,
        "rtt_avg_ms": rtt_avg,
        "rtt_max_ms": rtt_max,
        "rtt_mdev_ms": rtt_mdev,
        "packet_loss_percent": packet_loss_percent,
        "source_file": filename
    })

rows = sorted(rows, key=lambda x: x["field_id"])

with open("parsed_results_clean.json", "w") as f:
    json.dump(rows, f, indent=2)

with open("parsed_results_clean.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print("Generated parsed_results_clean.json and parsed_results_clean.csv")
print(json.dumps(rows, indent=2))
