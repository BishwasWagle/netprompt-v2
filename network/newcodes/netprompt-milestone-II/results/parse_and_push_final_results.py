import re
import json
import csv
from pathlib import Path
from neo4j import GraphDatabase


# -----------------------------
# Neo4j Configuration
# -----------------------------
URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

RESULTS_DIR = Path("/home/cc/netprompt-milestone-II/results")

CSV_OUTPUT = RESULTS_DIR / "final_milestone2_results_clean.csv"
JSON_OUTPUT = RESULTS_DIR / "final_milestone2_results_clean.json"


# -----------------------------
# Helpers
# -----------------------------
def get(pattern, text, default=None):
    match = re.search(pattern, text)
    return match.group(1).strip() if match else default


def parse_float(value):
    if value in [None, ""]:
        return None
    try:
        return float(value)
    except Exception:
        return None


def parse_result_file(file_path):
    text = file_path.read_text(errors="ignore")

    experiment_type = get(r"Experiment Type:\s*(.+)", text, "Unknown")
    scenario = get(r"Scenario:\s*(.+)", text, "Unknown")
    selected_sfc = get(r"Selected SFC:\s*(.+)", text, "Unknown")

    p4_json = get(r"P4 JSON:\s*(.+)", text, "")
    rule_file = get(r"Rule File:\s*(.+)", text, "")
    access_rules = get(r"Access Rules:\s*(.+)", text, "")
    relay_rules = get(r"Relay Rules:\s*(.+)", text, "")
    backup_rules = get(r"Backup Rules:\s*(.+)", text, "")
    policy_type = get(r"Policy Type:\s*(.+)", text, "")

    configured_bandwidth_mbps = parse_float(
        get(r"Configured Bandwidth:\s*([\d.]+)", text, "")
    )
    configured_delay = get(r"Configured Delay:\s*(.+)", text, "")
    configured_loss_percent = parse_float(
        get(r"Configured Loss:\s*([\d.]+)", text, "")
    )

    rtt_matches = re.findall(
        r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)",
        text
    )

    if rtt_matches:
        rtt_min_ms, rtt_avg_ms, rtt_max_ms, rtt_mdev_ms = map(
            float,
            rtt_matches[0]
        )
    else:
        rtt_min_ms = rtt_avg_ms = rtt_max_ms = rtt_mdev_ms = None

    packet_matches = re.findall(
        r"(\d+) packets transmitted, (\d+) received",
        text
    )

    if packet_matches:
        transmitted, received = map(int, packet_matches[0])
        measured_packet_loss_percent = round(
            (transmitted - received) / transmitted * 100,
            2
        )
    else:
        measured_packet_loss_percent = None

    throughput_matches = re.findall(
        r"([\d.]+)\s+Mbits/sec",
        text
    )

    throughput_mbps = (
        float(throughput_matches[-1])
        if throughput_matches
        else None
    )

    return {
        "experiment_type": experiment_type,
        "scenario": scenario,
        "selected_sfc": selected_sfc,
        "policy_type": policy_type,
        "p4_json": p4_json,
        "rule_file": rule_file,
        "access_rules": access_rules,
        "relay_rules": relay_rules,
        "backup_rules": backup_rules,
        "configured_bandwidth_mbps": configured_bandwidth_mbps,
        "configured_delay": configured_delay,
        "configured_loss_percent": configured_loss_percent,
        "throughput_mbps": throughput_mbps,
        "rtt_min_ms": rtt_min_ms,
        "rtt_avg_ms": rtt_avg_ms,
        "rtt_max_ms": rtt_max_ms,
        "rtt_mdev_ms": rtt_mdev_ms,
        "measured_packet_loss_percent": measured_packet_loss_percent,
        "source_file": file_path.name
    }


def parse_all_results():
    files = sorted(
        list(RESULTS_DIR.glob("dynamic_p4_*.txt")) +
        list(RESULTS_DIR.glob("multihop_*.txt"))
    )

    if not files:
        raise FileNotFoundError(
            "No result files found. Expected dynamic_p4_*.txt or multihop_*.txt"
        )

    rows = []

    for file_path in files:
        rows.append(parse_result_file(file_path))

    return rows


def write_csv_json(rows):
    with open(JSON_OUTPUT, "w") as f:
        json.dump(rows, f, indent=2)

    with open(CSV_OUTPUT, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated JSON: {JSON_OUTPUT}")
    print(f"Generated CSV: {CSV_OUTPUT}")


def push_to_neo4j(rows):
    driver = GraphDatabase.driver(
        URI,
        auth=(USER, PASSWORD)
    )

    with driver.session() as session:
        for row in rows:
            result_id = (
                f"{row['experiment_type']}_"
                f"{row['scenario']}_"
                f"{row['selected_sfc']}"
            )

            session.run(
                """
                MERGE (r:FinalMilestone2Result {id:$id})
                SET r.experiment_type=$experiment_type,
                    r.scenario=$scenario,
                    r.selected_sfc=$selected_sfc,
                    r.policy_type=$policy_type,
                    r.p4_json=$p4_json,
                    r.rule_file=$rule_file,
                    r.access_rules=$access_rules,
                    r.relay_rules=$relay_rules,
                    r.backup_rules=$backup_rules,
                    r.configured_bandwidth_mbps=$configured_bandwidth_mbps,
                    r.configured_delay=$configured_delay,
                    r.configured_loss_percent=$configured_loss_percent,
                    r.throughput_mbps=$throughput_mbps,
                    r.rtt_min_ms=$rtt_min_ms,
                    r.rtt_avg_ms=$rtt_avg_ms,
                    r.rtt_max_ms=$rtt_max_ms,
                    r.rtt_mdev_ms=$rtt_mdev_ms,
                    r.measured_packet_loss_percent=$measured_packet_loss_percent,
                    r.source_file=$source_file,
                    r.updated_by="network-node"
                """,
                id=result_id,
                **row
            )

            session.run(
                """
                MATCH (r:FinalMilestone2Result {id:$result_id})
                MATCH (s:SFCTemplate {id:$selected_sfc})
                MERGE (r)-[:USED_SFC]->(s)
                """,
                result_id=result_id,
                selected_sfc=row["selected_sfc"]
            )

            session.run(
                """
                MATCH (r:FinalMilestone2Result {id:$result_id})
                MATCH (p:P4PolicyMapping {id:$selected_sfc})
                MERGE (r)-[:USED_P4_POLICY]->(p)
                """,
                result_id=result_id,
                selected_sfc=row["selected_sfc"]
            )

    driver.close()
    print("Pushed final parsed results into Neo4j.")


def main():
    print("Parsing final Milestone II result files...")
    rows = parse_all_results()

    print(f"Parsed {len(rows)} result files.")

    write_csv_json(rows)

    print("Pushing results to Neo4j...")
    push_to_neo4j(rows)

    print("Done.")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
