#!/usr/bin/env python3
"""Persist a HotpotQA closed-world local graph directly into Nebula Graph.

Writes straight into the graph database via
``agents/graph_db_client.NebulaGraphClient``, so retrieval uses Nebula's own
query language (nGQL) instead of SQL -- the SQL-backed variant of this
importer (``import_hotpotqa_kg_tenant.py``) has been retired. Reuses the
extraction step (``hotpotqa_kg_extraction.HotpotQARelationExtractor``).

Unlike the SQL variant (one join table per relation label -- 299 tables for
100 questions), this uses a single TAG (``HotpotEntity``) and a single EDGE
type (``RELATION``, with a ``relation_label`` property) for every relation
the LLM invents -- Nebula's property-on-edge model doesn't need a new type
per relation the way the SQL join-table model needed a new table per
relation.

Uses a dedicated Nebula space (default ``hotpotqa_kg``), separate from the
shared ``"aletheia"`` space the Northwind demo (`query_graph.py`,
`agents/graph_ingestion_agent.py`) uses.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "agents"))
sys.path.append(str(ROOT / "scripts"))

from graph_db_client import NebulaGraphClient  # noqa: E402
from hotpotqa_entity_ids import entity_id  # noqa: E402
from hotpotqa_frozen_sample import DEFAULT_SAMPLE_PATH, load_hotpotqa_cases  # noqa: E402
from hotpotqa_kg_extraction import HotpotQARelationExtractor  # noqa: E402
from relation_catalog import RelationCatalog  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402

DEFAULT_SPACE = "hotpotqa_kg"
DEFAULT_CASES = ROOT / "benchmarks" / "hotpotqa" / "hotpot_nebula_cases.json"
DEFAULT_REPORT = ROOT / "reports" / "hotpotqa-nebula-import.json"
TAG_NAME = "HotpotEntity"
EDGE_TYPE = "RELATION"
EVIDENCE_MAX_LEN = 300


def materialize_hotpotqa_questions(
    cases: list[dict[str, Any]],
    extractor: HotpotQARelationExtractor,
    relation_catalog: RelationCatalog | None = None,
) -> dict[str, Any]:
    """Extract each question's local graph and shape it into Nebula rows.

    Entity ids are qid-scoped (same ``entity_id()`` helper the SQL variant
    uses) so the ~100 per-question subgraphs stay disjoint inside the one
    shared Nebula space even though they may reuse relation labels.

    ``relation_catalog``, when given, normalizes every triple's relation
    name through the governed catalog before it becomes a ``relation_label``
    -- the single EDGE type's storage shape doesn't change, only which
    string ends up in that property (the extractor's ad hoc name, or the
    catalog's existing canonical name for the same real-world relation).
    Governance, not a schema change: see relation_catalog.py.
    """
    vertex_rows: dict[str, dict[str, Any]] = {}
    edge_rows: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    output_cases: list[dict[str, Any]] = []
    extraction_errors = 0

    def ensure_vertex(qid: str, title_or_value: str) -> str:
        vid = entity_id(qid, title_or_value)
        vertex_rows.setdefault(vid, {"id": vid, "label": str(title_or_value)})
        return vid

    for idx, case in enumerate(cases, start=1):
        qid = case["qid"]
        start = time.monotonic()
        graph = extractor.extract_local_graph(case["question"], case["context"])
        elapsed = time.monotonic() - start
        status = f"error={graph.error[:60]!r}" if (graph.error and graph.used_fallback) else f"triples={len(graph.triples)}"
        print(f"[extract] {idx}/{len(cases)} qid={qid} {elapsed:.1f}s {status}", flush=True)
        if graph.error and graph.used_fallback:
            extraction_errors += 1

        for triple in graph.triples:
            from_id = ensure_vertex(qid, triple.from_title)
            to_id = ensure_vertex(qid, triple.to_value)
            relation_label = (
                relation_catalog.normalize(triple.relation, evidence=triple.evidence)
                if relation_catalog is not None else triple.relation
            )
            edge_key = (from_id, to_id, relation_label)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edge_rows.append({
                    "source_id": from_id,
                    "target_id": to_id,
                    "relation_label": relation_label,
                    "evidence": triple.evidence[:EVIDENCE_MAX_LEN],
                })

        topic_title = graph.topic_title or (case["context"][0][0] if case["context"] else "")
        topic_id = ensure_vertex(qid, topic_title) if topic_title else ""

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
        additional_center_nodes = sorted({ensure_vertex(qid, title) for title in graph.entity_mentions})

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

    return {
        "vertex_rows": list(vertex_rows.values()),
        "edge_rows": edge_rows,
        "cases": output_cases,
        "extraction_errors": extraction_errors,
        "question_count": len(cases),
    }


def ensure_schema(client: NebulaGraphClient) -> None:
    client.execute_query(f"CREATE TAG IF NOT EXISTS {TAG_NAME}(label string);")
    client.execute_query(f"CREATE EDGE IF NOT EXISTS {EDGE_TYPE}(relation_label string, evidence string);")
    # metad sees the new schema immediately, but graphd/storaged cache
    # schema on their own heartbeat cycle (default 10s) -- INSERT
    # VERTEX/EDGE can fail with "No schema found" until that cache
    # refreshes. Wait past one heartbeat; _insert_with_schema_retry below
    # covers the rest if the cache is still stale.
    time.sleep(11)


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
) -> dict[str, Any]:
    cases = load_hotpotqa_cases(input_path)
    if max_questions:
        cases = cases[:max_questions]
    if not cases:
        raise ValueError(f"No HotpotQA cases found at {input_path}")

    # Relation governance lives in Postgres (onto's metadata store), not a
    # standalone file -- it's metadata ABOUT the graph's relations, not
    # graph data, so it belongs with onto's other ontology governance.
    relation_catalog = RelationCatalog.load_from_postgres(
        relation_catalog_db_url, scope=relation_catalog_scope,
    ) if relation_catalog_db_url else None

    materialized = materialize_hotpotqa_questions(cases, HotpotQARelationExtractor(), relation_catalog)

    if relation_catalog is not None:
        relation_catalog.save()

    client = NebulaGraphClient(ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_password, space=space)
    client.connect()
    try:
        ensure_schema(client)
        _insert_with_schema_retry(lambda: client.insert_vertices(TAG_NAME, materialized["vertex_rows"]))
        _insert_with_schema_retry(lambda: client.insert_edges(EDGE_TYPE, materialized["edge_rows"]))
    finally:
        client.close()

    cases_json.parent.mkdir(parents=True, exist_ok=True)
    cases_json.write_text(json.dumps(materialized["cases"], ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "space": space,
        "input_path": str(input_path),
        "cases_json": str(cases_json),
        "question_count": materialized["question_count"],
        "extraction_errors": materialized["extraction_errors"],
        "vertex_count": len(materialized["vertex_rows"]),
        "edge_count": len(materialized["edge_rows"]),
        "relation_catalog_size": len(relation_catalog.entries) if relation_catalog is not None else None,
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
        help="Postgres URL for the governed relation catalog (onto's metadata store)",
    )
    parser.add_argument("--relation-catalog-scope", default="hotpotqa")
    parser.add_argument(
        "--no-relation-catalog", action="store_true",
        help="Disable relation governance -- write the extractor's raw relation names as-is (offline/testing only)",
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
        relation_catalog_db_url=None if args.no_relation_catalog else args.relation_catalog_db_url,
        relation_catalog_scope=args.relation_catalog_scope,
    )
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"space={report['space']}")
    print(f"questions={report['question_count']} extraction_errors={report['extraction_errors']}")
    print(f"vertex_count={report['vertex_count']} edge_count={report['edge_count']}")
    print(f"relation_catalog_size={report['relation_catalog_size']}")
    print(f"cases_json={report['cases_json']}")
    print(f"report_json={args.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
