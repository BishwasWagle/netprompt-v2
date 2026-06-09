import json
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"


def get_switch_statuses():
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

    with driver.session() as session:
        result = session.run(
            """
            MATCH (s:ProgrammableSwitch)
            WHERE s.id IN ["s1", "s2", "s3"]
            RETURN s.id AS id, s.status AS status, s.role AS role
            ORDER BY s.id
            """
        )

        switches = {
            record["id"]: {
                "status": record["status"],
                "role": record["role"]
            }
            for record in result
        }

    driver.close()
    return switches


def select_path_from_kg():
    switches = get_switch_statuses()

    s2_status = switches.get("s2", {}).get("status", "Unknown")
    s3_status = switches.get("s3", {}).get("status", "Unknown")

    if s2_status in ["Failed", "Degraded"] and s3_status in ["Active", "Standby"]:
        selected_path = "BackupPath"
        selected_relay = "s3"
        reason = f"Primary relay s2 is {s2_status}, so backup relay s3 is selected."

    elif s2_status == "Active":
        selected_path = "PrimaryPath"
        selected_relay = "s2"
        reason = "Primary relay s2 is active, so primary path is selected."

    elif s3_status in ["Active", "Standby"]:
        selected_path = "BackupPath"
        selected_relay = "s3"
        reason = f"Primary relay status is {s2_status}, so backup relay s3 is selected."

    else:
        selected_path = "NoValidPath"
        selected_relay = "None"
        reason = "No valid relay path is currently available."

    return {
        "selected_path": selected_path,
        "selected_relay": selected_relay,
        "reason": reason,
        "switches": switches
    }


if __name__ == "__main__":
    print(json.dumps(select_path_from_kg(), indent=2))
