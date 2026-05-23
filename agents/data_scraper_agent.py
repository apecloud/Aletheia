import os
import requests
import pandas as pd
from sqlalchemy import create_engine
from urllib.parse import urlparse
import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GenericDataScraperAgent")

class GenericDataScraper:
    def __init__(self, db_url: str):
        """
        Initialize the scraper with a database connection.
        db_url: SQLAlchemy connection string.
        """
        self.engine = create_engine(db_url)
        logger.info(f"Initialized database connection to {db_url.split('@')[-1]}")

    def download_and_import(self, source_url: str, table_name: str, file_type: str = "csv", chunksize: int = 10000, if_exists: str = "replace"):
        """
        Downloads a dataset from a URL or reads a local file and imports it into the MySQL database.
        
        :param source_url: URL or local path to the public dataset.
        :param table_name: Name of the target table in MySQL.
        :param file_type: 'csv' or 'json'
        :param chunksize: Number of rows per chunk for insertion.
        :param if_exists: 'fail', 'replace', or 'append'
        """
        logger.info(f"Starting load from: {source_url}")
        
        try:
            # Handle different file types
            if file_type.lower() == 'csv':
                # Use pandas to read directly from URL
                df = pd.read_csv(source_url)
            elif file_type.lower() == 'json':
                import json
                
                # Check if it's a URL or local path
                if source_url.startswith("http://") or source_url.startswith("https://"):
                    response = requests.get(source_url)
                    response.raise_for_status()
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            data = [data]
                        df = pd.json_normalize(data)
                    except json.JSONDecodeError:
                        # Fallback to json lines if normal json decode fails
                        lines = response.text.strip().split('\n')
                        data = [json.loads(line) for line in lines if line.strip()]
                        df = pd.json_normalize(data)
                else:
                    try:
                        with open(source_url, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict):
                            data = [data]
                        df = pd.json_normalize(data)
                    except json.JSONDecodeError:
                        # Fallback to json lines
                        data = []
                        with open(source_url, "r", encoding="utf-8") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    data.append(json.loads(line))
                        df = pd.json_normalize(data)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")
                
            logger.info(f"Successfully loaded data. Shape: {df.shape}. Columns: {list(df.columns)}")
            
            # Clean column names (remove spaces, special characters, convert to lowercase)
            df.columns = [str(c).strip().replace(' ', '_').replace('-', '_').replace('.', '_') for c in df.columns]
            
            # Insert into database
            logger.info(f"Inserting data into table '{table_name}'...")
            df.to_sql(
                name=table_name,
                con=self.engine,
                if_exists=if_exists,
                index=False,
                chunksize=chunksize
            )
            logger.info(f"Successfully imported {len(df)} rows into '{table_name}'.")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to process dataset: {str(e)}")
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generic Data Scraper Agent for Aletheia")
    parser.add_argument("--url", required=True, help="URL or local path of the dataset (CSV/JSON)")
    parser.add_argument("--table", required=True, help="Target table name in MySQL")
    parser.add_argument("--type", default="csv", choices=["csv", "json"], help="File type (csv or json)")
    parser.add_argument("--db", default=os.environ.get("ALETHEIA_MYSQL_URL", f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}"), 
                        help="MySQL connection string")
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"), help="Tenant used for optional ontology web enrichment")
    parser.add_argument("--enrich-web", action="store_true", help="After data import, create draft web enrichment proposals for ontology artifacts")
    parser.add_argument("--metadata-db", default=os.environ.get("ALETHEIA_PG_URL", f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"), help="Metadata/PostGIS connection string for web enrichment")
    parser.add_argument("--artifact", action="append", default=[], help="Ontology artifact key to enrich. Repeatable.")
    parser.add_argument("--search-results-json", help="Deterministic search fixture for offline/test enrichment")
    parser.add_argument("--seed-url", action="append", default=[], help="Seed URL used as a search result for enrichment. Repeatable.")
    parser.add_argument("--enable-live-search", action="store_true", help="Use live web search for enrichment")
    parser.add_argument("--allowed-domain", action="append", default=[], help="Domain allowlist for enrichment crawling. Repeatable.")
    parser.add_argument("--allow-discovered-domains", action="store_true", help="Allow crawling public search-result domains outside allowlist")
    parser.add_argument("--max-enrich-artifacts", type=int, default=5)
    parser.add_argument("--max-results-per-query", type=int, default=3)
    parser.add_argument("--max-crawl-pages", type=int, default=2)
    
    args = parser.parse_args()
    
    scraper = GenericDataScraper(db_url=args.db)
    ok = scraper.download_and_import(source_url=args.url, table_name=args.table, file_type=args.type)
    if ok and args.enrich_web:
        sys.path.append(str(Path(__file__).resolve().parent))
        from web_enrichment_agent import WebEnrichmentAgent

        enrichment = WebEnrichmentAgent(
            metadata_db_url=args.metadata_db,
            tenant=args.tenant,
            search_results_json=args.search_results_json,
            seed_urls=args.seed_url,
            enable_live_search=args.enable_live_search,
            allowed_domains=args.allowed_domain,
            allow_discovered_domains=args.allow_discovered_domains,
            max_artifacts=args.max_enrich_artifacts,
            max_results_per_query=args.max_results_per_query,
            max_crawl_pages=args.max_crawl_pages,
        )
        result = enrichment.run(artifact_keys=args.artifact or None)
        logger.info(
            "Web enrichment completed: run=%s proposals=%s",
            result["run_key"],
            result["proposal_count"],
        )
