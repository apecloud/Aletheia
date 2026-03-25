import os
import json
import argparse
import logging
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LinkWeaverAgent")

Base = declarative_base()

# --- Existing Database Models ---
class ExtractedTable(Base):
    __tablename__ = 'aletheia_extracted_tables'
    id = Column(Integer, primary_key=True, autoincrement=True)
    table_name = Column(String(255), nullable=False)

class BusinessObject(Base):
    __tablename__ = 'aletheia_business_objects'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text)

class ObjectTableMapping(Base):
    __tablename__ = 'aletheia_object_mappings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    object_id = Column(Integer, ForeignKey('aletheia_business_objects.id'))
    table_id = Column(Integer, ForeignKey('aletheia_extracted_tables.id'))

# --- New Database Models for Link Weaver ---
class BusinessLink(Base):
    __tablename__ = 'aletheia_business_links'
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_object_id = Column(Integer, ForeignKey('aletheia_business_objects.id'), nullable=False)
    target_object_id = Column(Integer, ForeignKey('aletheia_business_objects.id'), nullable=False)
    link_type = Column(String(50)) # 1:1, 1:N, N:M
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

# --- LLM Structured Output Models (Pydantic) ---
class LinkDraft(BaseModel):
    source_object_name: str = Field(description="Name of the source Business Object")
    target_object_name: str = Field(description="Name of the target Business Object")
    link_type: str = Field(description="Relationship cardinality (e.g., '1:1', '1:N', or 'N:M')")
    description: str = Field(description="Detailed explanation of how and why these objects relate based on metadata")

class LinksDraft(BaseModel):
    links: List[LinkDraft] = Field(description="List of identified explicit or implicit relationships between Business Objects")

# --- Agent Class ---
class LinkWeaverAgent:
    def __init__(self, metadata_db_url: str, model_name: str = "gpt-4o"):
        logger.info(f"Initializing Link Weaver Agent with LiteLLM (Model: {model_name})...")
        self.metadata_engine = create_engine(metadata_db_url)
        self.model_name = model_name
        
        # Ensure new link tables exist in PostGIS
        Base.metadata.create_all(self.metadata_engine)
        self.Session = sessionmaker(bind=self.metadata_engine)
        
        # Wrap LiteLLM with Instructor
        self.client = instructor.from_litellm(completion)

    def fetch_ontology_dump(self, session) -> list:
        logger.info("Fetching mapped Business Objects from PostGIS...")
        objects = session.query(BusinessObject).all()
        ontology_dump = []
        
        for obj in objects:
            tables = session.query(ExtractedTable).join(ObjectTableMapping).filter(ObjectTableMapping.object_id == obj.id).all()
            table_names = [t.table_name for t in tables]
            ontology_dump.append({
                "object_name": obj.name,
                "description": obj.description,
                "underlying_tables": table_names
            })
        return ontology_dump

    def weave_links_with_llm(self, ontology_dump: list) -> LinksDraft:
        logger.info(f"Calling LLM ({self.model_name}) to discover relationships between Objects...")
        
        prompt = f"""
        Here are the Business Objects modeled in our enterprise graph:
        
        {json.dumps(ontology_dump, indent=2)}
        
        Task:
        Discover explicit (foreign keys, naming conventions) and implicit semantic relationships between these Business Objects.
        If there are only independent objects, return an empty links list. But if they logically relate (e.g., Customer to Order, or Movie to Review), define the link.
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_model=LinksDraft,
                messages=[
                    {"role": "system", "content": "You are the 'Link Weaver Agent' (Architect) for the Aletheia project. You discover logical graphs connecting Enterprise Business Objects."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
            )
            return response
        except Exception as e:
            logger.error(f"LLM Link Discovery failed: {e}")
            return None

    def run(self):
        logger.info("Starting Link Weaving workflow...")
        session = self.Session()
        try:
            ontology_dump = self.fetch_ontology_dump(session)
            if len(ontology_dump) < 2:
                logger.warning("Need at least 2 Business Objects to weave links. Create more tables and run Object Modeler first.")
                return

            llm_links = self.weave_links_with_llm(ontology_dump)
            if not llm_links:
                return

            # Clear old links
            session.query(BusinessLink).delete()
            session.commit()

            # Save new links
            for link_draft in llm_links.links:
                logger.info(f"Weaved Link: {link_draft.source_object_name} --({link_draft.link_type})--> {link_draft.target_object_name}")
                
                source_obj = session.query(BusinessObject).filter_by(name=link_draft.source_object_name).first()
                target_obj = session.query(BusinessObject).filter_by(name=link_draft.target_object_name).first()
                
                if source_obj and target_obj:
                    new_link = BusinessLink(
                        source_object_id=source_obj.id,
                        target_object_id=target_obj.id,
                        link_type=link_draft.link_type,
                        description=link_draft.description
                    )
                    session.add(new_link)
                else:
                    logger.warning(f"LLM referenced unknown objects in link: {link_draft.source_object_name} -> {link_draft.target_object_name}")

            session.commit()
            logger.info("Successfully saved Business Links to PostGIS.")
        except Exception as e:
            session.rollback()
            logger.error(f"Workflow error: {e}")
        finally:
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    
    agent = LinkWeaverAgent(metadata_db_url=args.target, model_name=args.model)
    agent.run()
