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
logger = logging.getLogger("ObjectModelerAgent")

Base = declarative_base()

# --- Existing Database Models ---
class ExtractedTable(Base):
    __tablename__ = 'aletheia_extracted_tables'
    id = Column(Integer, primary_key=True, autoincrement=True)
    schema_name = Column(String(255))
    table_name = Column(String(255), nullable=False)
    table_comment = Column(String(1000))
    columns = relationship("ExtractedColumn", back_populates="table")

class ExtractedColumn(Base):
    __tablename__ = 'aletheia_extracted_columns'
    id = Column(Integer, primary_key=True, autoincrement=True)
    table_id = Column(Integer, ForeignKey('aletheia_extracted_tables.id'))
    column_name = Column(String(255), nullable=False)
    data_type = Column(String(255), nullable=False)
    table = relationship("ExtractedTable", back_populates="columns")
    profile = relationship("ColumnProfile", back_populates="column", uselist=False)

class ColumnProfile(Base):
    __tablename__ = 'aletheia_column_profiles'
    id = Column(Integer, primary_key=True, autoincrement=True)
    column_id = Column(Integer, ForeignKey('aletheia_extracted_columns.id'))
    semantic_type = Column(String(255))
    semantic_hypothesis = Column(Text)
    column = relationship("ExtractedColumn", back_populates="profile")

# --- New Database Models for Object Modeler ---
class BusinessObject(Base):
    __tablename__ = 'aletheia_business_objects'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class ObjectTableMapping(Base):
    __tablename__ = 'aletheia_object_mappings'
    id = Column(Integer, primary_key=True, autoincrement=True)
    object_id = Column(Integer, ForeignKey('aletheia_business_objects.id'))
    table_id = Column(Integer, ForeignKey('aletheia_extracted_tables.id'))

# --- LLM Structured Output Models (Pydantic) ---
class BusinessObjectDraft(BaseModel):
    name: str = Field(description="High-level entity name (e.g., 'Customer', 'MovieReview', 'Order')")
    description: str = Field(description="Business definition of this object")
    mapped_table_names: List[str] = Field(description="List of physical table names that belong to this business object")

class OntologyDraft(BaseModel):
    business_objects: List[BusinessObjectDraft] = Field(description="List of identified cohesive Business Objects")

# --- Agent Class ---
class ObjectModelerAgent:
    def __init__(self, metadata_db_url: str, model_name: str = "gpt-4o"):
        logger.info(f"Initializing Object Modeler Agent with LiteLLM (Model: {model_name})...")
        self.metadata_engine = create_engine(metadata_db_url)
        self.model_name = model_name
        
        # Ensure new ontology tables exist in PostGIS
        Base.metadata.create_all(self.metadata_engine)
        self.Session = sessionmaker(bind=self.metadata_engine)
        
        # Wrap LiteLLM with Instructor
        self.client = instructor.from_litellm(completion)

    def fetch_semantic_metadata(self, session) -> list:
        logger.info("Fetching physical tables and semantic profiles from PostGIS...")
        tables = session.query(ExtractedTable).all()
        metadata_dump = []
        
        for t in tables:
            cols = []
            for c in t.columns:
                cols.append({
                    "column": c.column_name,
                    "type": c.data_type,
                    "semantic_type": c.profile.semantic_type if c.profile else "Unknown",
                    "hypothesis": c.profile.semantic_hypothesis if c.profile else "Unknown"
                })
            metadata_dump.append({
                "table_name": t.table_name,
                "table_comment": t.table_comment,
                "columns": cols
            })
        return metadata_dump

    def model_objects_with_llm(self, metadata_dump: list) -> OntologyDraft:
        logger.info(f"Calling LLM ({self.model_name}) to group tables into Business Objects...")
        
        prompt = f"""
        Here is the metadata and semantic profiles of tables from a legacy database:
        
        {json.dumps(metadata_dump, indent=2)}
        
        Task:
        Collapse these normalized physical tables into cohesive, high-level Business Objects.
        For example, 'orders' and 'order_items' might both map to a single 'Order' Business Object.
        If a table represents a standalone concept (like 'imdb_reviews'), map it to a 'MovieReview' object.
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_model=OntologyDraft,
                messages=[
                    {"role": "system", "content": "You are the 'Object Modeler Agent' (Architect) for the Aletheia project. You design high-level Business Ontologies from legacy metadata."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
            )
            return response
        except Exception as e:
            logger.error(f"LLM Modeling failed: {e}")
            return None

    def run(self):
        logger.info("Starting Object Modeling workflow...")
        session = self.Session()
        try:
            metadata_dump = self.fetch_semantic_metadata(session)
            if not metadata_dump:
                logger.warning("No metadata found. Run the Metadata Scraper first.")
                return

            llm_ontology = self.model_objects_with_llm(metadata_dump)
            if not llm_ontology:
                return

            # Clear old mappings (Idempotency)
            session.query(ObjectTableMapping).delete()
            session.query(BusinessObject).delete()
            session.commit()

            # Save objects and mappings
            for obj_draft in llm_ontology.business_objects:
                logger.info(f"Identified Business Object: {obj_draft.name}")
                
                new_obj = BusinessObject(name=obj_draft.name, description=obj_draft.description)
                session.add(new_obj)
                session.flush() # Get ID
                
                for table_name in obj_draft.mapped_table_names:
                    table = session.query(ExtractedTable).filter_by(table_name=table_name).first()
                    if table:
                        mapping = ObjectTableMapping(object_id=new_obj.id, table_id=table.id)
                        session.add(mapping)
                    else:
                        logger.warning(f"LLM mapped unknown table: {table_name}")

            session.commit()
            logger.info("Successfully saved Business Objects and Mappings to PostGIS.")
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
    
    agent = ObjectModelerAgent(metadata_db_url=args.target, model_name=args.model)
    agent.run()
