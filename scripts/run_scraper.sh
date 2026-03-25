#!/bin/bash

# Aletheia: Run Data Scraper Agent

# Activate the virtual environment if it exists
if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Set the project root directory
PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "========================================="
echo "Starting Aletheia Data Scraper Agent..."
echo "========================================="

# Example 1: Scrape Titanic passenger data (CSV)
echo "[1/2] Scraping Titanic dataset (CSV)..."
python agents/data_scraper_agent.py \
    --url "https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv" \
    --table "titanic_passengers" \
    --type "csv"

echo ""

# Example 2: Scrape generic user data (JSON)
echo "[2/2] Scraping JSONPlaceholder users (JSON)..."
python agents/data_scraper_agent.py \
    --url "https://jsonplaceholder.typicode.com/users" \
    --table "api_users" \
    --type "json"

echo "========================================="
echo "Done. Data has been imported into MySQL."
echo "Check your database to verify the tables."
