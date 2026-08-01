#!/bin/bash
# Aletheia: shared model-version defaults for the legacy agent launcher scripts.
# Each launcher keeps its own provider precedence (which env var it checks
# first) -- this file only centralizes the literal model strings so there's
# one place to change either, and lets an operator override via env var.

: "${ALETHEIA_GEMINI_MODEL:=gemini/gemini-3.1-pro-preview}"
: "${ALETHEIA_OPENAI_MODEL:=gpt-4o}"
