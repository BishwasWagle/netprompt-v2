import csv
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

CSV_FILE = "/home/cc/netprompt-milestone-II/results/final_milestone2_results_clean.csv"

driver = GraphDatabase.driver(
    URI,
    auth=(USER, PASSWORD)
)

with driver.session() as session:

    with open(CSV_FILE) as f:
        reader = csv.DictReader(f)

        for row in reader:
            result_id = f"{row['experiment_type']}_{row['scenario']}_{row['selected_sfc']}"

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
                    r.rtt_avg_ms=$rtt_avg_ms,
                    r.measured_packet_loss_percent=$measured_packet_loss_percent,
                    r.source_file=$source_file,
                    r.updated_by="network-node"
                """,
                id=result_id,
                experiment_type=row["experiment_type"],
                scenario=row["scenario"],
                selected_sfc=row["selected_sfc"],
                policy_type=row["policy_type"],
                p4_json=row["p4_json"],
                rule_file=row["rule_file"],
                access_rules=row["access_rules"],
                relay_rules=row["relay_rules"],
                backup_rules=row["backup_rules"],
                configured_bandwidth_mbps=float(row["configured_bandwidth_mbps"]) if row["configured_bandwidth_mbps"] else None,
                configured_delay=row["configured_delay"],
                configured_loss_percent=float(row["configured_loss_percent"]) if row["configured_loss_percent"] else None,
                throughput_mbps=float(row["throughput_mbps"]) if row["throughput_mbps"] else None,
                rtt_avg_ms=float(row["rtt_avg_ms"]) if row["rtt_avg_ms"] else None,
                measured_packet_loss_percent=float(row["measured_packet_loss_percent"]) if row["measured_packet_loss_percent"] else None,
                source_file=row["source_file"]
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

print("Final Milestone II results pushed to Neo4j.")
driver.close()
