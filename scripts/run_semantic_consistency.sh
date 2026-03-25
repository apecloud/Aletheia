#!/bin/bash

# Aletheia: Run Semantic Consistency Agent

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "================================================="
echo "Starting Aletheia Semantic Consistency Agent..."
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
echo "Semantic Consistency Agent (Evaluating Ontology)..."
echo "-------------------------------------------------"
python agents/semantic_consistency_agent.py --model "$MODEL"

echo "================================================="
echo "Done! The semantic consistency report has been generated."
