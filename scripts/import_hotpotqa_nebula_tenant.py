#!/usr/bin/env python3
"""Persist a HotpotQA closed-world local graph directly into Nebula Graph.

Writes straight into the graph database via
``agents/graph_db_client.NebulaGraphClient``, so retrieval uses Nebula's own
query language (nGQL) instead of SQL -- the SQL-backed variant of this
importer (``import_hotpotqa_kg_tenant.py``) has been retired. Reuses the
extraction step (``passage_relation_extraction.PassageRelationExtractor``).

Strongly-typed multi-TAG/multi-EDGE-type model: each extracted entity is
assigned a real Nebula TAG matching its LLM-classified type (e.g. "Person",
"Location") and each relation becomes its own EDGE TYPE (instead of a single
flat ``HotpotEntity`` tag / ``RELATION`` edge type with everything folded
into string properties). New types are auto-registered into the tenant's
approved ontology (``agents/graph_ontology_registry.py``) the first time
they're seen -- same "no manual review gate" precedent already used by
``RelationCatalog.normalize()`` for relation names, since this is a scripted
batch import with no human in the loop. This importer still owns that
auto-approve policy; the extractor itself only reads back the tenant's
already-approved node types (fetched once, before extraction starts) and
passes them into each prompt so independent, stateless per-question calls
stay consistent (reusing "Person" rather than drifting to "Human" on a
later question) -- it doesn't reject/gate types outside that list.

Entities are deduplicated across questions via
``agents/graph_entity_resolver.py`` (exact-identity-key then
embedding-nearest-neighbor matching against ``GraphIdentityIndex``) -- the
same real-world entity mentioned in two different HotpotQA questions
resolves to the same vertex instead of minting a fresh one per question,
moving this tenant from a closed-world (one isolated subgraph per question)
to a shared, deduplicated graph.

Uses a dedicated Nebula space (default ``hotpotqa_kg``), separate from the
shared ``"aletheia"`` space the Northwind demo (`query_graph.py`,
`agents/graph_ingestion_agent.py`) uses.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "agents"))
sys.path.append(str(ROOT / "scripts"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from graph_db_client import NebulaGraphClient  # noqa: E402
from graph_entity_resolver import SOURCE_SPACE_DESCRIPTION, resolve_or_mint_vertex_id  # noqa: E402
from graph_ontology_registry import (  # noqa: E402
    get_approved_node_types, get_edge_type, get_node_type, propose_edge_type, propose_node_type,
)
from graph_schema_sync import sync_tenant_schema  # noqa: E402
from hotpotqa_entity_ids import entity_id  # noqa: E402
from hotpotqa_frozen_sample import DEFAULT_SAMPLE_PATH, load_hotpotqa_cases  # noqa: E402
from iterative_graph_enrichment_agent import SmallMultilingualEmbeddingAdapter, _cosine_distance  # noqa: E402
from llm_planner import LLMPlanner  # noqa: E402
from passage_relation_extraction import PassageRelationExtractor  # noqa: E402
from ontology_artifacts import ensure_artifact_schema  # noqa: E402
from ontology_label_embeddings import sync_label_embeddings  # noqa: E402
from relation_catalog import RelationCatalog  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402

DEFAULT_SPACE = "hotpotqa_kg"
DEFAULT_CASES = ROOT / "benchmarks" / "hotpotqa" / "hotpot_nebula_cases.json"
DEFAULT_REPORT = ROOT / "reports" / "hotpotqa-nebula-import.json"
EVIDENCE_MAX_LEN = 300
DEFAULT_ENTITY_TYPE = "Entity"
# Type assigned to a literal triple-object with no natural entity type at
# all (a date, a count, a bare descriptive/categorical phrase -- see
# passage_relation_extraction.py rule 9's own definition of when to_type is
# left empty). These are plain property values, not independently
# resolvable real-world entities -- see _build_vertex_descriptions and
# materialize_hotpotqa_questions's nodes_with_description filtering below.
GENERIC_LITERAL_TYPE = "Value"
# Caps the prompt size for LLMPlanner.summarize_entity_description --
# an entity mentioned across dozens of questions only needs a handful of
# representative evidence sentences, not an exhaustive list.
MAX_EVIDENCE_PER_VERTEX = 8
# Defense-in-depth for GENERIC_LITERAL_TYPE's own gap: rule 9 asks the
# extraction LLM to leave to_type empty for a categorical/descriptive
# literal (e.g. "Maine's oldest maritime museum" describing what
# "Penobscot Marine Museum" IS), but compliance isn't 100% -- some import
# runs instead give it a plausible-sounding non-empty type (seen in
# practice: "Description"), which evades the GENERIC_LITERAL_TYPE filter
# entirely since that filter only catches a literal to_type string
# match. This is a STRUCTURAL check instead: any such literal's evidence
# sentence is written from its own subject's point of view (add_evidence
# attaches the same sentence to both triple endpoints), so a spurious
# categorical-phrase vertex ends up with a description nearly identical to
# its subject's -- directly measured on the real embedding model: the
# Penobscot Marine Museum/"Maine's oldest maritime museum" pair sits at
# 0.023 cosine distance, Finding Kraftland's earlier near-duplicate case at
# 0.088, while a real distinct child (e.g. Berenberg Bank's own 1590
# founding-year fact vs Berenberg Bank itself) sits at 0.209 and an
# unrelated pair at 0.541 -- 0.15 cleanly separates near-duplicate noise
# from genuine distinct facts with margin on both sides.
LITERAL_NEAR_DUPLICATE_MAX_DISTANCE = 0.15


def _register_node_type_if_new(session, tenant_id: str, name: str, seen: set[str]) -> None:
    if name in seen:
        return
    seen.add(name)
    if get_node_type(session, tenant_id, name) is not None:
        return
    propose_node_type(
        session, tenant_id=tenant_id, name=name,
        description=f"Auto-registered from HotpotQA extraction (type: {name}).",
        properties=[{"name": "label", "data_type": "string"}],
        confidence=0.7, evidence=["hotpotqa_extraction"], status="approved",
    )


def _register_or_extend_edge_type(session, tenant_id: str, name: str, domain_type: str, range_type: str) -> None:
    existing = get_edge_type(session, tenant_id, name)
    domain = sorted(set((existing or {}).get("domain") or []) | {domain_type})
    range_ = sorted(set((existing or {}).get("range") or []) | {range_type})
    propose_edge_type(
        session, tenant_id=tenant_id, name=name, domain=domain, range=range_,
        description=f"Auto-registered from HotpotQA extraction (relation: {name}).",
        properties=[{"name": "evidence", "data_type": "string"}],
        confidence=0.7, evidence=["hotpotqa_extraction"], status="approved",
    )


def _extract_all_graphs(
    cases: list[dict[str, Any]], extractor: PassageRelationExtractor, *,
    concurrency: int, approved_node_types: list[str] | None = None,
) -> dict[str, Any]:
    """Run extraction for every case, optionally in parallel.

    Each ``extract_local_graph`` call is I/O-bound (waiting on the LLM
    provider) and independent per question -- this is the dominant cost of
    a full import (observed: ~10-15s/question serially). Deliberately kept
    as its own pass, separate from dedup/type-registration/row-building
    (still single-threaded, in ``materialize_hotpotqa_questions``'s own
    for-loop over ``cases`` in original order) -- no concurrency-sensitive
    Postgres write path needs to change to get this speedup, and
    dedup/materialization order stays fully deterministic regardless of
    which order the LLM calls actually complete in.

    ``approved_node_types`` is a single snapshot taken before extraction
    starts (see ``materialize_hotpotqa_questions``) -- types discovered
    mid-run aren't fed back into still-pending calls, since parallel
    extraction means there's no well-defined "so far" partway through one
    batch; a second import of the same tenant sees this run's types."""
    graphs_by_qid: dict[str, Any] = {}
    total = len(cases)
    if concurrency <= 1:
        for idx, case in enumerate(cases, start=1):
            start = time.monotonic()
            graph = extractor.extract_local_graph(case["question"], case["context"], approved_node_types)
            elapsed = time.monotonic() - start
            status = f"error={graph.error[:60]!r}" if (graph.error and graph.used_fallback) else f"triples={len(graph.triples)}"
            print(f"[extract] {idx}/{total} qid={case['qid']} {elapsed:.1f}s {status}", flush=True)
            graphs_by_qid[case["qid"]] = graph
        return graphs_by_qid

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        future_to_case = {
            pool.submit(extractor.extract_local_graph, case["question"], case["context"], approved_node_types): case
            for case in cases
        }
        completed = 0
        for future in as_completed(future_to_case):
            case = future_to_case[future]
            graph = future.result()
            completed += 1
            status = f"error={graph.error[:60]!r}" if (graph.error and graph.used_fallback) else f"triples={len(graph.triples)}"
            print(f"[extract] {completed}/{total} qid={case['qid']} {status}", flush=True)
            graphs_by_qid[case["qid"]] = graph
    return graphs_by_qid


