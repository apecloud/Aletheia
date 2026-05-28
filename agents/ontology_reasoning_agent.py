import os
import argparse
import logging
from sqlalchemy import create_engine, text
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from litellm import completion
import instructor

from graph_db_client import NebulaGraphClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OntologyReasoningAgent")

class DeepReasoningResult(BaseModel):
    business_objective: str = Field(description="The core business goal of this reasoning task.")
    semantic_computations: List[str] = Field(description="Computations performed on the retrieved subgraph data based on ontology semantics (e.g., LTV calculation).")
    insight_synthesis: str = Field(description="A profound, analytical conclusion derived from the *actual data retrieved* combined with the ontology.")
    strategic_recommendation: str = Field(description="A highly actionable business recommendation based on the uncovered latent truths.")


class GraphQueryCase(BaseModel):
    title: str = Field(description="A descriptive title for this reasoning case.")
    description: str = Field(description="The business objective and context of what we want to find out.")
    graph_queries: List[str] = Field(description="A list of 1 to 3 valid nGQL/Cypher queries to extract the necessary subgraph from Nebula Graph.")

class ReasoningCasePlan(BaseModel):
    cases: List[GraphQueryCase] = Field(description="Exactly 3 distinct reasoning cases utilizing the available graph schema.")

class OntologyReasoningAgent:
    def __init__(self, target_db_url: str, nebula_ip: str, nebula_port: int, nebula_user: str, nebula_pass: str, graph_space: str, model_name: str):
        self.target_engine = create_engine(target_db_url)
        
        self.graph_client = NebulaGraphClient(
            ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_pass, space=graph_space
        )
        
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Deep Ontology Reasoning Agent with model: {self.model_name}")

    def fetch_graph_ontology_schema(self) -> str:
        """Fetch the physical schema and data profiler insights to form the reasoning meta-context."""
        context = "=== Stored Schema & Profiler Semantic Meta-Graph ===\n\n"
        try:
            with self.target_engine.connect() as conn:
                tables = conn.execute(text("SELECT id, table_name FROM aletheia_extracted_tables")).fetchall()
                for t in tables:
                    context += f"Entity Node [TAG]: {t[1]}\n"
                    
                    query = """
                    SELECT c.column_name, c.data_type, p.semantic_type, p.semantic_hypothesis
                    FROM aletheia_extracted_columns c
                    LEFT JOIN aletheia_column_profiles p ON c.id = p.column_id
                    WHERE c.table_id = :tid
                    """
                    cols = conn.execute(text(query), {"tid": t[0]}).fetchall()
                    for c in cols:
                        semantic = f" -> Semantic Type: {c[2]} ({c[3]})" if c[2] else ""
                        context += f"  - {c[0]} ({c[1]}){semantic}\n"
                    context += "\n"
                        
        except Exception as e:
            logger.error(f"Failed to fetch schema context: {e}")
        return context

    def execute_and_format_query(self, query: str) -> str:
        """Execute a query against Nebula Graph and format the results as text context."""
        try:
            result_obj = self.graph_client.execute_query(query)
            if result_obj.is_empty():
                return "[No data returned]"
                
            cols = result_obj.keys()
            rows = result_obj.rows()
            
            output = f"Executed Query: {query}\n"
            output += " | ".join(cols) + "\n"
            output += "-" * 50 + "\n"
            
            for row in rows:
                row_vals = []
                for val in row.values:
                    try:
                        # check if the value type is empty/null first based on Nebula Python client
                        if val.is_null() or val.is_empty() or val.is_bad_type():
                            row_vals.append("NULL")
                            continue
                    except AttributeError:
                        pass
                        
                    if hasattr(val, 'get_sVal'):
                        try:
                            row_vals.append(val.get_sVal().decode('utf-8'))
                        except:
                            row_vals.append(str(val))
                    elif hasattr(val, 'get_fVal'):
                        row_vals.append(str(val.get_fVal()))
                    elif hasattr(val, 'get_iVal'):
                        row_vals.append(str(val.get_iVal()))
                    else:
                        row_vals.append(str(val))
                output += " | ".join(row_vals) + "\n"
            return output
        except Exception as e:
            logger.error(f"Failed to execute graph query '{query}': {e}")
            return f"Error executing query: {str(e)}"

    def get_real_entity_id(self, query: str) -> str:
        """Helper to fetch a single random real ID from the graph dynamically."""
        try:
            result_obj = self.graph_client.execute_query(query)
            if result_obj and not result_obj.is_empty() and result_obj.rows():
                row = result_obj.rows()[0]
                val = row.values[0]
                
                try:
                    if val.is_null() or val.is_empty() or val.is_bad_type():
                        return ""
                except AttributeError:
                    pass

                if hasattr(val, 'get_sVal'):
                    return val.get_sVal().decode('utf-8')
                elif hasattr(val, 'get_iVal'):
                    return str(val.get_iVal())
                else:
                    return str(val)
        except Exception as e:
            logger.error(f"Failed to fetch real entity ID using query '{query}': {e}")
        return ""

    def run(self):
        logger.info("Initializing Graph Connection to Nebula...")
        self.graph_client.connect()
        
        logger.info("Extracting Schema and Data Profiler Meta-Graph for Reasoning Context...")
        schema_context = self.fetch_graph_ontology_schema()
        
        client = instructor.from_litellm(completion)

        logger.info("Dynamically generating reasoning cases based on Ontology Metadata...")
        
        # Fetch available Tags (Nodes) and Edges (Relationships) directly from Metadata to form the dynamic prompt
        with self.target_engine.connect() as conn:
            objects = conn.execute(text("SELECT name, graph_label FROM aletheia_schema_object_candidates WHERE graph_label IS NOT NULL")).fetchall()
            links = conn.execute(text("SELECT graph_edge_name FROM aletheia_schema_link_candidates WHERE graph_edge_name IS NOT NULL")).fetchall()
        
        graph_entities = "Available Nodes (TAGS):\n" + "\n".join([f"- {o[1]} (Business Object: {o[0]})" for o in objects])
        graph_relations = "Available Edges (RELATIONSHIPS):\n" + "\n".join([f"- {l[0]}" for l in links])
        
        prompt_cases = f"""
You are an expert Graph Data Architect working with Nebula Graph database.
Based on the extracted Semantic Meta-Graph and available Graph Schema below, generate exactly 3 distinct, high-value business reasoning test cases.

=== SEMANTIC META-GRAPH ===
{schema_context}

=== GRAPH SCHEMA MAPPINGS ===
{graph_entities}
{graph_relations}

Requirements:
1. Each case should aim to solve a real business problem (e.g., risk detection, fraud, customer lifetime value, logistics, supply chain).
2. The `graph_queries` MUST be valid Nebula Graph nGQL/openCypher MATCH queries using the EXACT node TAGS and EDGE names listed above.
3. Instead of hardcoding specific IDs like 'ALFKI', use general MATCH aggregations, LIMITs, or graph traversals (e.g., MATCH (a)-[r]->(b) RETURN a, count(r) ORDER BY count(r) DESC LIMIT 5).
4. DO NOT make up tags or edges that are not explicitly provided in the mapping above.
5. All graph schema names MUST be enclosed in backticks in the query, e.g., (c:`Customer`)-[e:`PLACED_ORDER`]->(o:`Order`).
6. IMPORTANT SYNTAX: In Nebula Graph, use double equals `==` for equality comparison in the WHERE clause, NOT single `=`.
7. IMPORTANT SYNTAX: ALL node/edge properties were ingested as STRING types. So numbers and booleans MUST be compared as strings (e.g., `WHERE t.isFraud == "True"`, NOT `WHERE t.isFraud = 1`).
"""
        try:
            plan = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt_cases}],
                response_model=ReasoningCasePlan,
            )
            reasoning_cases = []
            for c in plan.cases:
                reasoning_cases.append({
                    "title": c.title,
                    "description": c.description,
                    "graph_queries": c.graph_queries
                })
            logger.info("✅ Successfully generated dynamic reasoning cases from Metadata.")
        except Exception as e:
            logger.error(f"Failed to dynamically generate cases: {e}")
            logger.warning("Falling back to empty cases or exiting...")
            return

        logger.info("Starting Ontology Reasoning on LIVE Graph Data...\n")

        for idx, case in enumerate(reasoning_cases, 1):
            logger.info(f"===========================================================")
            logger.info(f"{case['title']}")
            logger.info(f"===========================================================")
            
            # Step 1: Fetch real data from the Graph Database
            real_data_context = ""
            for query in case['graph_queries']:
                logger.info(f"🔍 Fetching Subgraph: {query}")
                query_result = self.execute_and_format_query(query)
                real_data_context += query_result + "\n\n"
                
            # Step 2: Perform Deep Reasoning on the combination of Ontology Meta-Schema + Real Subgraph Data
            prompt = f"""
You are an elite Business Data Scientist and Graph Architect operating on the Aletheia Ontology.

You are presented with two pieces of context:
1. The Semantic Meta-Graph (Schema + Profiler Semantics)
2. A LIVE Subgraph extracted directly from the graph database for a specific reasoning case.

=== SEMANTIC META-GRAPH ===
{schema_context}

=== LIVE SUBGRAPH DATA ===
{real_data_context}

=== REASONING TASK ===
{case['description']}

Perform deep mathematical, semantic, and logical deduction on the LIVE data provided above. 
Synthesize a profound business conclusion that reveals latent truths that a standard SQL engine could not easily deduce. 
Finally, provide a strategic recommendation.
"""
            try:
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=DeepReasoningResult,
                )
                
                logger.info(f"\n🎯 Objective: {response.business_objective}")
                
                logger.info("\n🧮 Data-Driven Computations:")
                for comp in response.semantic_computations:
                    logger.info(f"   - {comp}")
                    
                logger.info(f"\n🧠 Insight Synthesis:\n{response.insight_synthesis}")
                
                logger.info(f"\n🚀 Strategic Recommendation:\n{response.strategic_recommendation}\n")
                
                with open("run_reasoning_result.md", "a", encoding="utf-8") as f:
                    f.write(f"\n## {case['title']}\n")
                    f.write(f"**Objective:** {response.business_objective}\n\n")
                    f.write(f"### Data-Driven Computations\n")
                    for comp in response.semantic_computations:
                        f.write(f"* {comp}\n")
                    f.write(f"\n### Insight Synthesis\n{response.insight_synthesis}\n")
                    f.write(f"\n### Strategic Recommendation\n{response.strategic_recommendation}\n")
                
            except Exception as e:
                logger.error(f"❌ Reasoning execution failed for Case {idx}.")
                logger.error(f"Error Details: {str(e)}\n")
                
        self.graph_client.close()
        logger.info("=================================================")
        logger.info("Ontology Deep Reasoning completed on LIVE data.")
        logger.info("=================================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"))
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-pass", default="nebula")
    parser.add_argument("--graph-space", default=os.environ.get("ALETHEIA_GRAPH_SPACE", "aletheia"))
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = OntologyReasoningAgent(
        target_db_url=args.target, 
        nebula_ip=args.nebula_ip, 
        nebula_port=args.nebula_port, 
        nebula_user=args.nebula_user, 
        nebula_pass=args.nebula_pass, 
        graph_space=args.graph_space,
        model_name=args.model
    )
    agent.run()
