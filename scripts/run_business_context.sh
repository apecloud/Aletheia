#!/bin/bash

# Aletheia: Run Business Context Agent

if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/model_defaults.sh"

# Ensure docs directory exists
mkdir -p "$PROJECT_ROOT/docs"

echo "================================================="
echo "Starting Aletheia Business Context Agent..."
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
echo "Looking for external documentation in: ./docs"

echo "-------------------------------------------------"
echo "Business Context Agent (Aligning terminology)..."
echo "-------------------------------------------------"
python agents/business_context_agent.py --model "$MODEL" --docs-dir "$PROJECT_ROOT/docs"

echo "================================================="
echo "Done! The technical tables have been updated with business terminology."
