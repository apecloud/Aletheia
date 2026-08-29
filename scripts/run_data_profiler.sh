#!/bin/bash

# Aletheia: Run Data Profiler Agent (LiteLLM)

# Activate the virtual environment if it exists
if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/scripts/lib/model_defaults.sh"

echo "================================================="
echo "Starting Aletheia Knowledge Extraction Group..."
echo "--> Agent: Data Profiler (LiteLLM + Instructor)"
echo "================================================="

# LiteLLM allows using unified model names and automatically picks up standard env variables.
# Models supported: https://docs.litellm.ai/docs/providers

# --- Example 1: OpenAI ---
# export OPENAI_API_KEY="sk-..."
# MODEL="gpt-4o"

# --- Example 2: Google Gemini ---
# export GEMINI_API_KEY="AIza..."

# --- Example 3: Anthropic ---
# export ANTHROPIC_API_KEY="sk-ant-..."
# MODEL="anthropic/claude-3-5-sonnet-20240620"

# --- Example 4: Local Ollama ---
# export OLLAMA_API_BASE="http://localhost:11434"
# MODEL="ollama/llama3"

# Currently set to use OpenAI as default, change as needed:
if [ -n "$OPENAI_API_KEY" ]; then
    MODEL="$ALETHEIA_OPENAI_MODEL"
elif [ -n "$GEMINI_API_KEY" ]; then
    MODEL="$ALETHEIA_GEMINI_MODEL"
else
    echo "⚠️ Error: No API Key found. Please export OPENAI_API_KEY or GEMINI_API_KEY."
    exit 1
fi

echo "Using Model: $MODEL"

python -m aletheia.ingest.data_profiler --model "$MODEL"

echo "================================================="
echo "Done! The semantic profiles have been updated in PostGIS."
