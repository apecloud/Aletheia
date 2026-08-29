"""Shared two-tier name-matching algorithm: cheap exact/alias match first,
one LLM semantic-match call as fallback if nothing cheap matched.

Extracted from ``relation_catalog.RelationCatalog``'s original
``_cheap_match``/``_semantic_match``/``_parse_response`` (algorithm and
prompt format unchanged) so ``agents/node_type_catalog.py``'s
``NodeTypeCatalog`` can reuse the exact same governance pattern for node
TYPE names instead of re-implementing it -- see the "ontology mapping"
stage of the text-QA -> ontology extraction -> mapping -> human review ->
ontology-aware reasoning pipeline. ``RelationCatalog`` itself is refactored
to call these functions too, so relation-name and node-type governance can
never quietly drift apart into two different matching behaviors.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from aletheia.llms.hard_timeout import call_with_hard_timeout

MAX_CANDIDATES_IN_PROMPT = 60

# Format-only, not domain-specific -- shared verbatim by every caller
# (relation names, node type names, ...) so the JSON contract never drifts
# per-caller. Semantic instructions (what "matching" means for this domain)
# stay caller-owned via `system_prompt`.
JSON_INSTRUCTION = (
    '\n\nRespond with ONLY a JSON object in this exact format '
    '(no markdown, no extra text):\n{"canonical_match": str}'
)


def normalize_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")


def format_candidates(entries: dict[str, dict[str, Any]], max_candidates: int = MAX_CANDIDATES_IN_PROMPT) -> str:
    items = list(entries.items())[:max_candidates]
    if not items:
        return "  (none yet)"
    lines = []
    for name, meta in items:
        desc = meta.get("description", "")
        lines.append(f'  - "{name}"' + (f": {desc}" if desc else ""))
    return "\n".join(lines)


def cheap_match(raw_name: str, entries: dict[str, dict[str, Any]]) -> str | None:
    """Exact match after casing/punctuation normalization, against
    canonical names or their known aliases. No LLM call."""
    key = normalize_key(raw_name)
    if not key:
        return None
    for canonical, meta in entries.items():
        if key == normalize_key(canonical):
            return canonical
        if key in {normalize_key(a) for a in meta.get("aliases", [])}:
            return canonical
    return None


def parse_canonical_match_response(content: Any) -> dict | None:
    if not isinstance(content, str):
        return None

    def valid(payload: Any) -> dict | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("canonical_match"), str):
            return None
        return payload

    try:
        return valid(json.loads(content))
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content)
    if match:
        try:
            return valid(json.loads(match.group(1).strip()))
        except (json.JSONDecodeError, TypeError):
            pass
    match = re.search(r"\{[\s\S]*\}", content)
    if match:
        try:
            return valid(json.loads(match.group(0)))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def semantic_match_via_llm(
    raw_name: str, evidence: str, entries: dict[str, dict[str, Any]], *,
    planner: Any, executor: ThreadPoolExecutor, timeout: float,
    system_prompt: str, user_template: str, completion_kwargs: dict[str, Any],
    logger: logging.Logger, label: str = "entry",
) -> str | None:
    """One LLM call: does ``raw_name`` mean the same real-world thing as an
    existing canonical entry? Returns the matched canonical name, or None.
    Degrades to None on any failure (timeout, provider error, unparseable
    response) -- a missed dedup just adds one new catalog entry, it never
    blocks ingestion. ``label`` is only used in log messages (e.g.
    "RelationCatalog" vs "NodeTypeCatalog") to tell call sites apart."""
    try:
        from litellm import completion
    except ImportError:
        return None

    user_msg = user_template.format(
        raw_name=raw_name, evidence=evidence or "(none)", candidates=format_candidates(entries),
    )
    try:
        try:
            raw_response = call_with_hard_timeout(
                executor, completion,
                model=planner.model,
                messages=[
                    {"role": "system", "content": system_prompt + JSON_INSTRUCTION},
                    {"role": "user", "content": user_msg},
                ],
                timeout=timeout,
                temperature=0.0,
                **completion_kwargs,
            )
        except TimeoutError:
            logger.warning("%s: hard timeout matching %r, treating as new", label, raw_name)
            return None

        if not raw_response or not raw_response.choices:
            return None
        from aletheia.llms.planner import LLMPlanner
        candidates = LLMPlanner._response_text_candidates(raw_response.choices[0].message)
        for candidate in candidates:
            parsed = parse_canonical_match_response(candidate)
            if parsed is not None:
                match = parsed.get("canonical_match", "").strip()
                return match if match in entries else None
        return None
    except Exception as exc:
        logger.warning("%s: match call failed for %r: %s", label, raw_name, exc)
        return None
