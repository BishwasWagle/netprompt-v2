import sys
import json
import subprocess
from neo4j import GraphDatabase

URI = "bolt://controller-node:7687"
USER = "neo4j"
PASSWORD = "netprompt123"


SCENARIO_TO_GOAL = {
    "low_latency": "Latency",
    "baseline": "Latency",
    "congestion": "Reliability",
    "ddil": "Reliability",
    "relay_failure": "Reliability",
    "battery_depletion": "Energy"
}


def get_kg_path_decision():
    output = subprocess.check_output(
        [
            "python3",
            "/home/cc/netprompt-milestone-II/experiments/kg_path_selector.py"
        ],
        text=True
    )
    return json.loads(output)


def select_sfc_from_kg(goal):
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))

    with driver.session() as session:
        result = session.run(
            """
            MATCH (s:SFCTemplate)
            WHERE s.optimization_goal = $goal
            RETURN s.id AS sfc
            LIMIT 1
            """,
            goal=goal
        )

        record = result.single()

    driver.close()

    if not record:
        raise RuntimeError(f"No SFC template found for goal: {goal}")

    return record["sfc"]


def select_sfc(scenario):
    if scenario not in SCENARIO_TO_GOAL:
        raise ValueError(f"Unknown scenario: {scenario}")

    path_decision = get_kg_path_decision()
    selected_path = path_decision["selected_path"]

    if selected_path == "BackupPath":
        goal = "Reliability"

    elif scenario == "battery_depletion":
        goal = "Energy"

    else:
        goal = SCENARIO_TO_GOAL[scenario]

    sfc = select_sfc_from_kg(goal)

    return {
        "scenario": scenario,
        "selected_path": selected_path,
        "selected_relay": path_decision["selected_relay"],
        "path_reason": path_decision["reason"],
        "optimization_goal": goal,
        "selected_sfc": sfc
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 path_aware_sfc_selector.py <scenario>")
        sys.exit(1)

    scenario = sys.argv[1]
    print(json.dumps(select_sfc(scenario), indent=2))
