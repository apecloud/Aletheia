import argparse
import logging
import time
from sqlalchemy import create_engine, text
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor
from graph_db_client import NebulaGraphClient

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GraphIngestionAgent")

class NodeExtractionQuery(BaseModel):
    sql_query: str = Field(description="SQL query to extract the business object. Must return an 'id' column uniquely identifying the node.")
    node_label: str = Field(description="The graph label (TAG in Nebula) for this vertex. E.g., Customer, Order.")
    ngql_schema: str = Field(description="Nebula Graph nGQL CREATE TAG command. E.g., CREATE TAG Customer (id string, name string)")

class EdgeExtractionQuery(BaseModel):
    sql_query: str = Field(description="SQL query to extract the relationship. Must return 'source_id' and 'target_id'.")
    relationship_type: str = Field(description="The graph relationship type (EDGE in Nebula). E.g., PLACED_ORDER.")
    ngql_schema: str = Field(description="Nebula Graph nGQL CREATE EDGE command. E.g., CREATE EDGE PLACED_ORDER ()")

class GraphIngestionAgent:
    def __init__(self, source_db_url: str, target_db_url: str, nebula_ip: str, nebula_port: int, nebula_user: str, nebula_pass: str, model_name: str):
        self.source_engine = create_engine(source_db_url)
        self.target_engine = create_engine(target_db_url)
        
        self.graph_client = NebulaGraphClient(
            ip=nebula_ip, 
            port=nebula_port, 
            user=nebula_user, 
            password=nebula_pass
        )
        self.graph_client.connect()
        
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Graph Ingestion Agent with model: {self.model_name}")

    def __del__(self):
        if hasattr(self, 'graph_client'):
            self.graph_client.close()

    def fetch_ontology_context(self) -> str:
        context = "=== Aletheia Data Ontology ===\n\n"
        with self.target_engine.connect() as conn:
            tables = conn.execute(text("SELECT id, table_name FROM aletheia_extracted_tables")).fetchall()
            for t in tables:
                context += f"Table: {t[1]}\nColumns: "
                cols = conn.execute(text(f"SELECT column_name, data_type FROM aletheia_extracted_columns WHERE table_id={t[0]}")).fetchall()
                context += ", ".join([f"{c[0]} ({c[1]})" for c in cols]) + "\n\n"
        return context

    def run_phase_1(self, client, ontology_context):
        logger.info("Phase 1: Nebula Graph Schema & Node Ingestion")
        with self.target_engine.connect() as conn:
            objects = conn.execute(text("SELECT id, name, description FROM aletheia_business_objects")).fetchall()
            
        for obj in objects:
            prompt = f"""
You are the Graph Data Integration Engine (Nebula Graph). You need to define and ingest the Business Object "{obj[1]}".
Description: {obj[2]}
{ontology_context}

Write a safe MySQL query to extract ALL instances, and the matching Nebula Graph CREATE TAG command.
Rules:
1. SQL MUST SELECT a primary identifier aliased as `id`.
2. nGQL MUST define the tag properties. Use basic types (string). ALL properties MUST be of type string. Enclose the TAG name in backticks. E.g., CREATE TAG `{obj[1].replace(' ', '')}` (companyname string, address string)
"""
            try:
                extraction = client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=NodeExtractionQuery,
                )
                
                logger.info(f"Creating TAG Schema: {extraction.node_label}")
                try:
                    self.graph_client.execute_query(extraction.ngql_schema)
                    time.sleep(15) # Wait for schema sync
                except Exception as e:
                    logger.warning(f"Schema issue or already exists: {e}")
                    time.sleep(15)
                
                with self.source_engine.connect() as source_conn:
                    logger.debug(f"Executing SQL Extraction for {obj[1]}: {extraction.sql_query}")
                    # fetchall as list of dicts for the graph client
                    rows_result = source_conn.execute(text(extraction.sql_query)).mappings().all()
                    rows = [dict(r) for r in rows_result]
                
                logger.info(f"Upserting {len(rows)} vertices for '{extraction.node_label}' into Nebula...")
                self.graph_client.insert_vertices(extraction.node_label, rows)
                        
                logger.info(f"✅ Successfully processed batch for {obj[1]}")
                
            except Exception as e:
                logger.error(f"❌ Failed to process object {obj[1]}: {e}")

    def run_phase_2(self, client, ontology_context):
        logger.info("Phase 2: Nebula Graph Schema & Edge Ingestion")
        with self.target_engine.connect() as conn:
            # Join aletheia_business_links with aletheia_business_objects to get the names
            links = conn.execute(text("""
                SELECT l.id, l.description, s.name AS source_name, t.name AS target_name 
                FROM aletheia_business_links l
                JOIN aletheia_business_objects s ON l.source_object_id = s.id
                JOIN aletheia_business_objects t ON l.target_object_id = t.id
            """)).mappings().all()
            
        for link in links:
            prompt = f"""
You are the Graph Data Integration Engine (Nebula Graph). You need to define and ingest a Relationship (Edge) between two Business Objects.
Source Object: "{link['source_name']}"
Target Object: "{link['target_name']}"
Relationship Description: {link['description']}

{ontology_context}

Write a safe MySQL query to extract ALL instances of this relationship, and the matching Nebula Graph CREATE EDGE command.
Rules:
1. SQL MUST SELECT the source's primary identifier aliased as `source_id` AND the target's primary identifier aliased as `target_id`.
2. nGQL MUST define edge properties (if any exist). Use basic types (string). ALL properties MUST be of type string. Enclose the EDGE name in backticks.
3. Example nGQL: CREATE EDGE `PLACED_ORDER` (order_time string)
4. Do NOT include `source_id` or `target_id` in the nGQL properties definition. Nebula inherently stores src and dst for edges.
"""
            try:
                extraction = client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=EdgeExtractionQuery,
                )
                
                logger.info(f"Creating EDGE Schema: {extraction.relationship_type}")
                try:
                    self.graph_client.execute_query(extraction.ngql_schema)
                    time.sleep(15) # Wait for schema sync
                except Exception as e:
                    logger.warning(f"Edge Schema issue or already exists: {e}")
                    time.sleep(15)
                
                with self.source_engine.connect() as source_conn:
                    logger.debug(f"Executing SQL Edge Extraction for {extraction.relationship_type}: {extraction.sql_query}")
                    rows_result = source_conn.execute(text(extraction.sql_query)).mappings().all()
                    rows = [dict(r) for r in rows_result]
                
                logger.info(f"Upserting {len(rows)} edges for '{extraction.relationship_type}' into Nebula...")
                self.graph_client.insert_edges(extraction.relationship_type, rows)
                        
                logger.info(f"✅ Successfully processed edge batch for {extraction.relationship_type}")
                
            except Exception as e:
                logger.error(f"❌ Failed to process link ID {link['id']} ({link['source_name']} -> {link['target_name']}): {e}")

    def run(self, phase: str = "all"):
        logger.info("Gathering ontology context from PostGIS...")
        ontology_context = self.fetch_ontology_context()
        client = instructor.from_litellm(completion)
        
        if phase in ["1", "all"]:
            self.run_phase_1(client, ontology_context)
            
        if phase in ["2", "all"]:
            self.run_phase_2(client, ontology_context)

        logger.info("Nebula ingestion complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data")
    parser.add_argument("--target", default="postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology")
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-pass", default="nebula")
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    parser.add_argument("--phase", default="all", choices=["1", "2", "all"], help="Which phase to run (1 for nodes, 2 for edges, all for both)")
    args = parser.parse_args()
    
    agent = GraphIngestionAgent(
        source_db_url=args.source, target_db_url=args.target, 
        nebula_ip=args.nebula_ip, nebula_port=args.nebula_port, nebula_user=args.nebula_user, nebula_pass=args.nebula_pass,
        model_name=args.model
    )
    agent.run(phase=args.phase)
