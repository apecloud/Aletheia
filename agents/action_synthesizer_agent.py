import argparse
import logging
import os
from sqlalchemy import create_engine, inspect, Column, Integer, String, Text, Boolean, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List, Optional

from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ActionSynthesizerAgent")

Base = declarative_base()

class BusinessAction(Base):
    __tablename__ = 'aletheia_business_actions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    action_type = Column(String(50)) # 'procedure' or 'trigger'
    source_name = Column(String(255), nullable=False) # e.g. the sproc name
    description = Column(Text)
    is_safe = Column(Boolean, default=False)
    inputs_json = Column(Text)
    outputs_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

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
        Base.metadata.create_all(self.target_engine)
        logger.info("Ensured aletheia_business_actions table exists.")

    def run(self):
        self.setup_target_db()
        session = self.TargetSession()
        
        try:
            # Clear previous runs
            session.execute(text('TRUNCATE TABLE aletheia_business_actions CASCADE'))
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
                        name=analysis.action_name,
                        action_type=r_type.lower(),
                        source_name=r_name,
                        description=analysis.description,
                        is_safe=analysis.is_safe,
                        inputs_json=analysis.inputs,
                        outputs_json=analysis.outputs
                    )
                    session.add(action)
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
                        name=analysis.action_name,
                        action_type='trigger',
                        source_name=t_name,
                        description=analysis.description,
                        is_safe=analysis.is_safe,
                        inputs_json=analysis.inputs,
                        outputs_json=analysis.outputs
                    )
                    session.add(action)
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
    parser.add_argument("--source", default="mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data")
    parser.add_argument("--target", default="postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology")
    parser.add_argument("--model", default="gemini/gemini-3.1-pro-preview", help="Model name for litellm")
    args = parser.parse_args()
    
    agent = ActionSynthesizerAgent(source_db_url=args.source, target_db_url=args.target, model_name=args.model)
    agent.run()
