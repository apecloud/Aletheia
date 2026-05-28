import os
import json
import argparse
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field
from typing import List

from litellm import completion
import instructor
try:
    from ontology_artifacts import (
        Base,
        ColumnProfile,
        ExtractedColumn,
        ExtractedTable,
        BusinessObject,
        BusinessLink,
        ObjectTableMapping,
        ensure_artifact_schema,
    )
except ModuleNotFoundError:
    from agents.ontology_artifacts import (
        Base,
        ColumnProfile,
        ExtractedColumn,
        ExtractedTable,
        BusinessObject,
        BusinessLink,
        ObjectTableMapping,
        ensure_artifact_schema,
    )
try:
    from schema_graph_modeling_agent import SchemaGraphModelingAgent
except ModuleNotFoundError:
    from agents.schema_graph_modeling_agent import SchemaGraphModelingAgent

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ObjectModelerAgent")

# --- LLM Structured Output Models (Pydantic) ---
class BusinessObjectDraft(BaseModel):
    name: str = Field(description="High-level entity name (e.g., 'Customer', 'MovieReview', 'Order')")
    description: str = Field(description="Business definition of this object")
    mapped_table_names: List[str] = Field(description="List of physical table names that belong to this business object")

class OntologyDraft(BaseModel):
    business_objects: List[BusinessObjectDraft] = Field(description="List of identified cohesive Business Objects")

# --- Agent Class ---
class ObjectModelerAgent:
    unified_modeling_agent = SchemaGraphModelingAgent

    def __init__(self, metadata_db_url: str, model_name: str = "gpt-4o"):
        logger.info(f"Initializing Object Modeler Agent with LiteLLM (Model: {model_name})...")
        self.metadata_engine = create_engine(metadata_db_url)
        self.model_name = model_name
        
        # Ensure ontology and artifact tables exist in PostGIS.
        ensure_artifact_schema(self.metadata_engine)
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

        Keep stable classification/master-data tables as their own Business Objects when they are a
        durable business vocabulary used by another object. For example, a `categories` table with
        category id/name/description should become a `Category` Business Object, not only a field
        embedded inside `Product`. Reference/detail tables such as `order_details` can be collapsed
        into their transaction object when they do not represent a durable standalone vocabulary.
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

    def to_graph_model_draft(self, llm_ontology: OntologyDraft, metadata_dump: list) -> object:
        """Compatibility adapter for the unified schema graph modeling contract."""
        return self.unified_modeling_agent.draft_from_legacy_object_model(llm_ontology, metadata_dump)

    def run(self):
        logger.info("Starting Object Modeling workflow...")
        project_id = os.environ.get("ALETHEIA_TENANT", "default")
        session = self.Session()
        try:
            metadata_dump = self.fetch_semantic_metadata(session)
            if not metadata_dump:
                logger.warning("No metadata found. Run the Metadata Scraper first.")
                return

            llm_ontology = self.model_objects_with_llm(metadata_dump)
            if not llm_ontology:
                return
            graph_model_draft = self.to_graph_model_draft(llm_ontology, metadata_dump)
            logger.info(
                "Adapted ObjectModeler output to SchemaGraphModelingAgent draft contract: %s node types",
                len(graph_model_draft.node_types),
            )
            self.unified_modeling_agent.persist_draft_artifacts_in_session(
                session,
                graph_model_draft,
                project_id=project_id,
                source_agent=self.unified_modeling_agent.source_agent,
            )

            # Clear old mappings (Idempotency)
            object_ids = [
                row[0]
                for row in session.query(BusinessObject.id).filter_by(project_id=project_id).all()
            ]
            session.query(BusinessLink).filter_by(project_id=project_id).delete()
            if object_ids:
                session.query(ObjectTableMapping).filter(ObjectTableMapping.object_id.in_(object_ids)).delete(
                    synchronize_session=False
                )
            session.query(BusinessObject).filter_by(project_id=project_id).delete()
            session.commit()

            # Save objects and mappings
            for obj_draft in llm_ontology.business_objects:
                logger.info(f"Identified Business Object: {obj_draft.name}")
                
                new_obj = BusinessObject(project_id=project_id, name=obj_draft.name, description=obj_draft.description)
                session.add(new_obj)
                session.flush() # Get ID
                mapped_tables = []
                
                for table_name in obj_draft.mapped_table_names:
                    table = session.query(ExtractedTable).filter_by(table_name=table_name).first()
                    if table:
                        mapped_tables.append(table)
                        mapping = ObjectTableMapping(object_id=new_obj.id, table_id=table.id)
                        session.add(mapping)
                    else:
                        logger.warning(f"LLM mapped unknown table: {table_name}")

            session.commit()
            logger.info("Successfully saved Business Objects and unified draft ontology artifacts to PostGIS.")
        except Exception as e:
            session.rollback()
            logger.error(f"Workflow error: {e}")
        finally:
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"))
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    
    agent = ObjectModelerAgent(metadata_db_url=args.target, model_name=args.model)
    agent.run()
