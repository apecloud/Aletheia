#!/bin/bash

# Aletheia: Run Semantic Consistency Agent

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/model_defaults.sh"

echo "================================================="
echo "Starting Aletheia Semantic Consistency Agent..."
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

echo "-------------------------------------------------"
echo "Semantic Consistency Agent (Evaluating Ontology)..."
echo "-------------------------------------------------"
python agents/semantic_consistency_agent.py --model "$MODEL"

echo "================================================="
echo "Done! The semantic consistency report has been generated."
