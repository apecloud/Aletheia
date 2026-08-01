#!/bin/bash

# Aletheia: Run Graph Ingestion Agent (Atlas/Phoenix to Nebula Graph)

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/model_defaults.sh"

# Optional parameter to specify phase: 1 (nodes), 2 (edges), or all (default).
# Pass --include-unapproved as a later argument for explicit legacy/demo mode.
PHASE=${1:-all}
TENANT=${ALETHEIA_TENANT:-default}
INCLUDE_UNAPPROVED_FLAG=""
shift || true
while [ "$#" -gt 0 ]; do
    case "$1" in
        --tenant)
            TENANT="$2"
            shift 2
            ;;
        --include-unapproved)
            INCLUDE_UNAPPROVED_FLAG="--include-unapproved"
            shift
            ;;
        *)
            shift
            ;;
    esac
done

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
    MODEL="$ALETHEIA_GEMINI_MODEL"
elif [ -n "$OPENAI_API_KEY" ]; then
    MODEL="$ALETHEIA_OPENAI_MODEL"
else
    echo "⚠️ Error: No API Key found. Please export GEMINI_API_KEY or OPENAI_API_KEY."
    exit 1
fi

echo "Using Model: $MODEL"
echo "Using Tenant: $TENANT"

echo "-------------------------------------------------"
echo "Executing Graph ETL Pipeline..."
echo "-------------------------------------------------"
python agents/graph_ingestion_agent.py --tenant "$TENANT" --model "$MODEL" --phase "$PHASE" $INCLUDE_UNAPPROVED_FLAG

echo "================================================="
echo "Done! The data is now available in Nebula Graph."