def _build_vertex_descriptions(
    vertex_rows_by_type: dict[str, dict[str, dict[str, Any]]],
    vertex_evidence: dict[str, list[str]],
    planner: LLMPlanner,
    *, concurrency: int,
) -> dict[str, str]:
    """One LLM call per vertex that accumulated evidence during extraction
    (see ``materialize_hotpotqa_questions``'s triple loop) -- borrowed from
    GraphRAG's own construction pipeline, which embeds an LLM-summarized
    description of an entity rather than just its bare label (see
    ``llm_planner.EntityDescriptionResult``'s docstring for why). Skips the
    LLM call entirely for a vertex with no accumulated evidence (e.g. a
    topic_title/entity_mentions promotion that never appeared as a triple
    endpoint) -- callers fall back to the bare label for those. Also skips
    ``GENERIC_LITERAL_TYPE`` vertices entirely: a triple's evidence sentence
    (added to both endpoints, see the triple loop below) is written from the
    SUBJECT's point of view, so summarizing it for a bare literal object
    produces a description that's often near-identical to its own subject's
    -- e.g. both "Finding Kraftland" and its own literal object "2006
    independent documentary" summarized to "Finding Kraftland is a 2006
    independent documentary produced by...", making them indistinguishable
    (even tied) to live entity-linking's embedding search. These vertices
    aren't independently resolvable real-world entities in the first place
    (that's precisely why to_type fell back to Value -- see rule 9 in
    passage_relation_extraction.py), so they should never compete with real
    entities for a question's center-node resolution. Returns
    ``{vertex_id: description}``, omitting vertices whose call failed."""
    jobs = [
        (vid, row["label"], entity_type, vertex_evidence[vid])
        for entity_type, rows in vertex_rows_by_type.items()
        for vid, row in rows.items()
        if vertex_evidence.get(vid) and entity_type != GENERIC_LITERAL_TYPE
    ]
    if not jobs:
        return {}

    def _run(job: tuple[str, str, str, list[str]]) -> tuple[str, str]:
        vid, label, entity_type, evidence = job
        return vid, planner.summarize_entity_description(label, entity_type, evidence).description

    descriptions: dict[str, str] = {}
    if concurrency <= 1:
        for job in jobs:
            vid, description = _run(job)
            if description:
                descriptions[vid] = description
        return descriptions

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for future in as_completed(pool.submit(_run, job) for job in jobs):
            vid, description = future.result()
            if description:
                descriptions[vid] = description
    return descriptions


