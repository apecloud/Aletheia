from agents.graph_db_client import NebulaGraphClient

client = NebulaGraphClient(ip="127.0.0.1", port=9669, user="root", password="nebula", space="aletheia")
client.connect()

try:
    res = client.execute_query("MATCH (o:`Order`)-[e:`CONSISTS_OF`]->(p:`Product`) RETURN id(p) AS product_id, sum(e.Quantity) AS total_qty ORDER BY total_qty DESC LIMIT 5;")
    if not res.is_empty():
        print("REAL ORDER:", res.rows())
except Exception as e:
    print("Error:", e)
