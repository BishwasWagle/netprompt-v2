import re
import csv
from pathlib import Path


RESULT_DIR = Path("/home/cc/netprompt-milestone-II/comparative_results")
OUT_CSV = RESULT_DIR / "comparative_results_clean.csv"


FIELDS = [
    "source_file",
    "method",
    "scenario",
    "selected_sfc",
    "selected_path",
    "selected_relay",
    "kg_used",
    "path_reasoning_used",
    "policy_type",
    "edge_interface_mode",
    "configured_bandwidth_mbps",
    "configured_delay",
    "configured_loss_percent",
    "rtt_avg_ms",
    "throughput_mbps",
    "udp_jitter_ms",
    "udp_loss_percent",
    "measured_packet_loss_percent",
    "sfc_selection_seconds",
    "kg_update_seconds",
    "path_aware_sfc_selection_seconds",
    "rule_install_seconds",
    "deployment_seconds",
    "result_push_seconds",
    "total_pipeline_seconds",
    "total_seconds",
    "result_file",
]


def grab(pattern, text, default=""):
    m = re.search(pattern, text)
    return m.group(1).strip() if m else default


def grab_float(pattern, text):
    value = grab(pattern, text, "")
    try:
        return float(value)
    except Exception:
        return ""


def load_linked_result_text(summary_text):
    result_file = grab(r"Result File:\s*(.+)", summary_text)
    if not result_file:
        return ""

    p = Path(result_file)
    if p.exists():
        return p.read_text(errors="ignore")

    return ""


def parse_rtt_avg(text):
    matches = re.findall(
        r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/[\d.]+",
        text,
    )
    if not matches:
        return ""

    vals = [float(x) for x in matches]
    return round(sum(vals) / len(vals), 6)


def normalize_bandwidth(value, unit):
    value = float(value)

    if unit == "K":
        return value / 1000.0
    if unit == "M":
        return value
    if unit == "G":
        return value * 1000.0

    return value


def extract_throughput_blocks(text):
    return re.findall(
        r"Throughput Result .*?-> edge:\n(.*?)(?=\nTCP Port Check|\nIperf Listen Check|\nIperf Server Process|\nIperf Server Log|\nThroughput Result|\nLatency Result|\Z)",
        text,
        flags=re.S,
    )


def parse_throughput(text):
    direct = grab_float(r"Throughput Mbps:\s*([\d.]+)", text)
    if direct != "":
        return direct

    values = []

    for block in extract_throughput_blocks(text):
        for value, unit in re.findall(r"([\d.]+)\s+([KMG])bits/sec", block):
            values.append(normalize_bandwidth(value, unit))

    if values:
        return round(sum(values) / len(values), 6)

    matches = re.findall(r"([\d.]+)\s+([KMG])bits/sec", text)
    if matches:
        vals = [normalize_bandwidth(v, u) for v, u in matches[:3]]
        return round(sum(vals) / len(vals), 6)

    return ""


def parse_udp_server_stats(text):
    blocks = re.findall(
        r"Iperf Server Log .*?-> edge:\n(.*?)(?=\nThroughput Result|\nLatency Result|\Z)",
        text,
        flags=re.S,
    )

    jitters = []
    losses = []

    for block in blocks:
        m = re.search(
            r"\[\s*\d+\]\s+[\d.]+-[\d.]+\s+sec\s+"
            r"[\d.]+\s+\w+Bytes\s+[\d.]+\s+[KMG]bits/sec\s+"
            r"([\d.]+)\s+ms\s+\d+/\d+\s+\(([\d.eE+-]+)%\)",
            block,
        )
        if m:
            try:
                loss_val = float(m.group(2))
                if loss_val <= 100:
                    jitters.append(float(m.group(1)))
                    losses.append(loss_val)
            except Exception:
                pass

    jitter = round(sum(jitters) / len(jitters), 6) if jitters else ""
    loss = round(sum(losses) / len(losses), 6) if losses else ""

    return jitter, loss