def _filter_literal_near_duplicates(
    vids: list[str],
    descriptions: dict[str, str],
    literal_parent: dict[str, str],
    embedding_adapter: SmallMultilingualEmbeddingAdapter,
) -> set[str]:
    """Structural defense-in-depth for the gap GENERIC_LITERAL_TYPE's own
    filter leaves open (see LITERAL_NEAR_DUPLICATE_MAX_DISTANCE's comment):
    a literal triple-object the extraction LLM typed as something other
    than the empty/Value fallback, but whose own description is a
    near-duplicate of its subject's -- the actual observed shape of the
    Penobscot Marine Museum bug. Returns the subset of ``vids`` to DROP
    from entity-linking indexing. Cheap: only embeds descriptions for
    vertices that are actually a literal_parent's child AND have their own
    description (the common case -- most vertices skip this entirely)."""
    embedding_cache: dict[str, list[float]] = {}

    def _embed(text: str) -> list[float] | None:
        if text not in embedding_cache:
            result = embedding_adapter.embed(text)
            embedding_cache[text] = result.get("vector") if result.get("status") == "ready" else None
        return embedding_cache[text]

    drop: set[str] = set()
    for vid in vids:
        parent_id = literal_parent.get(vid)
        own_description = descriptions.get(vid)
        parent_description = descriptions.get(parent_id) if parent_id else None
        if not parent_id or not own_description or not parent_description:
            continue
        own_vector = _embed(own_description)
        parent_vector = _embed(parent_description)
        if own_vector is None or parent_vector is None:
            continue
        if _cosine_distance(own_vector, parent_vector) < LITERAL_NEAR_DUPLICATE_MAX_DISTANCE:
            drop.add(vid)
    return drop


