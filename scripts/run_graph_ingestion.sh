#!/bin/bash

# Aletheia: Run Graph Ingestion Agent (Atlas/Phoenix to Nebula Graph)

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

# Optional parameter to specify phase: 1 (nodes), 2 (edges), or all (default).
# Pass --include-unapproved as a second argument for explicit legacy/demo mode.
PHASE=${1:-all}
INCLUDE_UNAPPROVED_FLAG=""
if [ "${2:-}" == "--include-unapproved" ]; then
    INCLUDE_UNAPPROVED_FLAG="--include-unapproved"
fi

echo "================================================="
echo "Starting Graph Ingestion Agent (Nebula Graph)..."
if [ "$PHASE" == "1" ]; then
    echo "Running Phase 1: Object Nodes Ingestion."
elif [ "$PHASE" == "2" ]; then
    echo "Running Phase 2: Relationship Edges Ingestion."
else
    echo "Running All Phases (Nodes and Edges)."
fi
echo "================================================="

# Detect model based on env vars
if [ -n "$GEMINI_API_KEY" ]; then
    MODEL="gemini/gemini-3.1-pro-preview"
elif [ -n "$OPENAI_API_KEY" ]; then
    MODEL="gpt-4o"
else
    echo "⚠️ Error: No API Key found. Please export GEMINI_API_KEY or OPENAI_API_KEY."
    exit 1
fi

echo "Using Model: $MODEL"

echo "-------------------------------------------------"
echo "Executing Graph ETL Pipeline..."
echo "-------------------------------------------------"
python agents/graph_ingestion_agent.py --model "$MODEL" --phase "$PHASE" $INCLUDE_UNAPPROVED_FLAG

echo "================================================="
echo "Done! The data is now available in Nebula Graph."
