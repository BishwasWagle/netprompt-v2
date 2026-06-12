import sys
from datetime import datetime
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

SCENARIO_STATE = {
    "low_latency": {
        "s1_status": "Active",
        "s2_status": "Active",
        "s3_status": "Standby",
        "selected_path": "PrimaryPath",
        "reason": "Low-latency traffic uses the primary relay path."
    },
    "baseline": {
        "s1_status": "Active",
        "s2_status": "Active",
        "s3_status": "Standby",
        "selected_path": "PrimaryPath",
        "reason": "Baseline traffic uses the primary relay path."
    },
    "congestion": {
        "s1_status": "Active",
        "s2_status": "Degraded",
        "s3_status": "Active",
        "selected_path": "BackupPath",
        "reason": "Congestion degrades the primary relay path, so backup path is preferred."
    },
    "ddil": {
        "s1_status": "Active",
        "s2_status": "Degraded",
        "s3_status": "Active",
        "selected_path": "BackupPath",
        "reason": "DDIL conditions require reliable backup relay behavior."
    },
    "relay_failure": {
        "s1_status": "Active",
        "s2_status": "Failed",
        "s3_status": "Active",
        "selected_path": "BackupPath",
        "reason": "Primary relay failed, so ReliableRelaySFC should use backup path."
    },
    "battery_depletion": {
        "s1_status": "Active",
        "s2_status": "Active",
        "s3_status": "Standby",
        "selected_path": "EssentialFlowsOnly",
        "reason": "Energy-aware mode limits traffic to essential flows."
    }
}


def update_state(scenario):
    if scenario not in SCENARIO_STATE:
        raise ValueError(f"Unknown scenario: {scenario}")

    state = SCENARIO_STATE[scenario]
    timestamp = datetime.utcnow().isoformat()

    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

    with driver.session() as session:
        session.run(
            """
            MATCH (s1:ProgrammableSwitch {id:"s1"})
            SET s1.status=$s1_status,
                s1.last_updated=$timestamp
            """,
            s1_status=state["s1_status"],
            timestamp=timestamp
        )

        session.run(
            """
            MATCH (s2:ProgrammableSwitch {id:"s2"})
            SET s2.status=$s2_status,
                s2.last_updated=$timestamp
            """,
            s2_status=state["s2_status"],
            timestamp=timestamp
        )

        session.run(
            """
            MATCH (s3:ProgrammableSwitch {id:"s3"})
            SET s3.status=$s3_status,
                s3.last_updated=$timestamp
            """,
            s3_status=state["s3_status"],
            timestamp=timestamp
        )

        session.run(
            """
            MERGE (p:PathDecision {id:$scenario})
            SET p.scenario=$scenario,
                p.selected_path=$selected_path,
                p.reason=$reason,
                p.timestamp=$timestamp,
                p.updated_by="network-node"
            """,
            scenario=scenario,
            selected_path=state["selected_path"],
            reason=state["reason"],
            timestamp=timestamp
        )

        session.run(
            """
            MATCH (p:PathDecision {id:$scenario})
            MATCH (s1:ProgrammableSwitch {id:"s1"})
            MERGE (p)-[:STARTS_AT]->(s1)
            """,
            scenario=scenario
        )

        if state["selected_path"] == "PrimaryPath":
            session.run(
                """
                MATCH (p:PathDecision {id:$scenario})
                MATCH (s2:ProgrammableSwitch {id:"s2"})
                MERGE (p)-[:SELECTED_RELAY]->(s2)
                """,
                scenario=scenario
            )

        elif state["selected_path"] == "BackupPath":
            session.run(
                """
                MATCH (p:PathDecision {id:$scenario})
                MATCH (s3:ProgrammableSwitch {id:"s3"})
                MERGE (p)-[:SELECTED_RELAY]->(s3)
                """,
                scenario=scenario
            )

    driver.close()

    print(f"Updated KG topology state for scenario: {scenario}")
    print(f"Selected path: {state['selected_path']}")
    print(f"Reason: {state['reason']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 update_topology_state.py <scenario>")
        sys.exit(1)

    update_state(sys.argv[1])
