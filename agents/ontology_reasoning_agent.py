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

class OntologyReasoningAgent:
    def __init__(self, target_db_url: str, nebula_ip: str, nebula_port: int, nebula_user: str, nebula_pass: str, model_name: str):
        self.target_engine = create_engine(target_db_url)
        
        self.graph_client = NebulaGraphClient(
            ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_pass
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
        
        # --- Dynamically fetch REAL IDs from the Graph ---
        logger.info("Fetching real entity IDs from the graph to build accurate reasoning cases...")
        
        # We run a quick MATCH to get a real Customer who has actually PLACED orders
        real_customer_id = self.get_real_entity_id("MATCH (c:`Customer`)-[:`PLACED_ORDER`]->(o:`Order`) RETURN id(c) LIMIT 1")
        
        # We run a quick MATCH to get a real Order that actually CONTAINS order_details
        real_order_id = self.get_real_entity_id("MATCH (o:`Order`)-[:`CONSISTS_OF`]->(p:`Product`) RETURN id(o) LIMIT 1")

        if not real_customer_id:
            logger.warning("Could not dynamically fetch real Customer ID from the graph. Using fallback 'ALFKI'.")
            real_customer_id = 'ALFKI'
            
        if not real_order_id:
            logger.warning("Could not dynamically fetch real Order ID from the graph. Using fallback '10248'.")
            real_order_id = '10248'

        logger.info(f"Targeting Real Order ID for reasoning: {real_order_id}")
        logger.info(f"Targeting Real Customer ID for reasoning: {real_customer_id}")

        reasoning_cases = [
            {
                "title": f"Case 1: True Order Margin & Fulfillment Complexity (Order-centric - ID: {real_order_id})",
                "description": f"We are analyzing a specific REAL Order '{real_order_id}'. We need to calculate its true margin, understand the customer profile, and assess the logistical complexity based on the categories of products ordered.",
                "graph_queries": [
                    f"MATCH (c:`Customer`)-[:`PLACED_ORDER`]->(o:`Order`)<-[:`PROCESSED_ORDER`]-(e:`Employee`) WHERE id(o) == '{real_order_id}' RETURN id(c) AS customer_id, id(e) AS employee_id",
                    f"MATCH (o:`Order`)-[e:`CONSISTS_OF`]->(p:`Product`) WHERE id(o) == '{real_order_id}' RETURN id(p) AS product_id, e.unitprice AS unit_price, e.quantity AS qty, e.discount AS discount, p.`Product`.categoryid AS category_id"
                ]
            },
            {
                "title": f"Case 2: Customer Buyer Persona & Network Value (Customer-centric - ID: {real_customer_id})",
                "description": f"We are evaluating a specific REAL Customer '{real_customer_id}'. We want to classify their 'Buyer Persona' (e.g., Premium vs Discount) and calculate their Network Lifetime Value (LTV) by looking at their entire order history and product preferences.",
                "graph_queries": [
                    f"MATCH (c:`Customer`)-[:`PLACED_ORDER`]->(o:`Order`)-[e:`CONSISTS_OF`]->(p:`Product`) WHERE id(c) == '{real_customer_id}' RETURN id(o) AS order_id, id(p) AS product_id, e.unitprice AS unit_price, e.quantity AS qty, e.discount AS discount"
                ]
            },
            {
                "title": "Case 3: Supply Chain Risk Identification (Product-centric)",
                "description": "We need to identify potential supply chain vulnerabilities. A product is 'High Risk' if it has been ordered in massive quantities across many distinct orders.",
                "graph_queries": [
                    "MATCH (o:`Order`)-[e:`CONSISTS_OF`]->(p:`Product`) RETURN id(p) AS product_id, sum(e.quantity) AS total_qty, count(o) AS order_count ORDER BY total_qty DESC LIMIT 5"
                ]
            }
        ]

        client = instructor.from_litellm(completion)

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
    parser.add_argument("--target", default="postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology")
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-pass", default="nebula")
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = OntologyReasoningAgent(
        target_db_url=args.target, 
        nebula_ip=args.nebula_ip, 
        nebula_port=args.nebula_port, 
        nebula_user=args.nebula_user, 
        nebula_pass=args.nebula_pass, 
        model_name=args.model
    )
    agent.run()