def materialize_hotpotqa_questions(
    session,
    cases: list[dict[str, Any]],
    extractor: PassageRelationExtractor,
    relation_catalog: RelationCatalog | None = None,
    *,
    tenant_id: str,
    extraction_concurrency: int = 1,
    build_description_embeddings: bool = True,
    llm_planner: LLMPlanner | None = None,
    include_mentioned_entities: bool = True,
) -> dict[str, Any]:
    """Extract each question's local graph, resolve/dedup entity identities
    across questions, and shape the result into per-type Nebula vertex/edge
    rows plus per-tenant ontology type registrations."""
    vertex_rows_by_type: dict[str, dict[str, dict[str, Any]]] = {}
    edge_rows_by_type: dict[str, list[dict[str, Any]]] = {}
    # Evidence sentences accumulated per vertex across every triple it
    # appears in (from_id or to_id), capped per vertex -- feeds the
    # description-embedding post-pass below.
    vertex_evidence: dict[str, list[str]] = {}
    # First-seen from_id for every is_literal triple's to_id -- feeds
    # LITERAL_NEAR_DUPLICATE_MAX_DISTANCE's near-duplicate-description
    # check below (only meaningful for literals, so is_literal=false
    # to_ids are never added here).
    literal_parent: dict[str, str] = {}
    seen_edges: set[tuple[str, str, str]] = set()
    seen_node_types: set[str] = set()
    output_cases: list[dict[str, Any]] = []
    extraction_errors = 0

    def ensure_vertex(qid: str, title_or_value: str, entity_type: str, is_literal: bool = False) -> str:
        _register_node_type_if_new(session, tenant_id, entity_type, seen_node_types)
        vid, _method = resolve_or_mint_vertex_id(
            session,
            tenant_id=tenant_id,
            candidate_label=title_or_value,
            candidate_type=entity_type,
            evidence_qid=qid,
            mint_id=lambda: entity_id(qid, title_or_value),
            is_literal=is_literal,
        )
        vertex_rows_by_type.setdefault(entity_type, {}).setdefault(vid, {"id": vid, "label": str(title_or_value)})
        return vid

    def add_evidence(vid: str, evidence: str) -> None:
        if not evidence:
            return
        bucket = vertex_evidence.setdefault(vid, [])
        if evidence not in bucket and len(bucket) < MAX_EVIDENCE_PER_VERTEX:
            bucket.append(evidence)

    # Snapshot of this tenant's already-approved node types, fed into every
    # extraction call so independent, stateless per-question LLM calls stay
    # consistent (reuse "Person" instead of drifting to "Human" later) --
    # empty on a tenant's first import, populated on subsequent ones.
    approved_node_types = [t["name"] for t in get_approved_node_types(session, tenant_id)]
    graphs_by_qid = _extract_all_graphs(
        cases, extractor, concurrency=extraction_concurrency, approved_node_types=approved_node_types,
    )

    for case in cases:
        qid = case["qid"]
        graph = graphs_by_qid[qid]
        if graph.error and graph.used_fallback:
            extraction_errors += 1

        title_types: dict[str, str] = {}
        for triple in graph.triples:
            from_id = ensure_vertex(qid, triple.from_title, triple.from_type or DEFAULT_ENTITY_TYPE)
            to_type = triple.to_type or (GENERIC_LITERAL_TYPE if triple.is_literal else DEFAULT_ENTITY_TYPE)
            to_id = ensure_vertex(qid, triple.to_value, to_type, is_literal=triple.is_literal)
            title_types[triple.from_title] = triple.from_type or DEFAULT_ENTITY_TYPE
            if not triple.is_literal:
                title_types[triple.to_value] = to_type
            else:
                literal_parent.setdefault(to_id, from_id)
            add_evidence(from_id, triple.evidence)
            add_evidence(to_id, triple.evidence)

            relation_name = (
                relation_catalog.normalize(triple.relation, evidence=triple.evidence)
                if relation_catalog is not None else triple.relation
            )
            _register_or_extend_edge_type(session, tenant_id, relation_name, triple.from_type or DEFAULT_ENTITY_TYPE, to_type)

            edge_key = (from_id, to_id, relation_name)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edge_rows_by_type.setdefault(relation_name, []).append({
                    "source_id": from_id,
                    "target_id": to_id,
                    "evidence": triple.evidence[:EVIDENCE_MAX_LEN],
                })

        # Entities mentioned anywhere in the passages get a vertex + their
        # own LLM-grounded description regardless of whether they ended up
        # as a triple's subject/object above (see
        # passage_relation_extraction.MentionedEntity's docstring -- fixes
        # e.g. a composer named only in passing, never the object of a
        # captured "composed_by"-style relation, having no vertex at all).
        # is_literal defaults to False: these are named entities (fuzzy-
        # dedup-eligible, like topic titles), not the scalar-literal
        # is_literal=True carve-out from the numeric-literal dedup fix.
        # include_mentioned_entities=False reproduces the pre-mentioned_
        # entities checkpoint tenant config exactly (measured 83% effective
        # hit rate vs. this feature's net-neutral-to-negative showing on
        # the same 100-question benchmark, likely due to the resulting
        # ~2x vertex-count growth diluting fixed-K candidate retrieval --
        # see run_hotpotqa_nebula_via_analyze_benchmark.py's
        # _CANDIDATE_RETRIEVAL_K comment).
        if include_mentioned_entities:
            for entity in graph.mentioned_entities:
                vid = ensure_vertex(qid, entity.name, entity.entity_type or DEFAULT_ENTITY_TYPE)
                add_evidence(vid, entity.description)

        topic_title = graph.topic_title or (case["context"][0][0] if case["context"] else "")
        topic_type = title_types.get(topic_title, DEFAULT_ENTITY_TYPE)
        topic_id = ensure_vertex(qid, topic_title, topic_type) if topic_title else ""

        # Not gated on case["type"] -- HotpotQA's own bridge/comparison label
        # doesn't reliably reflect question content (a real "type": "bridge"
        # question phrased as "if A was less dangerous than B, which
        # occurred first?" still names a second specific incident relevant
        # to topic_title). graph.entity_mentions is the extraction LLM's own
        # judgment (from the same call that produced topic_title) of which
        # OTHER given titles the question specifically names -- not a
        # keyword/type-label heuristic, so it doesn't over-trigger on titles
        # that are merely topically similar or near-duplicate franchise
        # variants the way a text-matching heuristic previously did.
        #
        # Named additional_center_nodes (not "compare_..."), matching
        # ReasoningEngine.analyze()'s own additional_center_nodes parameter --
        # the reasoning engine doesn't know or care whether these entities
        # were named for a comparison, a relationship check, or a bridge
        # chain; it's for the caller (ReasoningEngine itself) to try path-
        # finding first and only fall back to facts-only derivation when a
        # genuine relate/compare answer is actually needed.
        additional_center_nodes = sorted({
            ensure_vertex(qid, title, title_types.get(title, DEFAULT_ENTITY_TYPE))
            for title in graph.entity_mentions
        })

        output_cases.append({
            "qid": qid,
            "question": case["question"],
            "center_node": topic_id,
            "additional_center_nodes": additional_center_nodes,
            "answer": case["answer"],
            "type": case.get("type", ""),
            "level": case.get("level", ""),
            "triple_count": len(graph.triples),
            "extraction_error": graph.error if graph.used_fallback else "",
        })

    session.commit()

    description_count = 0
    if build_description_embeddings:
        planner = llm_planner or LLMPlanner()
        descriptions = _build_vertex_descriptions(
            vertex_rows_by_type, vertex_evidence, planner, concurrency=max(4, extraction_concurrency),
        )
        # Non-Value literals whose own description is a near-duplicate of
        # their subject's -- see LITERAL_NEAR_DUPLICATE_MAX_DISTANCE.
        near_duplicate_vids = _filter_literal_near_duplicates(
            list(descriptions.keys()), descriptions, literal_parent, SmallMultilingualEmbeddingAdapter(),
        )
        # GENERIC_LITERAL_TYPE vertices are excluded from the entity-linking
        # embedding index entirely (not just from LLM description
        # summarization above) -- a question must never resolve to a bare
        # literal property value as its center, only to a real entity.
        nodes_with_description = [
            {"id": vid, "type": entity_type, "label": row["label"], "description": descriptions.get(vid, "")}
            for entity_type, rows in vertex_rows_by_type.items()
            for vid, row in rows.items()
            if entity_type != GENERIC_LITERAL_TYPE and vid not in near_duplicate_vids
        ]
        description_count = sync_label_embeddings(
            session, tenant_id, nodes_with_description, source_space=SOURCE_SPACE_DESCRIPTION,
        )

    return {
        "vertex_rows_by_type": {t: list(rows.values()) for t, rows in vertex_rows_by_type.items()},
        "edge_rows_by_type": edge_rows_by_type,
        "cases": output_cases,
        "extraction_errors": extraction_errors,
        "question_count": len(cases),
        "description_embedding_count": description_count,
    }


