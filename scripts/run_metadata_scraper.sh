#!/bin/bash

# Aletheia: Run Metadata Scraper Agent

# Activate the virtual environment if it exists
if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "================================================="
echo "Starting Aletheia Knowledge Extraction Group..."
echo "--> Agent: Metadata Scraper"
echo "================================================="

# Start PostGIS if not already running (optional helper)
# docker-compose -f docker/docker-compose.yml up -d aletheia-postgis

echo "Extracting metadata from MySQL and saving to PostGIS..."
python agents/metadata_scraper_agent.py

echo "================================================="
echo "Done! The metadata is now structured and safely stored in PostGIS."
