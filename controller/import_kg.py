import json
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "netprompt123"

driver = GraphDatabase.driver(
    URI,
    auth=(USER, PASSWORD)
)

with open("drone_sfc_kg.json") as f:
    kg = json.load(f)

nodes = kg["knowledge_graph"]["nodes"]
relationships = kg["knowledge_graph"]["relationships"]

with driver.session() as session:

    # Clear database
    session.run("MATCH (n) DETACH DELETE n")

    # Create nodes
    for node in nodes:

        node_id = node["id"]
        node_type = node["type"]

        props = dict(node)

        query = f"""
        MERGE (n:{node_type} {{id:$id}})
        SET n += $props
        """

        session.run(
            query,
            id=node_id,
            props=props
        )

    # Create relationships
    for rel in relationships:

        source = rel["source"]
        relation = rel["relation"]
        target = rel["target"]

        query = f"""
        MATCH (a {{id:$source}})
        MATCH (b {{id:$target}})
        MERGE (a)-[r:{relation}]->(b)
        """

        session.run(
            query,
            source=source,
            target=target
        )

print("KG imported successfully")

driver.close()
