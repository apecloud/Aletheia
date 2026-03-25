#!/bin/bash

# Aletheia: Run Business Context Agent

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

# Ensure docs directory exists
mkdir -p "$PROJECT_ROOT/docs"

echo "================================================="
echo "Starting Aletheia Business Context Agent..."
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
echo "Looking for external documentation in: ./docs"

echo "-------------------------------------------------"
echo "Business Context Agent (Aligning terminology)..."
echo "-------------------------------------------------"
python agents/business_context_agent.py --model "$MODEL" --docs-dir "$PROJECT_ROOT/docs"

echo "================================================="
echo "Done! The technical tables have been updated with business terminology."
