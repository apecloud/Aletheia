"""Embedding-based entity linking for a tenant's approved ontology nodes.

Answers a different question than the identity-resolution pipeline this
reuses (``SmallMultilingualEmbeddingAdapter``/``_cosine_distance`` from
``iterative_graph_enrichment_agent.py``, already proven for label-vs-label
entity dedup in ``graph_entity_resolver.py``): given a FRESH QUESTION string
naming an entity somehow (paraphrase, abbreviation, different casing --
not necessarily a literal substring match), which of a tenant's already-
known nodes does it most likely refer to?

Used by ``InstanceRepository.graph_rag_query_context``
(``server/aletheia_server.py``) to replace a case-insensitive substring
scan (fragile against paraphrasing, capped at 50 candidates per type) with
a real nearest-neighbor lookup over cached label embeddings, stored in the
same ``GraphIdentityIndex`` table other tenants already use for entity
dedup -- under a distinct ``source_space`` so this never collides with
those rows.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from sqlalchemy.exc import IntegrityError

try:
    from ontology_artifacts import GraphIdentityIndex
except ModuleNotFoundError:
    from agents.ontology_artifacts import GraphIdentityIndex

try:
    from iterative_graph_enrichment_agent import SmallMultilingualEmbeddingAdapter, _cosine_distance
except ModuleNotFoundError:
    from agents.iterative_graph_enrichment_agent import SmallMultilingualEmbeddingAdapter, _cosine_distance

SOURCE_SPACE = "ontology_concrete_object_label"

# A question's embedding sits farther from a short label's embedding than
# two similar labels do to each other (surrounding question words dilute
# the similarity) -- calibrated against tests/test_continuous_enrichment_
# frontier.py's exact fixture, measured directly against the real
# embedding model: "What is connected to the Red Sea?" vs. label
# "Waterway Red Sea" = 0.249 (must match), a paraphrase ("Tell me about
# the connections of the Red Sea region") = 0.280 (must also match), vs.
# "Summarize the maritime graph." = 0.512-0.514 against either label (must
# NOT match anything). 0.40 sits in the middle of that gap with margin on
# both sides.
DEFAULT_MAX_DISTANCE = float(os.environ.get("ALETHEIA_ONTOLOGY_LABEL_MATCH_MAX_DISTANCE", "0.40"))

_shared_embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None


def _get_embedding_adapter() -> SmallMultilingualEmbeddingAdapter:
    global _shared_embedding_adapter
    if _shared_embedding_adapter is None:
        _shared_embedding_adapter = SmallMultilingualEmbeddingAdapter()
    return _shared_embedding_adapter


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _dedup_text(node: dict[str, Any]) -> str:
    description = str(node.get("description") or "").strip()
    if description:
        return description
    return f"{node.get('type', '')} {node.get('label', '')}".strip()


def label_embedding_count(session, tenant_id: str, *, source_space: str = SOURCE_SPACE) -> int:
    """Count of nodes currently indexed for this tenant -- used by the
    caller to decide whether a re-sync is needed (see module docstring)."""
    return (
        session.query(GraphIdentityIndex)
        .filter_by(project_id=tenant_id, source_space=source_space, element_kind="node")
        .count()
    )


def sync_label_embeddings(
    session, tenant_id: str, nodes: list[dict[str, Any]], *,
    embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None,
    source_space: str = SOURCE_SPACE,
) -> int:
    """Upsert one embedding row per node. Idempotent: re-running updates a
    node whose label/type/description changed rather than duplicating it.
    Returns the number of nodes synced (rows written or already up to date).

    Each node embeds ``node["description"]`` when present and non-empty
    (an LLM-generated summary of the entity, richer than its bare label --
    see ``llm_planner.EntityDescriptionResult``), else falls back to
    ``f"{type} {label}"`` as before.

    ``source_space`` defaults to this module's own namespace, but any
    namespace can be synced this way -- e.g. a separate description-only
    index kept apart from a tenant's dedup embeddings (see
    ``agents/graph_entity_resolver.py``'s ``SOURCE_SPACE_DESCRIPTION``)."""
    adapter = embedding_adapter or _get_embedding_adapter()
    synced = 0
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        dedup_text = _dedup_text(node)
        if not dedup_text:
            continue
        existing = (
            session.query(GraphIdentityIndex)
            .filter_by(project_id=tenant_id, source_space=source_space, source_key=node_id, element_kind="node")
            .first()
        )
        if existing is not None and existing.dedup_text == dedup_text:
            synced += 1
            continue

        embed_result = adapter.embed(dedup_text)
        vector = embed_result.get("vector")
        embedding_json = json.dumps(vector) if vector is not None else None
        vector_fingerprint = _fingerprint(embed_result.get("model") or "", dedup_text) if vector is not None else None
        payload_fingerprint = _fingerprint(tenant_id, node_id, dedup_text)

        if existing is not None:
            existing.dedup_text = dedup_text
            existing.embedding_model = embed_result.get("model") if vector is not None else None
            existing.embedding_dim = embed_result.get("dim") if vector is not None else None
            existing.embedding_json = embedding_json
            existing.vector_fingerprint = vector_fingerprint
            existing.payload_fingerprint = payload_fingerprint
            synced += 1
            continue

        row = GraphIdentityIndex(
            project_id=tenant_id,
            identity_key=f"{source_space}:{tenant_id}:{node_id}",
            element_kind="node",
            candidate_id=f"{source_space}:{tenant_id}:{node_id}",
            source_space=source_space,
            source_key=node_id,
            source_status="approved",
            dedup_text=dedup_text,
            embedding_model=embed_result.get("model") if vector is not None else None,
            embedding_dim=embed_result.get("dim") if vector is not None else None,
            embedding_json=embedding_json,
            vector_fingerprint=vector_fingerprint,
            payload_fingerprint=payload_fingerprint,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
            synced += 1
        except IntegrityError:
            # Another call already synced this exact node concurrently --
            # same SAVEPOINT-tolerant pattern as graph_entity_resolver.py.
            session.rollback()
            synced += 1
    session.commit()
    return synced


def find_nearest_labels(
    session, tenant_id: str, query_text: str, *,
    k: int = 5,
    embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    source_space: str = SOURCE_SPACE,
) -> list[dict[str, Any]]:
    """Embed ``query_text`` and return up to ``k`` nearest indexed labels
    within ``max_distance``, sorted closest-first: ``[{"node_id":...,
    "label":..., "distance":...}, ...]`` (empty if nothing qualifies).

    Returning several candidates (not just the argmin) lets a caller
    verify with an LLM which one -- if any -- is actually right, rather
    than trusting a single nearest-neighbor guess: the guessed mention name
    (from ``LLMPlanner.extract_question_entity_mentions``, say) doesn't
    always match the graph's exact label spelling, but the correct entity
    is often still somewhere in the top few candidates.

    ``source_space`` defaults to this module's own namespace, but any
    ``GraphIdentityIndex`` space populated with the same ``dedup_text =
    f"{type} {label}"`` convention works -- e.g. ``"nebula_vertex"``
    (``agents/graph_entity_resolver.py``'s dedup rows for graph-native
    tenants already carry exactly this shape, so a HotpotQA/2WikiMultihopQA
    tenant's existing dedup embeddings can be reused directly for question
    entity-linking, no separate index needed)."""
    query_text = str(query_text or "").strip()
    if not query_text:
        return []
    adapter = embedding_adapter or _get_embedding_adapter()
    embed_result = adapter.embed(query_text)
    if embed_result.get("status") != "ready":
        return []
    query_vector = embed_result["vector"]

    scored = []
    rows = (
        session.query(GraphIdentityIndex)
        .filter_by(project_id=tenant_id, source_space=source_space, element_kind="node")
        .all()
    )
    for row in rows:
        if not row.embedding_json:
            continue
        other_vector = json.loads(row.embedding_json)
        distance = _cosine_distance(query_vector, other_vector)
        if distance is None or distance > max_distance:
            continue
        scored.append({"node_id": row.source_key, "label": row.dedup_text, "distance": distance})

    scored.sort(key=lambda item: item["distance"])
    return scored[:k]


def find_nearest_label(
    session, tenant_id: str, query_text: str, *,
    embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None,
    max_distance: float = DEFAULT_MAX_DISTANCE,
    source_space: str = SOURCE_SPACE,
) -> str | None:
    """Single-best-match convenience wrapper over ``find_nearest_labels``
    (``k=1``) -- returns the node_id of the nearest indexed label within
    ``max_distance``, or None if nothing qualifies."""
    matches = find_nearest_labels(
        session, tenant_id, query_text, k=1,
        embedding_adapter=embedding_adapter, max_distance=max_distance, source_space=source_space,
    )
    return matches[0]["node_id"] if matches else None
