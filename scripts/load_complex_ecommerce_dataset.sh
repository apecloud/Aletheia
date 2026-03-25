#!/bin/bash

# Aletheia: Load Complex E-commerce Dataset (TPC-H style)

# Activate the virtual environment if it exists
if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "================================================="
echo "Fetching a Complex E-Commerce Dataset (Northwind) "
echo "This dataset features multiple normalized tables, "
echo "perfect for testing Object Modeler & Link Weaver. "
echo "================================================="

BASE_URL="https://raw.githubusercontent.com/graphql-compose/graphql-compose-examples/master/examples/northwind/data/csv"

echo "[1/6] Loading Customers table..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/customers.csv" \
    --table "customers" \
    --type "csv"

echo "[2/6] Loading Employees table..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/employees.csv" \
    --table "employees" \
    --type "csv"

echo "[3/6] Loading Orders table (1:N with Customers and Employees)..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/orders.csv" \
    --table "orders" \
    --type "csv"

echo "[4/6] Loading Categories table..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/categories.csv" \
    --table "categories" \
    --type "csv"

echo "[5/6] Loading Products table (1:N with Categories)..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/products.csv" \
    --table "products" \
    --type "csv"

echo "[6/6] Loading Order_Details table (M:N mapping between Orders and Products)..."
python agents/data_scraper_agent.py \
    --url "$BASE_URL/order_details.csv" \
    --table "order_details" \
    --type "csv"

echo "================================================="
echo "Done! The complex dataset has been loaded."
echo "You can now run:"
echo "1. ./scripts/run_metadata_scraper.sh"
echo "2. ./scripts/run_data_profiler.sh"
echo "3. ./scripts/run_design_modeling.sh"
echo "to see the Agent framework reverse engineer this schema!"
