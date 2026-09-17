"""Typed ontology registry for graph-native tenants (HotpotQA/WebQSP-style,
stored in Nebula) -- reuses the exact same governance envelope
(`OntologyArtifact`, draft -> review -> approved) that
`SchemaGraphModelingAgent` uses for SQL-schema-derived node/edge types, with
`source_agent="GraphNativeTypeRegistrar"` to distinguish graph-native-origin
types from SQL-origin ones sharing the same Postgres tables.

Deliberately does not import `schema_graph_modeling_agent.GraphNodeTypeDraft`/
`GraphEdgeTypeDraft` directly -- those Pydantic models require SQL-only
fields (`mapped_tables`, `source_table`/`target_table`) that have no
equivalent here, and mutating their shape would risk the dormant SQL
pipeline's own tests/traceability validation. This module defines its own
plain-dict payload shape instead, conceptually compatible (same field names
where they overlap: name/description/confidence/evidence/subclass_of/
disjoint_with for nodes; domain/range/cardinality for edges) but stored via
`OntologyArtifact.payload_json` as an arbitrary dict, not a serialized
Pydantic instance.
"""

from __future__ import annotations

from typing import Any

from aletheia.ontology.store import OntologyArtifact, canonical_key_for, upsert_artifact

SOURCE_AGENT = "GraphNativeTypeRegistrar"
NODE_ARTIFACT_TYPE = "object"
EDGE_ARTIFACT_TYPE = "link"
ACTION_ARTIFACT_TYPE = "action"

VALID_DATA_TYPES = {"string", "int64", "double", "bool", "timestamp"}


def _normalize_properties(properties: list[dict[str, str]] | None) -> list[dict[str, str]]:
    normalized = []
    for prop in properties or []:
        name = str(prop.get("name") or "").strip()
        data_type = str(prop.get("data_type") or "string").strip().lower()
        if not name:
            continue
        if data_type not in VALID_DATA_TYPES:
            data_type = "string"
        normalized.append({"name": name, "data_type": data_type})
    return normalized


def propose_node_type(
    session,
    *,
    tenant_id: str,
    name: str,
    description: str = "",
    properties: list[dict[str, str]] | None = None,
    confidence: float = 0.8,
    evidence: list[str] | None = None,
    subclass_of: list[str] | None = None,
    disjoint_with: list[str] | None = None,
    reasoning_focus: list[dict[str, Any]] | None = None,
    status: str = "draft",
) -> OntologyArtifact:
    """Register (or update) a node type for this tenant. Returns the
    OntologyArtifact row -- caller is responsible for session.commit().

    ``reasoning_focus`` is an optional, tenant-curated list of
    ``{"name", "description", "signals"}`` dicts naming the business
    dimensions that matter when reasoning about instances of this type
    (e.g. an Issue's urgency/response-time, a PullRequest's review-latency/
    blast-radius). ``signals`` are just human-readable hints (property or
    relation names, as they already appear in entity_facts/relation_summary)
    for prompt-writing convenience -- not a formula language, and nothing
    here parses or validates them. This is descriptive metadata only, read
    generically downstream (see reasoning_entity_config, traversal.py's
    _reasoning_focus_dimensions, and LLMPlanner.synthesize_relation_insight)
    -- no business vocabulary is ever hardcoded in that shared code, same
    as edge-type descriptions (propose_edge_type)."""
    payload = {
        "name": name,
        "properties": _normalize_properties(properties),
        "subclass_of": list(subclass_of or []),
        "disjoint_with": list(disjoint_with or []),
        "reasoning_focus": list(reasoning_focus or []),
    }
    return upsert_artifact(
        session,
        artifact_type=NODE_ARTIFACT_TYPE,
        natural_key=name,
        name=name,
        description=description,
        payload=payload,
        source_refs=evidence or [],
        source_agent=SOURCE_AGENT,
        project_id=tenant_id,
        confidence=confidence,
        status=status,
    )


def propose_edge_type(
    session,
    *,
    tenant_id: str,
    name: str,
    domain: list[str],
    range: list[str],
    description: str = "",
    properties: list[dict[str, str]] | None = None,
    cardinality: str | None = None,
    confidence: float = 0.8,
    evidence: list[str] | None = None,
    status: str = "draft",
) -> OntologyArtifact:
    """Register (or update) an edge (relation) type for this tenant. Returns
    the OntologyArtifact row -- caller is responsible for session.commit()."""
    payload = {
        "name": name,
        "domain": list(domain or []),
        "range": list(range or []),
        "cardinality": cardinality,
        "properties": _normalize_properties(properties),
    }
    return upsert_artifact(
        session,
        artifact_type=EDGE_ARTIFACT_TYPE,
        natural_key=name,
        name=name,
        description=description,
        payload=payload,
        source_refs=evidence or [],
        source_agent=SOURCE_AGENT,
        project_id=tenant_id,
        confidence=confidence,
        status=status,
    )


