"""Shared vertex-id scheme for HotpotQA/WebQSP graph-native tenants.

Extracted from the retired ``import_hotpotqa_kg_tenant.py`` (the SQL-backed
HotpotQA importer) -- ``entity_id()`` is backend-agnostic (just a qid-scoped
slug) and is reused by every graph-native importer (``import_hotpotqa_nebula_tenant.py``,
``import_webqsp_graph_tenant.py``) and by tests, so it lives in its own
module rather than disappearing with the SQL importer it originated in.
"""

from __future__ import annotations

import hashlib
import re


def _identifier(value: str, *, prefix: str = "hotpotqa", max_len: int = 60) -> str:
    text_value = re.sub(r"[^0-9a-zA-Z_]+", "_", str(value or "").lower()).strip("_")
    if not text_value:
        text_value = "value"
    if re.match(r"^[0-9]", text_value):
        text_value = f"_{text_value}"
    candidate = f"{prefix}_{text_value}" if prefix else text_value
    if len(candidate) <= max_len:
        return candidate
    digest = hashlib.sha1(candidate.encode("utf-8")).hexdigest()[:10]
    keep = max_len - len(digest) - 1
    return f"{candidate[:keep]}_{digest}"


def entity_id(qid: str, title_or_value: str) -> str:
    slug = _identifier(title_or_value, prefix="", max_len=40).lstrip("_") or "value"
    return f"{qid}:{slug}"
