"""Shared boilerplate for the legacy standalone metadata-pipeline agents
(action_synthesizer_agent.py, business_context_agent.py, data_profiler_agent.py,
data_scraper_agent.py, graph_ingestion_agent.py, hf_dataset_scraper.py,
metadata_scraper_agent.py, semantic_consistency_agent.py). Each of these
independently copy-pasted the same logging setup and, in 4 cases, the same
"force gemini-3.1-pro-preview" model override -- centralized here so there's
one place to change either.
"""

import logging
import os

PINNED_GEMINI_MODEL = os.environ.get("ALETHEIA_LEGACY_GEMINI_MODEL", "gemini/gemini-3.1-pro-preview")


def configure_logging(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    return logging.getLogger(name)


def resolve_model_name(model_name: str) -> str:
    if model_name and "gemini" in model_name.lower():
        return PINNED_GEMINI_MODEL
    return model_name
