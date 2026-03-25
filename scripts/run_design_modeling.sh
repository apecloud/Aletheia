#!/bin/bash

# Aletheia: Run Design & Modeling Group (Object Modeler + Link Weaver)

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "================================================="
echo "Starting Aletheia Design & Modeling Group..."
echo "================================================="

# Detect model based on env vars
if [ -n "$GEMINI_API_KEY" ]; then
    MODEL="gemini/gemini-3.1-pro-preview"
elif [ -n "$OPENAI_API_KEY" ]; then
    MODEL="gpt-4o"
else
    echo "⚠️ Error: No API Key found. Please export OPENAI_API_KEY or GEMINI_API_KEY."
    exit 1
fi

echo "Using Model: $MODEL"

echo "-------------------------------------------------"
echo "[1/2] Object Modeler Agent (Collapsing normalized tables)..."
echo "-------------------------------------------------"
python agents/object_modeler_agent.py --model "$MODEL"

echo ""
echo "-------------------------------------------------"
echo "[2/2] Link Weaver Agent (Discovering relationships)..."
echo "-------------------------------------------------"
python agents/link_weaver_agent.py --model "$MODEL"

echo "================================================="
echo "Done! The Business Objects and Links have been updated in PostGIS."
