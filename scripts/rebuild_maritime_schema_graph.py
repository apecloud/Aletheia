#!/usr/bin/env python3
"""Reset maritime metadata and rebuild draft ontology via SchemaGraphModelingAgent.

This script intentionally does not import the curated maritime OBJECT_SPECS /
LINK_SPECS fixture. It keeps raw source tables intact, clears generated metadata
for one tenant, then asks the generic schema modeling agent to infer draft graph
ontology artifacts from schema/profile evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SOURCE_TABLES = [
    "maritime_chokepoint_country_dependencies",
    "maritime_chokepoint_risk_indicators",
    "maritime_chokepoint_systemic_risk_results",
]


def _default_pg_url() -> str:
    return (
        "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/"
        f"{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}"
    )


def _default_mysql_url() -> str:
    return "mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/aletheia_test_data"


def _table_exists(engine, table: str) -> bool:
    return table in inspect(engine).get_table_names()


def _has_column(engine, table: str, column: str) -> bool:
    if not _table_exists(engine, table):
        return False
    return column in {item["name"] for item in inspect(engine).get_columns(table)}


def _count_project_rows(conn, engine, table: str, tenant: str) -> int | None:
    if not _table_exists(engine, table) or not _has_column(engine, table, "project_id"):
        return None
    return int(conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE project_id = :tenant"), {"tenant": tenant}).scalar() or 0)


def source_table_counts(source_engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with source_engine.connect() as conn:
        for table in SOURCE_TABLES:
            counts[table] = int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
    return counts


def metadata_counts(metadata_engine, tenant: str) -> dict[str, int | None]:
    tables = [
        "aletheia_ontology_artifacts",
        "aletheia_proposed_graph_elements",
        "aletheia_iterative_graph_enrichment_runs",
        "aletheia_web_enrichment_runs",
        "aletheia_web_enrichment_proposals",
        "aletheia_reasoning_tasks",
        "aletheia_reasoning_runs",
        "aletheia_reasoning_findings",
        "aletheia_autopilot_sessions",
        "aletheia_autopilot_hypotheses",
        "aletheia_autopilot_candidate_findings",
        "aletheia_continuous_enrichment_sessions",
        "aletheia_graph_deep_research_benchmarks",
        "aletheia_agent_runs",
        "aletheia_schema_object_candidates",
        "aletheia_schema_link_candidates",
    ]
    with metadata_engine.connect() as conn:
        return {table: _count_project_rows(conn, metadata_engine, table, tenant) for table in tables}


def cleanup_tenant_metadata(metadata_engine, tenant: str) -> dict[str, int]:
    deleted: dict[str, int] = {}

    def delete_where(conn, table: str, where_sql: str, params: dict[str, Any]) -> None:
        if not _table_exists(metadata_engine, table):
            deleted[table] = 0
            return
        result = conn.execute(text(f"DELETE FROM {table} WHERE {where_sql}"), params)
        deleted[table] = int(result.rowcount or 0)

    with metadata_engine.begin() as conn:
        artifact_ids = [
            row[0]
            for row in conn.execute(
                text("SELECT id FROM aletheia_ontology_artifacts WHERE project_id = :tenant"),
                {"tenant": tenant},
            ).fetchall()
        ] if _table_exists(metadata_engine, "aletheia_ontology_artifacts") else []
        if artifact_ids:
            delete_where(conn, "aletheia_artifact_evidence", "artifact_id = ANY(:artifact_ids)", {"artifact_ids": artifact_ids})
        else:
            deleted["aletheia_artifact_evidence"] = 0

        delete_where(conn, "aletheia_artifact_reviews", "project_id = :tenant", {"tenant": tenant})

        if _table_exists(metadata_engine, "aletheia_agent_runs"):
            run_ids = [
                row[0]
                for row in conn.execute(
                    text("SELECT id FROM aletheia_agent_runs WHERE project_id = :tenant"),
                    {"tenant": tenant},
                ).fetchall()
            ]
            if run_ids:
                delete_where(conn, "aletheia_agent_output_artifacts", "run_id = ANY(:run_ids)", {"run_ids": run_ids})
            else:
                deleted["aletheia_agent_output_artifacts"] = 0
        delete_where(conn, "aletheia_agent_runs", "project_id = :tenant", {"tenant": tenant})

        delete_where(conn, "aletheia_web_enrichment_proposals", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_web_enrichment_runs", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_proposed_graph_elements", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_iterative_graph_enrichment_runs", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_graph_deep_research_benchmarks", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_continuous_enrichment_sessions", "project_id = :tenant", {"tenant": tenant})

        delete_where(conn, "aletheia_autopilot_candidate_findings", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_autopilot_hypotheses", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_autopilot_sessions", "project_id = :tenant", {"tenant": tenant})

        delete_where(conn, "aletheia_reasoning_reviews", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_reasoning_findings", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_reasoning_runs", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_reasoning_tasks", "project_id = :tenant", {"tenant": tenant})

        delete_where(conn, "aletheia_schema_link_candidates", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_schema_object_candidates", "project_id = :tenant", {"tenant": tenant})
        delete_where(conn, "aletheia_ontology_artifacts", "project_id = :tenant", {"tenant": tenant})

    return deleted


def artifact_summary(metadata_engine, tenant: str) -> dict[str, Any]:
    if not _table_exists(metadata_engine, "aletheia_ontology_artifacts"):
        return {"count": 0, "by_type": {}, "by_status": {}, "artifacts": []}
    with metadata_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT canonical_key, artifact_type, name, status, confidence, source_agent, payload_json, source_refs_json
                FROM aletheia_ontology_artifacts
                WHERE project_id = :tenant
                ORDER BY artifact_type, canonical_key
                """
            ),
            {"tenant": tenant},
        ).mappings().all()
    artifacts = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        source_refs = json.loads(row["source_refs_json"] or "[]")
        artifacts.append(
            {
                "canonical_key": row["canonical_key"],
                "artifact_type": row["artifact_type"],
                "name": row["name"],
                "status": row["status"],
                "confidence": row["confidence"],
                "source_agent": row["source_agent"],
                "prompt_version": payload.get("prompt_version"),
                "llm_inferred": payload.get("llm_inferred"),
                "review_boundary": payload.get("canonical_write_boundary"),
                "source_refs": source_refs,
            }
        )
    return {
        "count": len(artifacts),
        "by_type": dict(Counter(item["artifact_type"] for item in artifacts)),
        "by_status": dict(Counter(item["status"] for item in artifacts)),
        "artifacts": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean maritime tenant metadata and rebuild draft ontology with SchemaGraphModelingAgent")
    parser.add_argument("--tenant", default="maritime-risk")
    parser.add_argument("--source", default=os.environ.get("ALETHEIA_MYSQL_URL", _default_mysql_url()))
    parser.add_argument("--metadata", default=os.environ.get("ALETHEIA_PG_URL", _default_pg_url()))
    parser.add_argument("--model", default=os.environ.get("ALETHEIA_SCHEMA_GRAPH_MODEL", "gemini/gemini-3.1-pro-preview"))
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--report-json", default=str(ROOT / "reports" / "maritime-schema-graph-rebuild-task317.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from agents.schema_graph_modeling_agent import SchemaGraphModelingAgent

    source_engine = create_engine(args.source)
    metadata_engine = create_engine(args.metadata)

    source_before = source_table_counts(source_engine)
    metadata_before = metadata_counts(metadata_engine, args.tenant)
    deleted: dict[str, int] = {}
    if not args.dry_run:
        deleted = cleanup_tenant_metadata(metadata_engine, args.tenant)

    agent = SchemaGraphModelingAgent(
        source_db_url=args.source,
        metadata_db_url=args.metadata,
        model_name=args.model,
        project_id=args.tenant,
    )
    result = agent.run(
        include_tables=SOURCE_TABLES,
        include_profile=True,
        sample_size=args.sample_size,
        persist=not args.dry_run,
    )

    source_after = source_table_counts(source_engine)
    metadata_after = metadata_counts(metadata_engine, args.tenant)
    summary = artifact_summary(metadata_engine, args.tenant)
    output = {
        "task": "317",
        "tenant": args.tenant,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "source_tables": SOURCE_TABLES,
        "source_counts_before": source_before,
        "source_counts_after": source_after,
        "metadata_counts_before": metadata_before,
        "deleted_metadata_rows": deleted,
        "metadata_counts_after": metadata_after,
        "prompt_version": agent.prompt_version,
        "schema_evidence": result.schema,
        "draft": result.draft.model_dump(),
        "persisted_artifacts": result.artifacts,
        "artifact_summary": summary,
        "boundaries": {
            "used_schema_graph_modeling_agent": True,
            "used_import_maritime_object_specs": False,
            "used_static_entity_config_for_semantics": False,
            "canonical_write": False,
            "formal_graph_write": False,
            "review_gate": "draft_only_until_human_review",
        },
    }
    if args.report_json:
        report_path = Path(args.report_json)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
