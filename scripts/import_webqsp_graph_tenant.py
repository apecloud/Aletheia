#!/usr/bin/env python3
"""Persist WebQSP's real local subgraph directly into Nebula Graph.

Sibling of ``import_webqsp_benchmark_tenant.py`` (the SQL variant) -- but a
genuinely different, more honest import, not just a backend swap. The SQL
importer's ``materialize_questions()`` only ever materializes ONE synthetic
path per question (the gold ``gold_relation_paths`` entries), inventing
placeholder intermediate-hop node ids (``f"{qid}_hop{n}_{path_idx}"``) for
multi-hop paths -- there are no distractor facts, no real Freebase entities
beyond the gold chain, so retrieval can't fail in any way that matters. This
is exactly why ``reports/webqsp-task83-sota-gate.md`` flags the existing
86/100 internal score as "NOT COMPARABLE" to real SOTA.

This importer instead reads each question's REAL local subgraph straight
from ``benchmarks/webqsp/validation.parquet``'s ``graph`` column, which is
already ``(subject, relation, object)`` triples with clean, already-distinct
Freebase dotted predicates (e.g. ``people.person.nationality``) -- unlike
HotpotQA, no LLM extraction step is needed. Relations are registered into
the governed ``RelationCatalog`` (scope="webqsp") via ``register_identity``
(no LLM semantic matching either -- Freebase predicates are already atomic
and reused verbatim across questions, so a cheap exact-match dedup is all
that's needed).

Uses the same single-TAG + single-EDGE-type model (``HotpotEntity``/
``RELATION``) as the HotpotQA Nebula tenant, in its own dedicated space
(default ``webqsp_kg``) -- ``GraphInstanceRepository``'s defaults apply
unchanged, only ``space``/``relation_catalog_scope`` differ per tenant.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "agents"))
sys.path.append(str(ROOT / "scripts"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from graph_db_client import NebulaGraphClient  # noqa: E402
from graph_ontology_registry import get_edge_type, propose_edge_type, propose_node_type  # noqa: E402
from hotpotqa_entity_ids import entity_id  # noqa: E402
from import_hotpotqa_nebula_tenant import _insert_with_schema_retry  # noqa: E402
from ontology_artifacts import ensure_artifact_schema  # noqa: E402
from relation_catalog import RelationCatalog  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402

DEFAULT_SPACE = "webqsp_kg"
DEFAULT_QUESTIONS_JSON = ROOT / "benchmarks" / "webqsp" / "webqsp_aletheia_benchmark.json"
DEFAULT_PARQUET = ROOT / "benchmarks" / "webqsp" / "validation.parquet"
DEFAULT_CASES = ROOT / "benchmarks" / "webqsp" / "webqsp_graph_cases.json"
DEFAULT_REPORT = ROOT / "reports" / "webqsp-graph-import.json"
EVIDENCE_MAX_LEN = 300
# WebQSP stays on the pre-typed flat single-TAG/single-EDGE-type model for
# now (agents/graph_ontology_registry.py's multi-TAG model is proven on
# HotpotQA first, see project plan) -- but GraphInstanceRepository's
# reasoning_entity_config/reasoning_link_config are now registry-driven for
# every tenant, so this importer registers the one flat node type plus each
# governed relation as a wildcard-domain/range edge type, so WebQSP keeps
# working unchanged rather than silently getting an empty entity/link config.
TAG_NAME = "HotpotEntity"
EDGE_TYPE = "RELATION"


def ensure_schema(client: NebulaGraphClient) -> None:
    client.execute_query(f"CREATE TAG IF NOT EXISTS {TAG_NAME}(label string);")
    client.execute_query(f"CREATE EDGE IF NOT EXISTS {EDGE_TYPE}(relation_label string, evidence string);")
    time.sleep(11)


def register_flat_ontology_types(session, tenant_id: str, relation_catalog: RelationCatalog | None) -> None:
    propose_node_type(
        session, tenant_id=tenant_id, name=TAG_NAME,
        description="WebQSP's flat entity tag (pre-typed model).",
        properties=[{"name": "label", "data_type": "string"}],
        confidence=0.6, evidence=["webqsp_flat_model"], status="approved",
    )
    for name in (relation_catalog.entries if relation_catalog is not None else {}):
        existing = get_edge_type(session, tenant_id, name)
        if existing is not None:
            continue
        propose_edge_type(
            session, tenant_id=tenant_id, name=name, domain=[TAG_NAME], range=[TAG_NAME],
            description=(relation_catalog.entries.get(name) or {}).get("description", ""),
            confidence=0.6, evidence=["webqsp_flat_model"], status="approved",
        )


def load_webqsp_graphs(parquet_path: Path, qids: set[str]) -> dict[str, list[tuple[str, str, str]]]:
    """Each requested question's real local subgraph, as plain triples --
    straight from the parquet's own ``graph`` column, no extraction step."""
    df = pd.read_parquet(parquet_path)
    df = df[df["id"].isin(qids)]
    graphs: dict[str, list[tuple[str, str, str]]] = {}
    for _, row in df.iterrows():
        graphs[row["id"]] = [(str(s), str(r), str(o)) for s, r, o in row["graph"]]
    return graphs


