import os
import argparse
import logging
import pandas as pd
from sqlalchemy import create_engine
from datasets import load_dataset
from huggingface_hub import login

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HFDatasetScraper")

class HFDatasetScraper:
    def __init__(self, db_url: str):
        self.engine = create_engine(db_url)
        logger.info(f"Connected to database: {db_url.split('@')[-1]}")

    def fetch_and_import(self, dataset_name: str, table_name: str, split: str = 'train', max_rows: int = 10000, token: str = None):
        """
        Fetches a dataset from Hugging Face Datasets and imports it into MySQL.
        """
        logger.info(f"Loading dataset '{dataset_name}' (split: {split})...")
        try:
            # Login if token is provided
            if token:
                logger.info("Using provided Hugging Face token for authentication...")
                login(token=token)

            # Load dataset from Hugging Face
            dataset = load_dataset(dataset_name, split=split, token=token)
            
            # Convert to pandas DataFrame
            logger.info("Converting to pandas DataFrame...")
            df = dataset.to_pandas()
            
            # Limit rows for testing
            if max_rows and len(df) > max_rows:
                logger.info(f"Limiting dataset from {len(df)} to {max_rows} rows.")
                df = df.head(max_rows)
            
            # Clean column names for MySQL
            df.columns = [str(c).strip().replace(' ', '_').replace('-', '_').lower() for c in df.columns]
            
            # Handle complex types (lists, dicts) by converting to string
            for col in df.columns:
                if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
                    logger.info(f"Converting complex column '{col}' to string.")
                    df[col] = df[col].astype(str)

            # Import into MySQL
            logger.info(f"Importing {len(df)} rows into table '{table_name}'...")
            df.to_sql(name=table_name, con=self.engine, if_exists='replace', index=False, chunksize=5000)
            logger.info("Import completed successfully!")
            
        except Exception as e:
            logger.error(f"Error fetching/importing dataset: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hugging Face Dataset Scraper for Aletheia")
    parser.add_argument("--dataset", required=True, help="Hugging Face dataset name (e.g., 'imdb', 'bank_marketing')")
    parser.add_argument("--table", required=True, help="Target MySQL table name")
    parser.add_argument("--split", default="train", help="Dataset split (default: train)")
    parser.add_argument("--rows", type=int, default=10000, help="Max rows to import (default: 10000)")
    parser.add_argument("--token", default=None, help="Hugging Face access token (optional, but needed for some datasets or gated access)")
    parser.add_argument("--db", default="mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data", 
                        help="MySQL connection string")
    
    args = parser.parse_args()
    
    scraper = HFDatasetScraper(db_url=args.db)
    scraper.fetch_and_import(dataset_name=args.dataset, table_name=args.table, split=args.split, max_rows=args.rows, token=args.token)
