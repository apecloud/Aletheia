#!/bin/bash

# Aletheia: Run Action Synthesizer Group

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "================================================="
echo "Starting Aletheia Action Synthesizer..."
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
echo "Action Synthesizer Agent (Mapping stored procedures and triggers)..."
echo "-------------------------------------------------"
python agents/action_synthesizer_agent.py --model "$MODEL"

echo "================================================="
echo "Done! The Business Actions have been extracted and mapped in PostGIS."
