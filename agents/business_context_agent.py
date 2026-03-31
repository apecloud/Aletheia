import argparse
import logging
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("BusinessContextAgent")

class TableAlignment(BaseModel):
    table_name: str = Field(description="The technical name of the table")
    business_term: str = Field(description="The aligned business terminology for this table")
    business_description: str = Field(description="A rich description based on the external documentation")

class OntologyAlignment(BaseModel):
    alignments: List[TableAlignment] = Field(description="List of table alignments with business context")

class BusinessContextAgent:
    def __init__(self, target_db_url: str, model_name: str, docs_dir: str):
        self.target_engine = create_engine(target_db_url)
        self.docs_dir = docs_dir
        
        # 强制优先使用 gemini-3.1-pro-preview
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Business Context Agent with model: {self.model_name}")

    def read_documentation(self) -> str:
        """Read all available documentation from the docs directory."""
        if not os.path.exists(self.docs_dir):
            logger.warning(f"Documentation directory '{self.docs_dir}' not found. Using empty context.")
            return ""
            
        doc_content = ""
        for filename in os.listdir(self.docs_dir):
            filepath = os.path.join(self.docs_dir, filename)
            if os.path.isfile(filepath):
                try:
                    if filepath.endswith('.txt') or filepath.endswith('.md') or filepath.endswith('.json'):
                        with open(filepath, 'r', encoding='utf-8') as f:
                            doc_content += f"\n--- {filename} ---\n"
                            doc_content += f.read() + "\n"
                    else:
                        logger.info(f"Skipping binary/unsupported file: {filename} (PDF ingestion requires PyPDF2, skipped for plain text fallback)")
                except Exception as e:
                    logger.error(f"Failed to read {filename}: {e}")
        
        return doc_content[:15000] # Limit context size to avoid blowing up tokens

    def run(self):
        logger.info("Reading external business documentation...")
        business_context = self.read_documentation()
        
        if not business_context.strip():
            logger.warning("No business documentation found. The alignment might be purely inferential.")
            business_context = "No external business documentation provided. Infer business terms based on standard enterprise knowledge."

        try:
            with self.target_engine.connect() as conn:
                tables = conn.execute(text("SELECT id, table_name, table_comment FROM aletheia_extracted_tables")).fetchall()
        except Exception as e:
            logger.error(f"Failed to fetch tables from PostGIS: {e}")
            return
            
        if not tables:
            logger.warning("No tables found in ontology. Run Metadata Scraper first.")
            return

        schema_context = "=== Current Database Tables ===\n"
        for t in tables:
            schema_context += f"Table: {t[1]} (Current Comment: {t[2]})\n"

        prompt = f"""
You are an Enterprise Data Steward. Your task is to align technical database tables with business terminology using external documentation.

{schema_context}

=== External Business Documentation ===
{business_context}
=======================================

Based on the external documentation (or standard enterprise knowledge if missing), provide a clear, business-friendly term and a rich business description for each technical table.
"""
        
        client = instructor.from_litellm(completion)
        logger.info("Analyzing business context and aligning terminology via LLM...")
        
        try:
            alignment_result = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                response_model=OntologyAlignment,
            )
            
            # Update the database with new aligned terms and descriptions
            with self.target_engine.begin() as conn:
                for alignment in alignment_result.alignments:
                    logger.info(f"Aligned '{alignment.table_name}' -> '{alignment.business_term}'")
                    # Update table comment
                    update_sql = text("""
                        UPDATE aletheia_extracted_tables 
                        SET table_comment = :desc 
                        WHERE table_name = :tname
                    """)
                    conn.execute(update_sql, {"desc": f"[{alignment.business_term}] {alignment.business_description}", "tname": alignment.table_name})
                    
            logger.info("=================================================")
            logger.info("✅ Successfully aligned technical tables with business terminology.")
            logger.info("=================================================")
            
        except Exception as e:
            logger.error(f"Failed to align business context: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"))
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    parser.add_argument("--docs-dir", default="./docs", help="Directory containing business documentation (PDFs, txt, md)")
    args = parser.parse_args()
    
    agent = BusinessContextAgent(target_db_url=args.target, model_name=args.model, docs_dir=args.docs_dir)
    agent.run()
