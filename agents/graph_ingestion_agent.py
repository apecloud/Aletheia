import os
import argparse
import logging
import time
from sqlalchemy import create_engine, text
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor
from graph_db_client import NebulaGraphClient
from ontology_artifacts import ensure_artifact_schema
from tenant_registry import TenantRegistry

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
    def __init__(
        self,
        source_db_url: str,
        target_db_url: str,
        nebula_ip: str,
        nebula_port: int,
        nebula_user: str,
        nebula_pass: str,
        graph_space: str,
        model_name: str,
        tenant_id: str = "default",
    ):
        self.source_engine = create_engine(source_db_url)
        self.target_engine = create_engine(target_db_url)
        self.tenant_id = tenant_id
        self.graph_space = graph_space
        
        self.graph_client = NebulaGraphClient(
            ip=nebula_ip, 
            port=nebula_port, 
            user=nebula_user, 
            password=nebula_pass,
            space=graph_space
        )
        self.graph_client.connect()
        
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(
            "Initialized Graph Ingestion Agent with model=%s tenant=%s graph_database=%s",
            self.model_name,
            self.tenant_id,
            self.graph_space,
        )

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

    def ensure_metadata_columns(self):
        ensure_artifact_schema(self.target_engine)

    def run_phase_1(self, client, ontology_context, include_unapproved: bool):
        logger.info("Phase 1: Nebula Graph Schema & Node Ingestion")
        with self.target_engine.connect() as conn:
            status_filter = "" if include_unapproved else "WHERE a.status = 'approved'"
            objects = conn.execute(text(f"""
                SELECT o.id, o.name, o.description, o.graph_label, o.extraction_sql, o.ngql_schema,
                       COALESCE(a.status, 'legacy') AS artifact_status
                FROM aletheia_business_objects o
                LEFT JOIN aletheia_ontology_artifacts a ON o.artifact_id = a.id
                {status_filter}
                {"AND" if status_filter else "WHERE"} COALESCE(o.project_id, 'default') = :tenant_id
            """), {"tenant_id": self.tenant_id}).mappings().all()
        if not objects and not include_unapproved:
            logger.warning("No approved business object artifacts found. Use --include-unapproved for legacy/demo ingestion.")
            
        for obj in objects:
            if obj['graph_label'] and obj['extraction_sql'] and obj['ngql_schema']:
                logger.info(f"Reusing existing metadata mapping for Node: {obj['name']} -> {obj['graph_label']}")
                extraction = NodeExtractionQuery(
                    sql_query=obj['extraction_sql'],
                    node_label=obj['graph_label'],
                    ngql_schema=obj['ngql_schema']
                )
            else:
                prompt = f"""
You are the Graph Data Integration Engine (Nebula Graph). You need to define and ingest the Business Object "{obj['name']}".
Description: {obj['description']}
{ontology_context}

Write a safe MySQL query to extract ALL instances, and the matching Nebula Graph CREATE TAG command.
Rules:
1. SQL MUST SELECT a primary identifier aliased as `id`.
2. nGQL MUST define the tag properties. Use basic types (string). ALL properties MUST be of type string. Enclose the TAG name in backticks. E.g., CREATE TAG `{obj['name'].replace(' ', '')}` (companyname string, address string)
"""
                try:
                    extraction = client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        response_model=NodeExtractionQuery,
                    )
                    
                    with self.target_engine.begin() as save_conn:
                        save_conn.execute(
                            text("UPDATE aletheia_business_objects SET graph_label = :gl, extraction_sql = :es, ngql_schema = :ns WHERE id = :id"),
                            {"gl": extraction.node_label, "es": extraction.sql_query, "ns": extraction.ngql_schema, "id": obj['id']}
                        )
                except Exception as e:
                    logger.error(f"❌ Failed to extract node mapping for {obj['name']}: {e}")
                    continue

            try:
                logger.info(f"Creating TAG Schema: {extraction.node_label}")
                try:
                    self.graph_client.execute_query(extraction.ngql_schema)
                    time.sleep(5)
                except Exception as e:
                    logger.warning(f"Schema issue or already exists: {e}")
                    time.sleep(2)
                
                with self.source_engine.connect() as source_conn:
                    logger.debug(f"Executing SQL Extraction for {obj['name']}: {extraction.sql_query[:100]}...")
                    rows_result = source_conn.execute(text(extraction.sql_query)).mappings().all()
                    rows = [dict(r) for r in rows_result]
                
                logger.info(f"Upserting {len(rows)} vertices for '{extraction.node_label}' into Nebula...")
                self.graph_client.insert_vertices(extraction.node_label, rows)
                        
                logger.info(f"✅ Successfully processed batch for {obj['name']}")
                
            except Exception as e:
                logger.error(f"❌ Failed to process object {obj['name']}: {e}")

    def run_phase_2(self, client, ontology_context, include_unapproved: bool):
        logger.info("Phase 2: Nebula Graph Schema & Edge Ingestion")
        with self.target_engine.connect() as conn:
            status_filter = "" if include_unapproved else "WHERE a.status = 'approved'"
            links = conn.execute(text("""
                SELECT l.id, l.description, s.name AS source_name, t.name AS target_name, 
                       l.graph_edge_name, l.extraction_sql, l.ngql_schema,
                       COALESCE(a.status, 'legacy') AS artifact_status
                FROM aletheia_business_links l
                JOIN aletheia_business_objects s ON l.source_object_id = s.id
                JOIN aletheia_business_objects t ON l.target_object_id = t.id
                LEFT JOIN aletheia_ontology_artifacts a ON l.artifact_id = a.id
                """ + status_filter + f"""
                {"AND" if status_filter else "WHERE"} COALESCE(l.project_id, 'default') = :tenant_id
                """), {"tenant_id": self.tenant_id}).mappings().all()
        if not links and not include_unapproved:
            logger.warning("No approved business link artifacts found. Use --include-unapproved for legacy/demo ingestion.")
            
        for link in links:
            if link['graph_edge_name'] and link['extraction_sql'] and link['ngql_schema']:
                logger.info(f"Reusing existing metadata mapping for Edge: {link['source_name']} -> {link['target_name']} ({link['graph_edge_name']})")
                extraction = EdgeExtractionQuery(
                    sql_query=link['extraction_sql'],
                    relationship_type=link['graph_edge_name'],
                    ngql_schema=link['ngql_schema']
                )
            else:
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
                    
                    with self.target_engine.begin() as save_conn:
                        save_conn.execute(
                            text("UPDATE aletheia_business_links SET graph_edge_name = :gen, extraction_sql = :es, ngql_schema = :ns WHERE id = :id"),
                            {"gen": extraction.relationship_type, "es": extraction.sql_query, "ns": extraction.ngql_schema, "id": link['id']}
                        )
                except Exception as e:
                    logger.error(f"❌ Failed to extract edge mapping for link ID {link['id']}: {e}")
                    continue

            try:
                logger.info(f"Creating EDGE Schema: {extraction.relationship_type}")
                try:
                    self.graph_client.execute_query(extraction.ngql_schema)
                    time.sleep(5)
                except Exception as e:
                    logger.warning(f"Edge Schema issue or already exists: {e}")
                    time.sleep(2)
                
                with self.source_engine.connect() as source_conn:
                    logger.debug(f"Executing SQL Edge Extraction for {extraction.relationship_type}: {extraction.sql_query[:100]}...")
                    rows_result = source_conn.execute(text(extraction.sql_query)).mappings().all()
                    rows = [dict(r) for r in rows_result]
                
                logger.info(f"Upserting {len(rows)} edges for '{extraction.relationship_type}' into Nebula...")
                self.graph_client.insert_edges(extraction.relationship_type, rows)
                        
                logger.info(f"✅ Successfully processed edge batch for {extraction.relationship_type}")
                
            except Exception as e:
                logger.error(f"❌ Failed to process link ID {link['id']} ({link['source_name']} -> {link['target_name']}): {e}")

    def run(self, phase: str = "all", include_unapproved: bool = False):
        logger.info("Gathering ontology context from PostGIS...")
        self.ensure_metadata_columns()
        ontology_context = self.fetch_ontology_context()
        client = instructor.from_litellm(completion)
        
        if phase in ["1", "all"]:
            self.run_phase_1(client, ontology_context, include_unapproved)
            
        if phase in ["2", "all"]:
            self.run_phase_2(client, ontology_context, include_unapproved)

        logger.info("Nebula ingestion complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--tenants-file", help="JSON file defining tenant graph/database routing")
    parser.add_argument("--source")
    parser.add_argument("--target")
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-pass", default="nebula")
    parser.add_argument("--graph-space")
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    parser.add_argument("--phase", default="all", choices=["1", "2", "all"], help="Which phase to run (1 for nodes, 2 for edges, all for both)")
    parser.add_argument("--include-unapproved", action="store_true", help="Legacy/demo mode: ingest draft or unreviewed artifacts")
    args = parser.parse_args()
    tenant = TenantRegistry.load(args.tenants_file).get(args.tenant)
    
    agent = GraphIngestionAgent(
        source_db_url=args.source or tenant.source_db_url,
        target_db_url=args.target or tenant.metadata_db_url,
        nebula_ip=args.nebula_ip, nebula_port=args.nebula_port, nebula_user=args.nebula_user, nebula_pass=args.nebula_pass,
        graph_space=args.graph_space or tenant.graph_database,
        model_name=args.model,
        tenant_id=tenant.tenant_id,
    )
    agent.run(phase=args.phase, include_unapproved=args.include_unapproved)
