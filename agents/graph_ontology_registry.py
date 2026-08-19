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

try:
    from ontology_artifacts import OntologyArtifact, canonical_key_for, upsert_artifact
except ModuleNotFoundError:
    from agents.ontology_artifacts import OntologyArtifact, canonical_key_for, upsert_artifact

SOURCE_AGENT = "GraphNativeTypeRegistrar"
NODE_ARTIFACT_TYPE = "object"
EDGE_ARTIFACT_TYPE = "link"

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
    status: str = "draft",
) -> OntologyArtifact:
    """Register (or update) a node type for this tenant. Returns the
    OntologyArtifact row -- caller is responsible for session.commit()."""
    payload = {
        "name": name,
        "properties": _normalize_properties(properties),
        "subclass_of": list(subclass_of or []),
        "disjoint_with": list(disjoint_with or []),
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
        results.append(payload)
    return results


def get_approved_node_types(session, tenant_id: str) -> list[dict[str, Any]]:
    return _query_types(session, tenant_id=tenant_id, artifact_type=NODE_ARTIFACT_TYPE, status="approved")


def get_approved_edge_types(session, tenant_id: str) -> list[dict[str, Any]]:
    return _query_types(session, tenant_id=tenant_id, artifact_type=EDGE_ARTIFACT_TYPE, status="approved")


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
