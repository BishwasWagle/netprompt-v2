import re
import json
import csv
from pathlib import Path

files = sorted(
    list(Path(".").glob("dynamic_p4_*.txt")) +
    list(Path(".").glob("multihop_*.txt"))
)

rows = []

def get(pattern, text, default=None):
    m = re.search(pattern, text)
    return m.group(1).strip() if m else default

def parse_float(value):
    if value in [None, ""]:
        return None
    try:
        return float(value)
    except Exception:
        return None

for file in files:
    text = file.read_text(errors="ignore")

    experiment_type = get(r"Experiment Type:\s*(.+)", text, "Unknown")
    scenario = get(r"Scenario:\s*(.+)", text, "Unknown")
    selected_sfc = get(r"Selected SFC:\s*(.+)", text, "Unknown")
    p4_json = get(r"P4 JSON:\s*(.+)", text, "")
    rule_file = get(r"Rule File:\s*(.+)", text, "")
    access_rules = get(r"Access Rules:\s*(.+)", text, "")
    relay_rules = get(r"Relay Rules:\s*(.+)", text, "")
    backup_rules = get(r"Backup Rules:\s*(.+)", text, "")
    policy_type = get(r"Policy Type:\s*(.+)", text, "")

    bw = parse_float(get(r"Configured Bandwidth:\s*([\d.]+)", text, ""))
    delay = get(r"Configured Delay:\s*(.+)", text, "")
    configured_loss = parse_float(get(r"Configured Loss:\s*([\d.]+)", text, ""))

    rtt_matches = re.findall(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        text
    )

    if rtt_matches:
        rtt_min, rtt_avg, rtt_max, rtt_mdev = map(float, rtt_matches[0])
    else:
        rtt_min = rtt_avg = rtt_max = rtt_mdev = None

    loss_matches = re.findall(
        r"(\d+) packets transmitted, (\d+) received",
        text
    )

    if loss_matches:
        transmitted, received = map(int, loss_matches[0])
        measured_loss = round((transmitted - received) / transmitted * 100, 2)
    else:
        measured_loss = None

    throughput_matches = re.findall(r"([\d.]+)\s+Mbits/sec", text)
    throughput_mbps = float(throughput_matches[-1]) if throughput_matches else None

    rows.append({
        "experiment_type": experiment_type,
        "scenario": scenario,
        "selected_sfc": selected_sfc,
        "policy_type": policy_type,
        "p4_json": p4_json,
        "rule_file": rule_file,
        "access_rules": access_rules,
        "relay_rules": relay_rules,
        "backup_rules": backup_rules,
        "configured_bandwidth_mbps": bw,
        "configured_delay": delay,
        "configured_loss_percent": configured_loss,
        "throughput_mbps": throughput_mbps,
        "rtt_min_ms": rtt_min,
        "rtt_avg_ms": rtt_avg,
        "rtt_max_ms": rtt_max,
        "rtt_mdev_ms": rtt_mdev,
        "measured_packet_loss_percent": measured_loss,
        "source_file": file.name
    })

with open("final_milestone2_results_clean.json", "w") as f:
    json.dump(rows, f, indent=2)

with open("final_milestone2_results_clean.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print("Generated final_milestone2_results_clean.json and final_milestone2_results_clean.csv")
print(json.dumps(rows, indent=2))