def materialize_triples_to_graph(
    questions: list[dict[str, Any]],
    graphs_by_qid: dict[str, list[tuple[str, str, str]]],
    relation_catalog: RelationCatalog | None = None,
) -> dict[str, Any]:
    """Generic core: given each question's own real ``(subject, relation,
    object)`` triples (no extraction needed -- the caller already has them),
    shape them into Nebula vertex/edge rows and benchmark cases. Reusable
    for any tenant whose source data is already triples (unlike HotpotQA,
    which needs ``passage_relation_extraction`` first)."""
    vertex_rows: dict[str, dict[str, Any]] = {}
    edge_rows: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    output_cases: list[dict[str, Any]] = []
    skipped_no_graph = 0

    def ensure_vertex(qid: str, label: str) -> str:
        vid = entity_id(qid, label)
        vertex_rows.setdefault(vid, {"id": vid, "label": str(label)})
        return vid

    for idx, q in enumerate(questions, start=1):
        qid = q["qid"]
        triples = graphs_by_qid.get(qid) or []
        if not triples:
            skipped_no_graph += 1
        for subj, rel, obj in triples:
            from_id = ensure_vertex(qid, subj)
            to_id = ensure_vertex(qid, obj)
            relation_label = (
                relation_catalog.register_identity(rel) if relation_catalog is not None else rel
            )
            edge_key = (from_id, to_id, relation_label)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edge_rows.append({
                    "source_id": from_id,
                    "target_id": to_id,
                    "relation_label": relation_label,
                    "evidence": f"{subj} {rel} {obj}"[:EVIDENCE_MAX_LEN],
                })

        topic_entities = q.get("q_entity") or ([q["topic_entity"]] if q.get("topic_entity") else [])
        topic_label = topic_entities[0] if topic_entities else ""
        topic_id = ensure_vertex(qid, topic_label) if topic_label else ""
        additional_center_nodes = sorted({
            ensure_vertex(qid, label) for label in topic_entities[1:] if label
        })

        answers = q.get("answers") or q.get("answer_entities") or []
        output_cases.append({
            "qid": qid,
            "question": q["question"],
            "center_node": topic_id,
            "additional_center_nodes": additional_center_nodes,
            "answers": [str(a) for a in answers],
            "triple_count": len(triples),
        })
        print(f"[materialize] {idx}/{len(questions)} qid={qid} triples={len(triples)}", flush=True)

    return {
        "vertex_rows": list(vertex_rows.values()),
        "edge_rows": edge_rows,
        "cases": output_cases,
        "skipped_no_graph": skipped_no_graph,
        "question_count": len(questions),
    }


def import_webqsp_graph_tenant(
    *,
    questions_json: Path,
    parquet_path: Path,
    max_questions: int | None,
    space: str,
    nebula_ip: str,
    nebula_port: int,
    nebula_user: str,
    nebula_password: str,
    cases_json: Path,
    relation_catalog_db_url: str | None = None,
    relation_catalog_scope: str = "webqsp",
) -> dict[str, Any]:
    questions = json.loads(questions_json.read_text(encoding="utf-8"))
    if max_questions:
        questions = questions[:max_questions]
    if not questions:
        raise ValueError(f"No WebQSP questions found at {questions_json}")

    qids = {q["qid"] for q in questions}
    graphs_by_qid = load_webqsp_graphs(parquet_path, qids)

    metadata_db_url = relation_catalog_db_url or default_metadata_db_url()
    relation_catalog = RelationCatalog.load_from_postgres(
        relation_catalog_db_url, scope=relation_catalog_scope,
    ) if relation_catalog_db_url else None

    materialized = materialize_triples_to_graph(questions, graphs_by_qid, relation_catalog)

    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    session = sessionmaker(bind=engine)()
    register_flat_ontology_types(session, relation_catalog_scope, relation_catalog)
    session.commit()

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
        "questions_json": str(questions_json),
        "cases_json": str(cases_json),
        "question_count": materialized["question_count"],
        "skipped_no_graph": materialized["skipped_no_graph"],
        "vertex_count": len(materialized["vertex_rows"]),
        "edge_count": len(materialized["edge_rows"]),
        "relation_catalog_size": len(relation_catalog.entries) if relation_catalog is not None else None,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import WebQSP's real local subgraph directly into Nebula Graph")
    parser.add_argument("--questions-json", type=Path, default=DEFAULT_QUESTIONS_JSON)
    parser.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-password", default="nebula")
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--relation-catalog-db-url", default=default_metadata_db_url())
    parser.add_argument("--relation-catalog-scope", default="webqsp")
    parser.add_argument(
        "--no-relation-catalog", action="store_true",
        help="Disable relation governance -- write each triple's raw Freebase predicate as-is (offline/testing only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.time()
    report = import_webqsp_graph_tenant(
        questions_json=args.questions_json,
        parquet_path=args.parquet,
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
    report["elapsed_seconds"] = round(time.time() - started, 1)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"space={report['space']}")
    print(f"questions={report['question_count']} skipped_no_graph={report['skipped_no_graph']}")
    print(f"vertex_count={report['vertex_count']} edge_count={report['edge_count']}")
    print(f"relation_catalog_size={report['relation_catalog_size']}")
    print(f"elapsed_seconds={report['elapsed_seconds']}")
    print(f"cases_json={report['cases_json']}")
    print(f"report_json={args.report_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
