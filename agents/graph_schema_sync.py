"""Sync a tenant's approved typed ontology (agents/graph_ontology_registry.py)
to real Nebula TAG/EDGE TYPE schema. Replaces the old single-TAG/single-EDGE
`ensure_schema()` (scripts/import_hotpotqa_nebula_tenant.py) with one TAG per
node type and one EDGE TYPE per relation type, driven deterministically from
the approved registry -- same DDL-issuing pattern already proven in
agents/graph_ingestion_agent.py (there, DDL is LLM-generated per business
object/relationship; here, it's derived from the reviewed/approved registry).
"""

from __future__ import annotations

import re
import time

try:
    import graph_ontology_registry as registry
except ModuleNotFoundError:
    import agents.graph_ontology_registry as registry

DATA_TYPE_TO_NGQL = {
    "string": "string",
    "int64": "int64",
    "double": "double",
    "bool": "bool",
    "timestamp": "timestamp",
}

def _safe_identifier(name: str) -> str:
    """Validate ``name`` for use as a backtick-quoted Nebula TAG/EDGE/property
    identifier. Nebula's backtick-quoted identifiers accept spaces and most
    punctuation -- confirmed against a live cluster (``CREATE TAG
    \\`Social group\\`(...)`` is valid nGQL) -- so this does NOT rewrite the
    name into a different string: doing so would desync it from the raw
    type/relation name used elsewhere (``insert_vertices``/``insert_edges``
    interpolate the same raw dict key into backticks without calling this
    function, so a renamed TAG here would no longer match the INSERT
    VERTEX/EDGE target). It only rejects characters that would let the name
    break out of the backtick-quoted identifier and inject arbitrary nGQL:
    a literal backtick, or newline/control characters."""
    name = str(name or "").strip()
    if not name or "`" in name or any(ord(ch) < 0x20 for ch in name):
        raise ValueError(f"unsafe Nebula tag/edge/property identifier: {name!r}")
    return name


def _property_clause(properties: list[dict[str, str]]) -> str:
    if not properties:
        return ""
    parts = []
    for prop in properties:
        prop_name = _safe_identifier(prop["name"])
        ngql_type = DATA_TYPE_TO_NGQL.get(prop.get("data_type", "string"), "string")
        parts.append(f"`{prop_name}` {ngql_type}")
    return "(" + ", ".join(parts) + ")"


def sync_tenant_schema(session, nebula_client, tenant_id: str, *, propagation_sleep_seconds: float = 11.0) -> dict:
    """Create (idempotently) one Nebula TAG per approved node type and one
    EDGE TYPE per approved edge type for this tenant. Returns a summary dict.
    Sleeps once at the end (not per-statement) to let Nebula's meta service
    propagate the new schema before the caller starts inserting data --
    mirrors the sleep already used by the old single-tag ensure_schema()."""
    node_types = registry.get_approved_node_types(session, tenant_id)
    edge_types = registry.get_approved_edge_types(session, tenant_id)

    created_tags = []
    for node_type in node_types:
        tag_name = _safe_identifier(node_type["name"])
        clause = _property_clause(node_type.get("properties") or [])
        nebula_client.execute_query(f"CREATE TAG IF NOT EXISTS `{tag_name}`{clause};")
        created_tags.append(tag_name)

    created_edges = []
    for edge_type in edge_types:
        edge_name = _safe_identifier(edge_type["name"])
        clause = _property_clause(edge_type.get("properties") or [])
        nebula_client.execute_query(f"CREATE EDGE IF NOT EXISTS `{edge_name}`{clause};")
        created_edges.append(edge_name)

    if created_tags or created_edges:
        time.sleep(propagation_sleep_seconds)

    return {"tags": created_tags, "edges": created_edges}