def propose_action(
    session,
    *,
    tenant_id: str,
    name: str,
    applies_to: list[str],
    trigger_event: str,
    input_parameters: list[str] | None = None,
    expected_effects: list[str] | None = None,
    guardrails: list[str] | None = None,
    description: str = "",
    confidence: float = 0.8,
    evidence: list[str] | None = None,
    status: str = "draft",
) -> OntologyArtifact:
    """Register (or update) an operational action for this tenant's graph-
    native ontology. Returns the OntologyArtifact row -- caller is
    responsible for session.commit().

    Field vocabulary deliberately matches the "action" ontology_part shape
    already produced by aletheia/enrichment/iterative_enrichment.py's LLM
    text-mining pipeline (trigger_event/applies_to/input_parameters/
    expected_effects/guardrails) -- that's the shape web/app/screens.jsx's
    DiscoveredOntologyReview.operationalRows already knows how to render, so
    a graph-native-origin action (this function) and a text-mined one look
    the same in the UI. Distinct from aletheia/modeling/action_synthesizer.py's
    BusinessAction (SQL routine/trigger-derived, action_type/source_name/
    is_safe/inputs_json/outputs_json) -- that shape only makes sense for a
    SQL-schema tenant, not a graph-native one."""
    payload = {
        "name": name,
        "applies_to": list(applies_to or []),
        "trigger_event": trigger_event,
        "input_parameters": list(input_parameters or []),
        "expected_effects": list(expected_effects or []),
        "guardrails": list(guardrails or []),
    }
    return upsert_artifact(
        session,
        artifact_type=ACTION_ARTIFACT_TYPE,
        natural_key=name,
        name=name,
        description=description,
        payload=payload,
        source_refs=evidence or [],
        source_agent=SOURCE_AGENT,
        project_id=tenant_id,
        confidence=confidence,
        status=status,
    )


def _query_types(session, *, tenant_id: str, artifact_type: str, status: str | None) -> list[dict[str, Any]]:
    import json

    query = session.query(OntologyArtifact).filter_by(
        project_id=tenant_id,
        artifact_type=artifact_type,
        source_agent=SOURCE_AGENT,
    )
    if status is not None:
        query = query.filter_by(status=status)
    results = []
    for artifact in query.all():
        payload = json.loads(artifact.payload_json or "{}")
        payload["_canonical_key"] = artifact.canonical_key
        payload["_confidence"] = artifact.confidence
        payload["_status"] = artifact.status
        # artifact.description lives on the row itself, not inside
        # payload_json -- without this, every caller (reasoning_link_config,
        # schema_sync, ...) silently gets "" regardless of what was passed
        # to propose_edge_type/propose_node_type's description= argument.
        payload.setdefault("description", artifact.description or "")
        results.append(payload)
    return results


def get_approved_node_types(session, tenant_id: str) -> list[dict[str, Any]]:
    return _query_types(session, tenant_id=tenant_id, artifact_type=NODE_ARTIFACT_TYPE, status="approved")


def get_approved_edge_types(session, tenant_id: str) -> list[dict[str, Any]]:
    return _query_types(session, tenant_id=tenant_id, artifact_type=EDGE_ARTIFACT_TYPE, status="approved")


def get_all_node_types(session, tenant_id: str) -> list[dict[str, Any]]:
    """Every node type regardless of review status -- used by
    ``graph_schema_sync.sync_tenant_schema(..., include_draft=True)`` so a
    "review_required" tenant can still write data typed with a not-yet-
    approved TAG (governance gates whether ``reasoning_engine.py`` can SEE
    the data at query time, not whether it can be written)."""
    return _query_types(session, tenant_id=tenant_id, artifact_type=NODE_ARTIFACT_TYPE, status=None)


def get_all_edge_types(session, tenant_id: str) -> list[dict[str, Any]]:
    """Edge-type counterpart of ``get_all_node_types``."""
    return _query_types(session, tenant_id=tenant_id, artifact_type=EDGE_ARTIFACT_TYPE, status=None)


def get_approved_actions(session, tenant_id: str) -> list[dict[str, Any]]:
    return _query_types(session, tenant_id=tenant_id, artifact_type=ACTION_ARTIFACT_TYPE, status="approved")


def get_all_actions(session, tenant_id: str) -> list[dict[str, Any]]:
    """Action counterpart of ``get_all_node_types``."""
    return _query_types(session, tenant_id=tenant_id, artifact_type=ACTION_ARTIFACT_TYPE, status=None)


def get_node_type(session, tenant_id: str, name: str) -> dict[str, Any] | None:
    import json

    canonical_key = canonical_key_for(NODE_ARTIFACT_TYPE, name)
    artifact = session.query(OntologyArtifact).filter_by(project_id=tenant_id, canonical_key=canonical_key).first()
    if artifact is None:
        return None
    payload = json.loads(artifact.payload_json or "{}")
    payload["_canonical_key"] = artifact.canonical_key
    payload["_status"] = artifact.status
    return payload


def get_edge_type(session, tenant_id: str, name: str) -> dict[str, Any] | None:
    import json

    canonical_key = canonical_key_for(EDGE_ARTIFACT_TYPE, name)
    artifact = session.query(OntologyArtifact).filter_by(project_id=tenant_id, canonical_key=canonical_key).first()
    if artifact is None:
        return None
    payload = json.loads(artifact.payload_json or "{}")
    payload["_canonical_key"] = artifact.canonical_key
    payload["_status"] = artifact.status
    return payload


def get_action(session, tenant_id: str, name: str) -> dict[str, Any] | None:
    import json

    canonical_key = canonical_key_for(ACTION_ARTIFACT_TYPE, name)
    artifact = session.query(OntologyArtifact).filter_by(project_id=tenant_id, canonical_key=canonical_key).first()
    if artifact is None:
        return None
    payload = json.loads(artifact.payload_json or "{}")
    payload["_canonical_key"] = artifact.canonical_key
    payload["_status"] = artifact.status
    return payload