def _insert_with_schema_retry(insert_call, *, retries: int = 3, retry_sleep: float = 5.0) -> None:
    """Retry an insert_vertices/insert_edges call if graphd's schema cache
    hasn't caught up yet ("No schema found" right after CREATE TAG/EDGE)."""
    for attempt in range(retries + 1):
        try:
            insert_call()
            return
        except Exception as exc:
            if "No schema found" not in str(exc) or attempt == retries:
                raise
            time.sleep(retry_sleep)


def import_hotpotqa_nebula_tenant(
    *,
    input_path: Path,
    max_questions: int | None,
    space: str,
    nebula_ip: str,
    nebula_port: int,
    nebula_user: str,
    nebula_password: str,
    cases_json: Path,
    relation_catalog_db_url: str | None = None,
    relation_catalog_scope: str = "hotpotqa",
    tenant_id: str | None = None,
    disable_relation_catalog: bool = False,
    extraction_concurrency: int = 1,
    build_description_embeddings: bool = True,
    max_gleanings: int = 1,
    include_mentioned_entities: bool = True,
) -> dict[str, Any]:
    cases = load_hotpotqa_cases(input_path)
    if max_questions:
        cases = cases[:max_questions]
    if not cases:
        raise ValueError(f"No HotpotQA cases found at {input_path}")

    tenant_id = tenant_id or relation_catalog_scope
    metadata_db_url = relation_catalog_db_url or default_metadata_db_url()
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    session = sessionmaker(bind=engine)()

    # Relation *name* governance (dedup LLM-invented wording variants to one
    # canonical string) stays a separate, narrower job from the typed
    # node/edge registry above -- see relation_catalog.py.
    relation_catalog = None if disable_relation_catalog else RelationCatalog.load_from_postgres(
        metadata_db_url, scope=relation_catalog_scope,
    )

    materialized = materialize_hotpotqa_questions(
        session, cases,
        PassageRelationExtractor(max_workers=max(4, extraction_concurrency), max_gleanings=max_gleanings),
        relation_catalog, tenant_id=tenant_id, extraction_concurrency=extraction_concurrency,
        build_description_embeddings=build_description_embeddings,
        include_mentioned_entities=include_mentioned_entities,
    )

    if relation_catalog is not None:
        relation_catalog.save()

    client = NebulaGraphClient(ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_password, space=space)
    client.connect()
    try:
        client.execute_query(
            f"CREATE SPACE IF NOT EXISTS {space} (partition_num=1, replica_factor=1, vid_type=FIXED_STRING(128));"
        )
        sync_tenant_schema(session, client, tenant_id)
        for node_type, rows in materialized["vertex_rows_by_type"].items():
            _insert_with_schema_retry(lambda t=node_type, r=rows: client.insert_vertices(t, r))
        for edge_type, rows in materialized["edge_rows_by_type"].items():
            _insert_with_schema_retry(lambda t=edge_type, r=rows: client.insert_edges(t, r))
    finally:
        client.close()

    cases_json.parent.mkdir(parents=True, exist_ok=True)
    cases_json.write_text(json.dumps(materialized["cases"], ensure_ascii=False, indent=2), encoding="utf-8")

    vertex_count = sum(len(rows) for rows in materialized["vertex_rows_by_type"].values())
    edge_count = sum(len(rows) for rows in materialized["edge_rows_by_type"].values())
    return {
        "space": space,
        "tenant_id": tenant_id,
        "input_path": str(input_path),
        "cases_json": str(cases_json),
        "question_count": materialized["question_count"],
        "extraction_errors": materialized["extraction_errors"],
        "vertex_count": vertex_count,
        "edge_count": edge_count,
        "node_type_count": len(materialized["vertex_rows_by_type"]),
        "edge_type_count": len(materialized["edge_rows_by_type"]),
        "relation_catalog_size": len(relation_catalog.entries) if relation_catalog is not None else None,
        "description_embedding_count": materialized["description_embedding_count"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import a HotpotQA closed-world local graph directly into Nebula Graph")
    parser.add_argument("--input", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-password", default="nebula")
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--relation-catalog-db-url", default=default_metadata_db_url(),
        help="Postgres URL for the governed relation catalog and typed ontology registry (onto's metadata store)",
    )
    parser.add_argument("--relation-catalog-scope", default="hotpotqa")
    parser.add_argument("--tenant-id", default=None, help="Ontology registry scope (defaults to --relation-catalog-scope)")
    parser.add_argument(
        "--no-relation-catalog", action="store_true",
        help="Disable relation-name governance -- write the extractor's raw relation names as-is (offline/testing only)",
    )
    parser.add_argument(
        "--extraction-concurrency", type=int, default=1,
        help=(
            "Run this many extract_local_graph() calls concurrently (each is I/O-bound, waiting "
            "on the LLM provider, and independent per question) -- dedup/type-registration/row-"
            "building stay single-threaded regardless. 1 (default) preserves the old serial "
            "behavior and per-question timing log."
        ),
    )
    parser.add_argument(
        "--skip-description-embeddings", action="store_true",
        help=(
            "Skip the post-import LLM description-summarization pass (one call per unique "
            "vertex with accumulated evidence) that powers query-time entity linking -- use "
            "for fast iteration/testing when that extra LLM cost isn't needed."
        ),
    )
    parser.add_argument(
        "--max-gleanings", type=int, default=1,
        help=(
            "Extra 'what did you miss' follow-up turns per question after the main extraction "
            "succeeds (see passage_relation_extraction.GLEANING_CONTINUE_PROMPT) -- borrowed "
            "from GraphRAG's own extraction pipeline. 1 (default) adds one follow-up call per "
            "question; 0 disables gleaning entirely (matches pre-gleaning behavior)."
        ),
    )
    parser.add_argument(
        "--skip-mentioned-entities", action="store_true",
        help=(
            "Skip minting a vertex for each passage_relation_extraction.MentionedEntity "
            "(entities named only in passing, never a triple's subject/object -- see rule 12 "
            "in DEFAULT_SYSTEM_PROMPT). Measured net-neutral-to-negative on the 100-question "
            "HotpotQA benchmark (roughly doubles vertex count, diluting fixed-K query-time "
            "retrieval) despite fixing individual cases -- pass this flag to reproduce the "
            "higher-scoring pre-mentioned_entities checkpoint config."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = import_hotpotqa_nebula_tenant(
        input_path=args.input,
        max_questions=args.max_questions,
        space=args.space,
        nebula_ip=args.nebula_ip,
        nebula_port=args.nebula_port,
        nebula_user=args.nebula_user,
        nebula_password=args.nebula_password,
        cases_json=args.cases_json,
        relation_catalog_db_url=args.relation_catalog_db_url,
        relation_catalog_scope=args.relation_catalog_scope,
        tenant_id=args.tenant_id,
        disable_relation_catalog=args.no_relation_catalog,
        extraction_concurrency=args.extraction_concurrency,
        build_description_embeddings=not args.skip_description_embeddings,
        max_gleanings=args.max_gleanings,
        include_mentioned_entities=not args.skip_mentioned_entities,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"space={report['space']} tenant_id={report['tenant_id']}")
    print(f"questions={report['question_count']} extraction_errors={report['extraction_errors']}")
    print(f"vertex_count={report['vertex_count']} ({report['node_type_count']} node types)")
    print(f"edge_count={report['edge_count']} ({report['edge_type_count']} edge types)")
    print(f"relation_catalog_size={report['relation_catalog_size']}")
    print(f"description_embedding_count={report['description_embedding_count']}")
    print(f"cases_json={report['cases_json']}")
    print(f"report_json={args.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
