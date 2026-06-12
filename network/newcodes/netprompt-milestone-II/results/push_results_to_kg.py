import csv
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

CSV_FILE = "/home/cc/netprompt-milestone-II/results/milestone2_results_clean.csv"

driver = GraphDatabase.driver(
    URI,
    auth=(USER, PASSWORD)
)

with driver.session() as session:

    with open(CSV_FILE) as f:
        reader = csv.DictReader(f)

        for row in reader:
            scenario = row["scenario"]
            selected_sfc = row["selected_sfc"]
            result_id = f"{scenario}_{selected_sfc}"

            throughput = (
                float(row["throughput_mbps"])
                if row["throughput_mbps"]
                else None
            )

            session.run(
                """
                MERGE (r:Milestone2ScenarioResult {id:$id})
                SET r.scenario=$scenario,
                    r.recommended_sfc=$recommended_sfc,
                    r.selected_sfc=$selected_sfc,
                    r.sfc_priority=$sfc_priority,
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
                scenario=scenario,
                recommended_sfc=row["recommended_sfc"],
                selected_sfc=selected_sfc,
                sfc_priority=row["sfc_priority"],
                configured_bandwidth_mbps=float(row["configured_bandwidth_mbps"]),
                configured_delay=row["configured_delay"],
                configured_loss_percent=float(row["configured_loss_percent"]),
                throughput_mbps=throughput,
                rtt_avg_ms=float(row["rtt_avg_ms"]) if row["rtt_avg_ms"] else None,
                measured_packet_loss_percent=float(row["measured_packet_loss_percent"]) if row["measured_packet_loss_percent"] else None,
                source_file=row["source_file"]
            )

            session.run(
                """
                MATCH (r:Milestone2ScenarioResult {id:$result_id})
                MATCH (s:SFCTemplate {id:$selected_sfc})
                MERGE (r)-[:USED_SFC]->(s)
                """,
                result_id=result_id,
                selected_sfc=selected_sfc
            )

print("Milestone II results pushed directly to Neo4j from network-node.")

driver.close()
