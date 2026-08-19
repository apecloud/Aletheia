"""Entity deduplication for graph-native (Nebula) tenants, reusing the
production identity-resolution building blocks already proven in
``agents/iterative_graph_enrichment_agent.py`` (``_node_identity_payload``,
``_identity_key``, ``SmallMultilingualEmbeddingAdapter``, ``_cosine_distance``,
and the same distance thresholds) against ``GraphIdentityIndex`` rows scoped
to a new ``source_space="nebula_vertex"`` value.

Two-tier matching (the third tier -- an LLM tie-break over the embedding
nearest-neighbors -- is not reused here; exact-key + embedding-threshold
matching is enough to prove cross-question entity merging works, and an LLM
tie-break can be layered on later the same way it was for the Postgres-only
pipeline, without changing this module's call signature):

1. Exact ``identity_key`` match (deterministic string: tenant + entity type +
   normalized label + source identity) -- same real-world entity mentioned
   under a different qid produces the identical key.
2. Sentence-embedding cosine-distance nearest-neighbor among this tenant's
   already-registered nodes, using the same ``VECTOR_DUPLICATE_DISTANCE``
   threshold as the Postgres-only pipeline.

If neither matches, a new vertex id is minted (caller supplies the minting
function, since ID *format* is a graph-native-importer concern, not this
module's) and registered into the index for future lookups.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

try:
    from ontology_artifacts import GraphIdentityIndex
except ModuleNotFoundError:
    from agents.ontology_artifacts import GraphIdentityIndex

try:
    from iterative_graph_enrichment_agent import (
        DEFAULT_DEDUP_EMBEDDING_MODEL,
        VECTOR_DUPLICATE_DISTANCE,
        SmallMultilingualEmbeddingAdapter,
        _cosine_distance,
        _identity_key,
        _node_identity_payload,
    )
except ModuleNotFoundError:
    from agents.iterative_graph_enrichment_agent import (
        DEFAULT_DEDUP_EMBEDDING_MODEL,
        VECTOR_DUPLICATE_DISTANCE,
        SmallMultilingualEmbeddingAdapter,
        _cosine_distance,
        _identity_key,
        _node_identity_payload,
    )

SOURCE_SPACE = "nebula_vertex"

# Separate namespace for LLM-generated entity descriptions (see
# llm_planner.EntityDescriptionResult's docstring) -- used only by
# query-time entity linking (agents/ontology_label_embeddings.py's
# find_nearest_labels), never by this module's own dedup matching above,
# which needs the stability of a bare type+label match.
SOURCE_SPACE_DESCRIPTION = "nebula_vertex_description"

_shared_embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None


def _get_embedding_adapter() -> SmallMultilingualEmbeddingAdapter:
    global _shared_embedding_adapter
    if _shared_embedding_adapter is None:
        _shared_embedding_adapter = SmallMultilingualEmbeddingAdapter()
    return _shared_embedding_adapter


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def resolve_or_mint_vertex_id(
    session,
    *,
    tenant_id: str,
    candidate_label: str,
    candidate_type: str,
    evidence_qid: str,
    mint_id: Callable[[], str],
    embedding_adapter: SmallMultilingualEmbeddingAdapter | None = None,
    is_literal: bool = False,
) -> tuple[str, str]:
    """Returns (vertex_id, match_method). match_method is one of
    "exact_identity_key", "vector_embedding", or "new_vertex".

    ``is_literal`` (True for a triple's object copied verbatim from source
    text, never for a subject or a given-title object -- see
    ``passage_relation_extraction.Triple.is_literal``) skips the embedding
    near-duplicate tier (below), using only exact identity-key matching.
    Fuzzy matching exists to merge spelling/capitalization variants of the
    SAME named entity ("Stephen Covey" vs "Stephen R. Covey") -- for a
    literal scalar value, textual closeness means the opposite: two
    genuinely different numbers/dates read as near-identical text and
    embed extremely close together (measured directly: "Quantity 7821 m"
    vs "Quantity 7823 m" -- two different real mountains' heights --
    embedded at cosine distance 0.034, far inside VECTOR_DUPLICATE_DISTANCE
    (0.12), incorrectly merging them into one vertex and corrupting any
    comparison that reads their height). A literal's exact text is either
    identical (safe to merge) or different (a different value -- must not
    merge), so exact-match alone is the correct and sufficient dedup rule.
    """
    identity = _node_identity_payload({
        "payload": {"label": candidate_label, "ontology_type": candidate_type, "properties": {}},
        "evidence_refs": [evidence_qid],
    })
    identity_key = _identity_key(tenant_id, identity)

    existing = (
        session.query(GraphIdentityIndex)
        .filter_by(project_id=tenant_id, source_space=SOURCE_SPACE, identity_key=identity_key)
        .first()
    )
    if existing is not None:
        return existing.source_key, "exact_identity_key"

    adapter = embedding_adapter or _get_embedding_adapter()
    dedup_text = f"{identity['entity_type']} {identity['label']}".strip()
    embed_result = adapter.embed(dedup_text)

    if not is_literal and embed_result.get("status") == "ready":
        vector = embed_result["vector"]
        best_row = None
        best_distance = None
        candidates = (
            session.query(GraphIdentityIndex)
            .filter_by(project_id=tenant_id, source_space=SOURCE_SPACE, element_kind="node")
            .all()
        )
        for row in candidates:
            if not row.embedding_json:
                continue
            other_vector = json.loads(row.embedding_json)
            distance = _cosine_distance(vector, other_vector)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_row = row
        if best_row is not None and best_distance is not None and best_distance <= VECTOR_DUPLICATE_DISTANCE:
            return best_row.source_key, "vector_embedding"

    vertex_id = mint_id()
    registered_id = _register_identity_or_get_existing(
        session,
        tenant_id=tenant_id,
        identity_key=identity_key,
        vertex_id=vertex_id,
        dedup_text=dedup_text,
        embed_result=embed_result,
    )
    if registered_id != vertex_id:
        return registered_id, "exact_identity_key"
    return vertex_id, "new_vertex"


def _register_identity_or_get_existing(
    session,
    *,
    tenant_id: str,
    identity_key: str,
    vertex_id: str,
    dedup_text: str,
    embed_result: dict[str, Any],
) -> str:
    """Insert a new identity row inside a SAVEPOINT, tolerating a race where
    another call already registered the exact same identity_key (or the
    same freshly-minted vertex_id, e.g. two mentions of the same literal
    value like "peach" within one question deterministically mint the same
    id) between this call's tier-1 exact-match check and now. On conflict,
    the earlier row wins -- return its source_key instead of raising."""
    vector = embed_result.get("vector")
    embedding_json = json.dumps(vector) if vector is not None else None
    vector_fingerprint = _fingerprint(embed_result.get("model") or "", dedup_text) if vector is not None else None
    row = GraphIdentityIndex(
        project_id=tenant_id,
        identity_key=identity_key,
        element_kind="node",
        candidate_id=f"nebula_vertex:{tenant_id}:{vertex_id}",
        source_space=SOURCE_SPACE,
        source_key=vertex_id,
        source_status="approved",
        dedup_text=dedup_text,
        embedding_model=embed_result.get("model") if vector is not None else None,
        embedding_dim=embed_result.get("dim") if vector is not None else None,
        embedding_json=embedding_json,
        vector_fingerprint=vector_fingerprint,
        payload_fingerprint=_fingerprint(identity_key, dedup_text),
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
        return vertex_id
    except IntegrityError:
        existing = (
            session.query(GraphIdentityIndex)
            .filter_by(project_id=tenant_id, source_space=SOURCE_SPACE, identity_key=identity_key)
            .first()
        )
        if existing is not None:
            return existing.source_key
        existing = (
            session.query(GraphIdentityIndex)
            .filter_by(project_id=tenant_id, source_space=SOURCE_SPACE, source_key=vertex_id, element_kind="node")
            .first()
        )
        if existing is not None:
            return existing.source_key
        raise
