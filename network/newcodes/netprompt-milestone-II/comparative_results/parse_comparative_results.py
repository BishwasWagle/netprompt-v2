import re
import csv
from pathlib import Path


RESULT_DIR = Path("/home/cc/netprompt-milestone-II/comparative_results")
OUT_CSV = RESULT_DIR / "comparative_results_clean.csv"


def grab(pattern, text, default=""):
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else default


def grab_float(pattern, text):
    value = grab(pattern, text, "")
    try:
        if value in ["", "NA", "None", "null"]:
            return ""
        return float(value)
    except Exception:
        return ""


def parse_rtt_avg(text):
    direct = grab_float(r"RTT Average ms:\s*([\d.]+)", text)
    if direct != "":
        return direct

    m = re.search(
        r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/[\d.]+",
        text
    )
    return float(m.group(1)) if m else ""


def parse_throughput(text):
    direct = grab_float(r"Throughput Mbps:\s*([\d.]+)", text)
    if direct != "":
        return direct

    matches = re.findall(r"([\d.]+)\s+Mbits/sec", text)
    return float(matches[-1]) if matches else ""


def parse_packet_loss(text):
    direct = grab_float(r"Measured Packet Loss Percent:\s*([\d.]+)", text)
    if direct != "":
        return direct

    m = re.search(r"\*\*\* Results:\s*([\d.]+)% dropped", text)
    if m:
        return float(m.group(1))

    m = re.search(r"PingAll Dropped Percent:\s*([\d.]+)", text)
    if m:
        return float(m.group(1))

    return ""


rows = []

for file in sorted(RESULT_DIR.glob("*.txt")):
    text = file.read_text(errors="ignore")

    rows.append({
        "source_file": file.name,
        "method": grab(r"Method:\s*(.+)", text),
        "scenario": grab(r"Scenario:\s*(.+)", text),
        "selected_sfc": grab(r"Selected SFC:\s*(.+)", text),
        "selected_path": grab(r"Selected Path:\s*(.+)", text),
        "selected_relay": grab(r"Selected Relay:\s*(.+)", text),
        "kg_used": grab(r"KG Used:\s*(.+)", text),
        "path_reasoning_used": grab(r"Path Reasoning Used:\s*(.+)", text),
        "policy_type": grab(r"Policy Type:\s*(.+)", text),
        "configured_bandwidth_mbps": grab_float(r"Configured Bandwidth Mbps:\s*([\d.]+)", text),
        "configured_delay": grab(r"Configured Delay:\s*(.+)", text),
        "configured_loss_percent": grab_float(r"Configured Loss Percent:\s*([\d.]+)", text),
        "rtt_avg_ms": parse_rtt_avg(text),
        "throughput_mbps": parse_throughput(text),
        "measured_packet_loss_percent": parse_packet_loss(text),
        "sfc_selection_seconds": grab_float(r"SFC Selection Seconds:\s*([\d.]+)", text),
        "kg_update_seconds": grab_float(r"KG Update Seconds:\s*([\d.]+)", text),
        "path_aware_sfc_selection_seconds": grab_float(r"Path-Aware SFC Selection Seconds:\s*([\d.]+)", text),
        "rule_install_seconds": grab_float(r"Rule Install Seconds:\s*([\d.]+)", text),
        "deployment_seconds": grab_float(r"Policy Deployment Experiment Seconds:\s*([\d.]+)", text),
        "result_push_seconds": grab_float(r"Result Push Seconds:\s*([\d.]+)", text),
        "total_pipeline_seconds": grab_float(r"Total Pipeline Seconds:\s*([\d.]+)", text),
        "total_seconds": grab_float(r"Total Seconds:\s*([\d.]+)", text),
        "result_file": grab(r"Result File:\s*(.+)", text),
    })


if not rows:
    raise SystemExit("No .txt result files found in comparative_results.")

with open(OUT_CSV, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"[OK] Generated {OUT_CSV}")
