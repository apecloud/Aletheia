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

if [[ "$1" == "all" || "$1" == "--all" || "$1" == "phase-all" ]]; then
    echo "================================================="
    echo "Starting Pipeline: Running subsequent phases..."
    echo "================================================="
    
    echo ">>> Phase 1: Knowledge Extraction"
    ./scripts/run_metadata_scraper.sh
    ./scripts/run_data_profiler.sh
    ./scripts/run_business_context.sh

    echo ">>> Phase 2: Design & Modeling"
    ./scripts/run_design_modeling.sh
    ./scripts/run_action_synthesizer.sh

    echo ">>> Phase 3: Graph Ingestion"
    ./scripts/run_graph_ingestion.sh all

    echo ">>> Phase 4: Semantic Validation"
    ./scripts/run_semantic_consistency.sh

    echo ">>> Phase 5: Deep Ontology Reasoning"
    ./scripts/run_ontology_reasoning.sh
    
    echo "================================================="
    echo "Pipeline completed successfully!"
    echo "Check 'run_reasoning_result.md' for the Deep Reasoning Output."
    echo "================================================="
else
    echo "================================================="
    echo "Done! The complex dataset has been loaded."
    echo "You can now run the pipeline in order:"
    echo "1. Phase 1: Knowledge Extraction (Metadata, Profiler, Context)"
    echo "2. Phase 2: Design & Modeling"
    echo "3. Phase 3: Graph Ingestion (run_graph_ingestion.sh)"
    echo "4. Phase 4: Semantic Validation (run_semantic_consistency.sh)"
    echo "5. Phase 5: Deep Ontology Reasoning (run_ontology_reasoning.sh)"
    echo ""
    echo "Or pass 'all' to this script to run the complete pipeline automatically."
fi
