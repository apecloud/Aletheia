"""Shared low-level utilities for the enrichment/graph-search/reasoning loop
harnesses. These three harnesses are distinct orchestration layers (different
domains, different DB tables, only enrichment mutates data) that happened to
each define byte-identical or near-identical copies of these three helpers.
Callers pass their own zero-denominator/container-passthrough defaults
explicitly so consolidating this code does not change any harness's behavior.
"""

from __future__ import annotations

import json
from typing import Any


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def json_load(value: Any, default: Any, *, passthrough_containers: bool = False) -> Any:
    if passthrough_containers:
        if value in (None, ""):
            return default
        if isinstance(value, (dict, list)):
            return value
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def ratio(numerator: int | float, denominator: int | float, zero_denominator_default: float) -> float:
    if denominator <= 0:
        return zero_denominator_default
    return round(float(numerator) / float(denominator), 4)
