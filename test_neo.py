from neo4j import GraphDatabase
import logging

logging.basicConfig(level=logging.INFO)
try:
    driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "password"))
    driver.verify_connectivity()
    print("Success bolt://")
except Exception as e:
    print("Failed bolt://", e)

try:
    driver = GraphDatabase.driver("neo4j://localhost:7687", auth=("neo4j", "password"))
    driver.verify_connectivity()
    print("Success neo4j://")
except Exception as e:
    print("Failed neo4j://", e)
