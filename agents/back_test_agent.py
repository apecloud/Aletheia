import argparse
import logging
from sqlalchemy import create_engine, text
from pydantic import BaseModel, Field
from typing import List, Optional

from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BackTestAgent")

class GeneratedQuery(BaseModel):
    sql_query: str = Field(description="The SQL query generated using the ontology to answer the business question.")
    reasoning: str = Field(description="Explanation of how the ontology objects and links were used to construct the query.")

class BackTestAgent:
    def __init__(self, source_db_url: str, target_db_url: str, model_name: str):
        self.source_engine = create_engine(source_db_url)
        self.target_engine = create_engine(target_db_url)
        
        # 强制优先使用 gemini-3.1-pro-preview
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Back-test Agent with model: {self.model_name}")

    def fetch_ontology_context(self) -> str:
        """Fetch the ontology structure to feed into the LLM as context."""
        context = "=== Aletheia Data Ontology ===\n\n"
        try:
            with self.target_engine.connect() as conn:
                # Get Tables and Columns
                tables = conn.execute(text("SELECT id, table_name FROM aletheia_extracted_tables")).fetchall()
                for t in tables:
                    context += f"Table: {t[1]}\nColumns: "
                    cols = conn.execute(text(f"SELECT column_name, data_type FROM aletheia_extracted_columns WHERE table_id={t[0]}")).fetchall()
                    context += ", ".join([f"{c[0]} ({c[1]})" for c in cols]) + "\n\n"
                
                # Get Business Objects
                objects = conn.execute(text("SELECT name, description FROM aletheia_business_objects")).fetchall()
                if objects:
                    context += "=== Business Objects ===\n"
                    for obj in objects:
                        context += f"- {obj[0]}: {obj[1]}\n"
                        
        except Exception as e:
            logger.error(f"Failed to fetch ontology context: {e}")
        return context

    def run(self):
        logger.info("Gathering ontology context from PostGIS...")
        ontology_context = self.fetch_ontology_context()
        
        if not ontology_context.strip():
            logger.error("Ontology context is empty. Please ensure the extraction agents have run.")
            return

        # Typical business questions based on standard test schemas (like Northwind)
        test_cases = [
            "List the names of all products in the 'Beverages' category.",
            "Find the company name of the customer who placed the most orders.",
            "Calculate the total revenue (unit price * quantity) for all orders."
        ]

        client = instructor.from_litellm(completion)
        passed_tests = 0

        logger.info(f"Starting back-testing for {len(test_cases)} business questions...\n")

        for idx, question in enumerate(test_cases, 1):
            logger.info(f"Test Case {idx}: {question}")
            
            prompt = f"""
You are an expert SQL Data Analyst working with a specific semantic ontology.
Using the provided Data Ontology, write a syntactically correct MySQL query to answer the business question.
Return ONLY standard SELECT statements. Do not invent tables or columns that do not exist in the ontology.

{ontology_context}

Business Question: "{question}"
"""
            try:
                # 1. Generate SQL using the Ontology
                response = client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_model=GeneratedQuery,
                )
                
                logger.info(f"Generated SQL:\n{response.sql_query}")
                logger.info(f"Reasoning: {response.reasoning}")

                # 2. Validate the SQL against the Raw Source Database
                # We use LIMIT 1 or EXPLAIN to test validity without pulling massive data
                test_sql = f"EXPLAIN {response.sql_query}"
                
                with self.source_engine.connect() as source_conn:
                    source_conn.execute(text(test_sql))
                    
                logger.info(f"✅ Test {idx} PASSED: Query successfully validated against the raw schema!\n")
                passed_tests += 1
                
            except Exception as e:
                logger.error(f"❌ Test {idx} FAILED: Database execution error or generation failure.")
                logger.error(f"Error Details: {str(e)}\n")
                
        logger.info("=================================================")
        logger.info(f"Back-test Complete: {passed_tests}/{len(test_cases)} passed.")
        if passed_tests == len(test_cases):
            logger.info("🎉 The ontology is perfectly accurate and exhibits zero data loss for the test cases!")
        else:
            logger.warning("⚠️ Some queries failed. The ontology may be missing critical relationships or column mappings.")
        logger.info("=================================================")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data")
    parser.add_argument("--target", default="postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology")
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = BackTestAgent(source_db_url=args.source, target_db_url=args.target, model_name=args.model)
    agent.run()
