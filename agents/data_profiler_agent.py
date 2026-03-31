import os
import json
import argparse
import logging
import pandas as pd
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
from pydantic import BaseModel, Field
from typing import List

# Industry Standard LLM Abstractions for Multi-Model Support
from litellm import completion
import instructor

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("DataProfilerAgent")

Base = declarative_base()

# --- Database Models ---
class ExtractedTable(Base):
    __tablename__ = 'aletheia_extracted_tables'
    id = Column(Integer, primary_key=True, autoincrement=True)
    schema_name = Column(String(255))
    table_name = Column(String(255), nullable=False)
    table_comment = Column(String(1000))
    extracted_at = Column(DateTime, default=datetime.utcnow)
    columns = relationship("ExtractedColumn", back_populates="table")

class ExtractedColumn(Base):
    __tablename__ = 'aletheia_extracted_columns'
    id = Column(Integer, primary_key=True, autoincrement=True)
    table_id = Column(Integer, ForeignKey('aletheia_extracted_tables.id'), nullable=False)
    column_name = Column(String(255), nullable=False)
    data_type = Column(String(255), nullable=False)
    
    table = relationship("ExtractedTable", back_populates="columns")
    profile = relationship("ColumnProfile", back_populates="column", uselist=False)

class ColumnProfile(Base):
    __tablename__ = 'aletheia_column_profiles'
    id = Column(Integer, primary_key=True, autoincrement=True)
    column_id = Column(Integer, ForeignKey('aletheia_extracted_columns.id'), nullable=False)
    semantic_type = Column(String(255))
    semantic_hypothesis = Column(Text)
    profiled_at = Column(DateTime, default=datetime.utcnow)
    
    column = relationship("ExtractedColumn", back_populates="profile")

# --- LLM Structured Output Models (Pydantic) ---
class ColumnSemantic(BaseModel):
    column_name: str = Field(description="Exact column name from the database table")
    semantic_type: str = Field(description="e.g., SentimentLabel, TextReview, UserID, CategoricalFeature, etc.")
    hypothesis: str = Field(description="Detailed explanation of what this column means in a business context")

class TableSemantic(BaseModel):
    table_hypothesis: str = Field(description="A short description of what business entity this table represents")
    columns: List[ColumnSemantic] = Field(description="List of semantic analysis for each column in the table")

# --- Agent Class ---
class DataProfilerAgent:
    def __init__(self, source_db_url: str, metadata_db_url: str, model_name: str = "gpt-4o"):
        logger.info(f"Initializing Data Profiler Agent with LiteLLM (Model: {model_name})...")
        self.source_engine = create_engine(source_db_url)
        self.metadata_engine = create_engine(metadata_db_url)
        self.model_name = model_name
        
        # Ensure the new profiling table exists in PostGIS
        Base.metadata.create_all(self.metadata_engine)
        self.Session = sessionmaker(bind=self.metadata_engine)
        
        # Wrap LiteLLM with Instructor to force Pydantic structured outputs universally
        self.client = instructor.from_litellm(completion)

    def profile_table(self, table_name: str, session):
        logger.info(f"Fetching data samples for table: {table_name}")
        query = f"SELECT * FROM {table_name} LIMIT 50"
        try:
            df = pd.read_sql(query, self.source_engine)
            if df.empty:
                logger.warning(f"Table {table_name} is empty. Skipping profiling.")
                return None
            sample_data = df.head(5).to_dict(orient="records")
            stats = {
                "row_count_sample": len(df),
                "unique_values_count": df.nunique().to_dict(),
                "missing_values_count": df.isnull().sum().to_dict()
            }
            return {"sample_data": sample_data, "stats": stats, "columns": list(df.columns)}
        except Exception as e:
            logger.error(f"Error querying table {table_name}: {e}")
            return None

    def analyze_with_llm(self, table_name: str, profiling_data: dict) -> TableSemantic:
        logger.info(f"Calling LLM ({self.model_name}) to analyze semantics for table: {table_name}")
        
        prompt = f"""
        Table Name: {table_name}
        Columns: {profiling_data['columns']}
        
        Data Statistics (from a sample):
        {json.dumps(profiling_data['stats'], indent=2)}
        
        Data Sample (Top 5 rows):
        {json.dumps(profiling_data['sample_data'], indent=2)}
        """
        
        try:
            # Instructor + LiteLLM magic: Automatically enforces Pydantic schema across 100+ LLMs
            response = self.client.chat.completions.create(
                model=self.model_name,
                response_model=TableSemantic,
                messages=[
                    {"role": "system", "content": "You are the 'Data Profiler Agent' (Digital Archeologist) for the Aletheia project.\n"
                                                  "Your job is to analyze data distributions and samples to validate semantic hypotheses about legacy database tables.\n"
                                                  "You must infer the real-world semantic meaning of the table and each column based on the data types, distributions, and actual values in the sample."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
            )
            return response
        except Exception as e:
            logger.error(f"LLM Analysis failed: {e}")
            return None

    def run(self):
        logger.info("Starting Profiling workflow...")
        session = self.Session()
        try:
            tables = session.query(ExtractedTable).all()
            for table_meta in tables:
                logger.info(f"--- Processing {table_meta.table_name} ---")
                profiling_data = self.profile_table(table_meta.table_name, session)
                if not profiling_data:
                    continue
                
                # LLM perfectly returns a Pydantic object (TableSemantic)
                llm_analysis = self.analyze_with_llm(table_meta.table_name, profiling_data)
                if not llm_analysis:
                    continue
                    
                logger.info(f"Table Hypothesis: {llm_analysis.table_hypothesis}")
                
                col_meta_dict = {c.column_name: c for c in table_meta.columns}
                for col_analysis in llm_analysis.columns:
                    col_name = col_analysis.column_name
                    if col_name in col_meta_dict:
                        col_record = col_meta_dict[col_name]
                        profile = session.query(ColumnProfile).filter_by(column_id=col_record.id).first()
                        if not profile:
                            profile = ColumnProfile(column_id=col_record.id)
                            session.add(profile)
                        profile.semantic_type = col_analysis.semantic_type
                        profile.semantic_hypothesis = col_analysis.hypothesis
                        profile.profiled_at = datetime.utcnow()
                        
                session.commit()
                logger.info(f"Successfully saved semantic profiles for {table_meta.table_name} to PostGIS.")
        except Exception as e:
            session.rollback()
            logger.error(f"Workflow error: {e}")
        finally:
            session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Data Profiler Agent (LiteLLM Semantic Analysis)")
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}"), help="Source DB")
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"), help="PostGIS Metadata DB")
    parser.add_argument("--model", required=True, help="LiteLLM model name (e.g., gpt-4o, gemini/gemini-1.5-pro, anthropic/claude-3-sonnet-20240229, ollama/llama3)")
    
    args = parser.parse_args()
    
    agent = DataProfilerAgent(
        source_db_url=args.source, 
        metadata_db_url=args.target,
        model_name=args.model
    )
    agent.run()
