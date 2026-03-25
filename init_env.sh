#!/bin/bash
set -e

echo "Creating isolated Python virtual environment for Aletheia..."
python3.11 -m venv .venv

echo "Activating virtual environment..."
source .venv/bin/activate

echo "Upgrading pip..."
pip install --upgrade pip

echo "Installing Data Scraper dependencies..."
pip install pandas SQLAlchemy pymysql "requests>=2.32.3"

echo "Environment setup complete! To activate the environment later, run:"
echo "source .venv/bin/activate"
