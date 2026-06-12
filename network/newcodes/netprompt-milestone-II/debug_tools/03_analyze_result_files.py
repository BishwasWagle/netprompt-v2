from pathlib import Path
import re
import csv


BASE = Path("/home/cc/netprompt-milestone-II")
RESULTS = BASE / "results"
OUT = BASE / "debug_results/03_result_file_diagnosis.csv"

rows = []

for file in sorted(RESULTS.glob("*.txt")):
    text = file.read_text(errors="ignore")

    scenario = ""
    selected_sfc = ""
    policy_type = ""

    for pat, key in [
        (r"Scenario:\s*(.+)", "scenario"),
        (r"Selected SFC:\s*(.+)", "selected_sfc"),
        (r"Policy Type:\s*(.+)", "policy_type"),
    ]:
        m = re.search(pat, text)
        if m:
            if key == "scenario":
                scenario = m.group(1).strip()
            elif key == "selected_sfc":
                selected_sfc = m.group(1).strip()
            elif key == "policy_type":
                policy_type = m.group(1).strip()

    pingall = ""
    m = re.search(r"PingAll Dropped Percent:\s*([\d.]+)", text)
    if m:
        pingall = float(m.group(1))

    rtt_avg = ""
    m = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/[\d.]+/[\d.]+", text)
    if m:
        rtt_avg = float(m.group(1))

    throughput = ""
    matches = re.findall(r"([\d.]+)\s+Mbits/sec", text)
    if matches:
        throughput = float(matches[-1])

    tcp_failed = "tcp connect failed" in text or "Connection timed out" in text
    has_iperf_output = "Client connecting" in text
    has_mbits = "Mbits/sec" in text

    rows.append({
        "file": file.name,
        "scenario": scenario,
        "selected_sfc": selected_sfc,
        "policy_type": policy_type,
        "pingall_drop_percent": pingall,
        "rtt_avg_ms": rtt_avg,
        "throughput_mbps": throughput,
        "tcp_failed": tcp_failed,
        "has_iperf_output": has_iperf_output,
        "has_mbits": has_mbits,
    })

OUT.parent.mkdir(parents=True, exist_ok=True)

with OUT.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

print(f"[OK] Saved {OUT}")
for r in rows:
    print(r)
