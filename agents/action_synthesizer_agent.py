import argparse
import logging
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field
from typing import List, Optional

from litellm import completion
import instructor
from ontology_artifacts import BusinessAction, delete_artifacts_by_type, ensure_artifact_schema, sync_action_artifact

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ActionSynthesizerAgent")

class ActionAnalysis(BaseModel):
    action_name: str = Field(description="A clean, business-friendly name for this action")
    description: str = Field(description="A description of what business process this action performs")
    is_safe: bool = Field(description="True if it only reads data or performs safe idempotent operations, False if it performs uncontrolled writes or dangerous side effects")
    inputs: str = Field(description="A JSON representation of the expected inputs")
    outputs: str = Field(description="A JSON representation of the outputs")

class ActionSynthesizerAgent:
    def __init__(self, source_db_url: str, target_db_url: str, model_name: str):
        self.source_engine = create_engine(source_db_url)
        self.target_engine = create_engine(target_db_url)
        self.TargetSession = sessionmaker(bind=self.target_engine)
        
        # Override with gemini-3.1-pro-preview if litellm doesn't resolve automatically
        if "gemini" in model_name.lower():
            self.model_name = "gemini/gemini-3.1-pro-preview"
        else:
            self.model_name = model_name
            
        logger.info(f"Initialized Action Synthesizer with model: {self.model_name}")

    def setup_target_db(self):
        ensure_artifact_schema(self.target_engine)
        logger.info("Ensured action and ontology artifact tables exist.")

    def run(self):
        self.setup_target_db()
        project_id = os.environ.get("ALETHEIA_TENANT", "default")
        session = self.TargetSession()
        
        try:
            # Clear previous runs
            delete_artifacts_by_type(session, ["action"], project_id=project_id)
            session.execute(text("DELETE FROM aletheia_business_actions WHERE project_id = :project_id"), {"project_id": project_id})
            session.commit()
            logger.info("Cleared old business actions.")
            
            # Fetch routines
            with self.source_engine.connect() as source_conn:
                routines = source_conn.execute(text("SELECT ROUTINE_NAME, ROUTINE_TYPE, ROUTINE_DEFINITION FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = DATABASE()")).fetchall()
                triggers = source_conn.execute(text("SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE, ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()")).fetchall()
            
            logger.info(f"Found {len(routines)} routines and {len(triggers)} triggers.")
            
            client = instructor.from_litellm(completion)
            
            # Analyze Routines
            for r_name, r_type, r_def in routines:
                logger.info(f"Synthesizing Routine: {r_name}")
                prompt = f"Analyze this database {r_type} named '{r_name}':\n\n```sql\n{r_def}\n```\nMap it to a safe, executable business action."
                
                try:
                    analysis = client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        response_model=ActionAnalysis,
                    )
                    
                    action = BusinessAction(
                        project_id=project_id,
                        name=analysis.action_name,
                        action_type=r_type.lower(),
                        source_name=r_name,
                        description=analysis.description,
                        is_safe=analysis.is_safe,
                        inputs_json=analysis.inputs,
                        outputs_json=analysis.outputs
                    )
                    session.add(action)
                    session.flush()
                    sync_action_artifact(session, action)
                except Exception as e:
                    logger.error(f"Failed to analyze {r_name}: {e}")

            # Analyze Triggers
            for t_name, event, table, t_def in triggers:
                logger.info(f"Synthesizing Trigger: {t_name}")
                prompt = f"Analyze this database TRIGGER '{t_name}' on table '{table}' (Event: {event}):\n\n```sql\n{t_def}\n```\nMap it to a business action if possible, explaining its side effects."
                
                try:
                    analysis = client.chat.completions.create(
                        model=self.model_name,
                        messages=[{"role": "user", "content": prompt}],
                        response_model=ActionAnalysis,
                    )
                    
                    action = BusinessAction(
                        project_id=project_id,
                        name=analysis.action_name,
                        action_type='trigger',
                        source_name=t_name,
                        description=analysis.description,
                        is_safe=analysis.is_safe,
                        inputs_json=analysis.inputs,
                        outputs_json=analysis.outputs
                    )
                    session.add(action)
                    session.flush()
                    sync_action_artifact(session, action)
                except Exception as e:
                    logger.error(f"Failed to analyze trigger {t_name}: {e}")

            session.commit()
            logger.info("Successfully synthesized all actions.")
        except Exception as e:
            session.rollback()
            logger.error(f"Error during synthesis: {e}")
        finally:
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}"))
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"))
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = ActionSynthesizerAgent(source_db_url=args.source, target_db_url=args.target, model_name=args.model)
    agent.run()