def parse_packet_loss(text, udp_loss):
    direct = grab_float(r"Measured Packet Loss Percent:\s*([\d.]+)", text)
    if direct != "":
        return direct

    # Prefer end-to-end ping latency packet loss, not pingAll, when available.
    latency_sections = re.findall(
        r"Latency Result .*?-> edge:\n(.*?)(?=\nLatency Result|\Z)",
        text,
        flags=re.S,
    )

    latency_losses = []
    for section in latency_sections:
        for loss in re.findall(r"(\d+(?:\.\d+)?)%\s+packet loss", section):
            latency_losses.append(float(loss))

    if latency_losses:
        return round(sum(latency_losses) / len(latency_losses), 6)

    if udp_loss != "":
        return udp_loss

    pingall = grab_float(r"PingAll Dropped Percent:\s*([\d.]+)", text)
    if pingall != "":
        return pingall

    losses = re.findall(r"(\d+(?:\.\d+)?)%\s+packet loss", text)
    if losses:
        vals = [float(x) for x in losses]
        return round(sum(vals) / len(vals), 6)

    return ""


rows = []

for file in sorted(RESULT_DIR.glob("*.txt")):
    summary_text = file.read_text(errors="ignore")
    linked_text = load_linked_result_text(summary_text)

    combined_text = summary_text + "\n\n" + linked_text

    udp_jitter, udp_loss = parse_udp_server_stats(combined_text)

    row = {
        "source_file": file.name,
        "method": grab(r"Method:\s*(.+)", summary_text),
        "scenario": grab(r"Scenario:\s*(.+)", summary_text),
        "selected_sfc": grab(r"Selected SFC:\s*(.+)", summary_text),
        "selected_path": grab(r"Selected Path:\s*(.+)", summary_text),
        "selected_relay": grab(r"Selected Relay:\s*(.+)", summary_text),
        "kg_used": grab(r"KG Used:\s*(.+)", summary_text),
        "path_reasoning_used": grab(r"Path Reasoning Used:\s*(.+)", summary_text),
        "policy_type": grab(r"Policy Type:\s*(.+)", combined_text),
        "edge_interface_mode": grab(r"Edge Interface Mode:\s*(.+)", combined_text),
        "configured_bandwidth_mbps": grab_float(r"Configured Bandwidth(?: Mbps)?:\s*([\d.]+)", combined_text),
        "configured_delay": grab(r"Configured Delay:\s*(.+)", combined_text),
        "configured_loss_percent": grab_float(r"Configured Loss(?: Percent)?:\s*([\d.]+)", combined_text),
        "rtt_avg_ms": parse_rtt_avg(combined_text),
        "throughput_mbps": parse_throughput(combined_text),
        "udp_jitter_ms": udp_jitter,
        "udp_loss_percent": udp_loss,
        "measured_packet_loss_percent": parse_packet_loss(combined_text, udp_loss),
        "sfc_selection_seconds": grab_float(r"SFC Selection Seconds:\s*([\d.]+)", summary_text),
        "kg_update_seconds": grab_float(r"KG Update Seconds:\s*([\d.]+)", summary_text),
        "path_aware_sfc_selection_seconds": grab_float(r"Path-Aware SFC Selection Seconds:\s*([\d.]+)", summary_text),
        "rule_install_seconds": grab_float(r"Rule Install Seconds:\s*([\d.]+)", summary_text),
        "deployment_seconds": grab_float(r"Policy Deployment Experiment Seconds:\s*([\d.]+)", summary_text),
        "result_push_seconds": grab_float(r"Result Push Seconds:\s*([\d.]+)", summary_text),
        "total_pipeline_seconds": grab_float(r"Total Pipeline Seconds:\s*([\d.]+)", summary_text),
        "total_seconds": grab_float(r"Total Seconds:\s*([\d.]+)", summary_text),
        "result_file": grab(r"Result File:\s*(.+)", summary_text),
    }

    rows.append(row)


with open(OUT_CSV, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)

print(f"[OK] Generated {OUT_CSV}")
