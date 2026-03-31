import os
import argparse
import logging
from sqlalchemy import create_engine, inspect, Column, Integer, String, Boolean, ForeignKey, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("MetadataScraperAgent")

Base = declarative_base()

class ExtractedTable(Base):
    __tablename__ = 'aletheia_extracted_tables'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    schema_name = Column(String(255))
    table_name = Column(String(255), nullable=False)
    table_comment = Column(String(1000))
    extracted_at = Column(DateTime, default=datetime.utcnow)
    
    columns = relationship("ExtractedColumn", back_populates="table", cascade="all, delete")

class ExtractedColumn(Base):
    __tablename__ = 'aletheia_extracted_columns'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    table_id = Column(Integer, ForeignKey('aletheia_extracted_tables.id'), nullable=False)
    column_name = Column(String(255), nullable=False)
    data_type = Column(String(255), nullable=False)
    is_primary_key = Column(Boolean, default=False)
    is_nullable = Column(Boolean, default=True)
    column_comment = Column(String(1000))
    
    table = relationship("ExtractedTable", back_populates="columns")

class MetadataScraperAgent:
    def __init__(self, source_db_url: str, target_db_url: str):
        """
        source_db_url: URL to the legacy database (MySQL)
        target_db_url: URL to the ontology storage database (PostGIS)
        """
        logger.info(f"Connecting to Source DB: {source_db_url.split('@')[-1]}")
        self.source_engine = create_engine(source_db_url)
        
        logger.info(f"Connecting to Target DB (PostGIS): {target_db_url.split('@')[-1]}")
        self.target_engine = create_engine(target_db_url)
        
        # Initialize target schema
        Base.metadata.create_all(self.target_engine)
        self.Session = sessionmaker(bind=self.target_engine)

    def run(self, schema: str = None):
        """
        Extracts metadata from the source DB and stores it into the PostGIS target DB.
        """
        logger.info("Starting metadata extraction process...")
        inspector = inspect(self.source_engine)
        
        target_session = self.Session()
        
        try:
            # Clear previous extraction for idempotency (optional, but good for testing)
            target_session.execute(text('TRUNCATE TABLE aletheia_extracted_tables CASCADE'))
            target_session.commit()
            
            tables = inspector.get_table_names(schema=schema)
            logger.info(f"Found {len(tables)} tables in source database.")
            
            for table_name in tables:
                logger.info(f"Analyzing table: {table_name}")
                table_comment = inspector.get_table_comment(table_name, schema=schema).get('text', '')
                
                # Create Table Record
                extracted_table = ExtractedTable(
                    schema_name=schema,
                    table_name=table_name,
                    table_comment=table_comment
                )
                target_session.add(extracted_table)
                target_session.flush() # Get the ID
                
                # Extract Columns
                pk_constraint = inspector.get_pk_constraint(table_name, schema=schema)
                pk_columns = pk_constraint.get('constrained_columns', []) if pk_constraint else []
                
                columns = inspector.get_columns(table_name, schema=schema)
                for col in columns:
                    col_name = col['name']
                    col_type = str(col['type'])
                    is_pk = col_name in pk_columns
                    is_nullable = col.get('nullable', True)
                    col_comment = col.get('comment', '')
                    
                    extracted_col = ExtractedColumn(
                        table_id=extracted_table.id,
                        column_name=col_name,
                        data_type=col_type,
                        is_primary_key=is_pk,
                        is_nullable=is_nullable,
                        column_comment=col_comment
                    )
                    target_session.add(extracted_col)
            
            target_session.commit()
            logger.info("Metadata extraction completed and saved to PostGIS successfully!")
            
        except Exception as e:
            target_session.rollback()
            logger.error(f"Failed to extract metadata: {e}")
        finally:
            target_session.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Metadata Scraper Agent")
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}"), 
                        help="Source legacy DB connection string (MySQL)")
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"), 
                        help="Target PostGIS DB connection string to store ontology metadata")
    
    args = parser.parse_args()
    
    agent = MetadataScraperAgent(source_db_url=args.source, target_db_url=args.target)
    agent.run()
