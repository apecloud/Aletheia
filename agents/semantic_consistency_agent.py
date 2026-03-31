import os
import argparse
import logging
from sqlalchemy import create_engine, text
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("SemanticConsistencyAgent")

class ConsistencyIssue(BaseModel):
    issue_type: str = Field(description="Type of issue: e.g., 'Orphaned Object', 'Invalid Link', 'Unsafe Action', 'Contradiction'")
    description: str = Field(description="Detailed explanation of the semantic issue")
    severity: str = Field(description="High, Medium, or Low")

class ConsistencyReport(BaseModel):
    is_consistent: bool = Field(description="True if the ontology is logically sound and usable, False otherwise")
    issues: List[ConsistencyIssue] = Field(description="List of detected semantic issues")
    suggested_fixes: List[str] = Field(description="Actionable suggestions to fix the issues")

class SemanticConsistencyAgent:
    def __init__(self, target_db_url: str, model_name: str):
        self.target_engine = create_engine(target_db_url)
        
        # 强制优先使用 gemini-3.1-pro-preview
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Semantic Consistency Agent with model: {self.model_name}")

    def run(self):
        logger.info("Fetching ontology data from PostGIS...")
        try:
            with self.target_engine.connect() as conn:
                objects = conn.execute(text("SELECT id, name, description FROM aletheia_business_objects")).fetchall()
                links = conn.execute(text("SELECT source_object_id, target_object_id, link_type, description FROM aletheia_business_links")).fetchall()
                actions = conn.execute(text("SELECT name, action_type, is_safe, description FROM aletheia_business_actions")).fetchall()
        except Exception as e:
            logger.error(f"Failed to fetch data: {e}")
            return
            
        if not objects:
            logger.warning("No business objects found to analyze. Run Object Modeler first.")
            return

        # 整理上下文喂给大模型
        context = "=== Business Objects ===\n"
        for obj in objects:
            context += f"ID: {obj[0]}, Name: {obj[1]}, Desc: {obj[2]}\n"
            
        context += "\n=== Business Links ===\n"
        for link in links:
            context += f"Source ID: {link[0]} -> Target ID: {link[1]}, Type: {link[2]}, Desc: {link[3]}\n"
            
        context += "\n=== Business Actions ===\n"
        for action in actions:
            context += f"Action: {action[0]}, Type: {action[1]}, Safe: {action[2]}, Desc: {action[3]}\n"

        prompt = f"""
You are an expert Data Ontology Architect. Analyze the following generated business ontology (Objects, Links, and Actions) for semantic consistency, logical contradictions, or missing relationships.

{context}

Please evaluate if:
1. Objects have valid, meaningful relationships.
2. Actions conceptually align with the objects.
3. There are no circular dependencies, orphaned critical entities, or contradictory definitions.

Provide a structured report of any issues found and actionable fixes.
"""
        
        client = instructor.from_litellm(completion)
        logger.info("Analyzing semantic consistency via LLM...")
        try:
            report = client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                response_model=ConsistencyReport,
            )
            
            logger.info("=================================================")
            logger.info(f"Ontology Consistent: {'✅ YES' if report.is_consistent else '❌ NO'}")
            
            if report.issues:
                logger.warning(f"Found {len(report.issues)} Issues:")
                for idx, issue in enumerate(report.issues):
                    logger.warning(f"  [{issue.severity}] {issue.issue_type}: {issue.description}")
            else:
                logger.info("No semantic issues detected! Everything looks perfectly aligned.")
                
            if report.suggested_fixes:
                logger.info("Suggested Fixes:")
                for fix in report.suggested_fixes:
                    logger.info(f"  💡 {fix}")
            logger.info("=================================================")
            
        except Exception as e:
            logger.error(f"Failed to run consistency check: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"))
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = SemanticConsistencyAgent(target_db_url=args.target, model_name=args.model)
    agent.run()
