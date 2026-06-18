#!/usr/bin/env python3
"""Review proposals and findings produced by one enrich-agent run."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents"))

from ontology_artifacts import (  # noqa: E402
    IterativeGraphEnrichmentRun,
    ProposedGraphElement,
)
from iterative_graph_enrichment_agent import _is_generic_entity_label  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def _json_load(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _domain(url: str | None) -> str:
    if not url:
        return ""
    return urlparse(url).netloc.lower()


def _issues(row: ProposedGraphElement) -> list[str]:
    payload = _json_load(row.payload_json, {})
    issues: list[str] = []
    if row.element_type == "node" and _is_generic_entity_label(
        payload.get("label") or row.name,
        payload.get("ontology_type") or payload.get("type"),
    ):
        issues.append("generic_node_label")
    if row.element_type == "edge":
        for field in ("source_label", "target_label", "relation"):
            if not payload.get(field):
                issues.append(f"missing_{field}")
        if _is_generic_entity_label(payload.get("source_label"), payload.get("source_type")):
            issues.append("generic_source_endpoint")
        if _is_generic_entity_label(payload.get("target_label"), payload.get("target_type")):
            issues.append("generic_target_endpoint")
    if row.element_type == "finding" and not payload.get("evidence_chain"):
        issues.append("finding_missing_evidence_chain")
    if row.element_type in {"situation", "metric_observation", "metric_change_observation", "impact_claim", "indicator_claim", "recommendation"}:
        if not payload.get("evidence_quote"):
            issues.append("semantic_missing_evidence_quote")
        if row.element_type.startswith("metric") and not payload.get("metric_key"):
            issues.append("metric_missing_key")
    if not row.source_url and not payload.get("source_url"):
        issues.append("missing_source_url")
    if _domain(row.source_url or payload.get("source_url")) in {"example.com", "localhost"}:
        issues.append("placeholder_source_url")
    if payload.get("possible_duplicate"):
        issues.append("possible_duplicate")
    if payload.get("review_required") or row.status in {"needs_review", "needs_more_evidence"}:
        issues.append("review_required")
    return sorted(set(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review enrich-agent proposals/findings for one run")
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology"))
    parser.add_argument("--run-key")
    args = parser.parse_args(argv)

    connect_args = {"connect_timeout": 8} if args.target.startswith("postgresql") else {}
    engine = create_engine(args.target, connect_args=connect_args)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        query = session.query(IterativeGraphEnrichmentRun).filter_by(project_id=args.tenant)
        if args.run_key:
            run = query.filter_by(run_key=args.run_key).first()
        else:
            run = query.order_by(IterativeGraphEnrichmentRun.id.desc()).first()
        if not run:
            print(json.dumps({"event": "enrich_post_run_review", "tenant": args.tenant, "status": "run_not_found"}, ensure_ascii=False))
            return 0
        rows = (
            session.query(ProposedGraphElement)
            .filter_by(project_id=args.tenant, run_id=run.id)
            .order_by(ProposedGraphElement.element_type.asc(), ProposedGraphElement.name.asc())
            .all()
        )
        issue_counter: Counter[str] = Counter()
        items = []
        for row in rows:
            issues = _issues(row)
            issue_counter.update(issues)
            payload = _json_load(row.payload_json, {})
            items.append(
                {
                    "type": row.element_type,
                    "name": row.name,
                    "status": row.status,
                    "confidence": row.confidence,
                    "source": row.source_url or payload.get("source_url"),
                    "dedup": payload.get("dedup_decision"),
                    "issues": issues,
                }
            )
        skipped = _json_load(run.skipped_sources_json, [])
        review = {
            "event": "enrich_post_run_review",
            "tenant": args.tenant,
            "run": run.run_key,
            "run_status": run.status,
            "proposal_count": len(rows),
            "finding_count": sum(1 for row in rows if row.element_type == "finding"),
            "counts_by_type": dict(Counter(row.element_type for row in rows)),
            "counts_by_status": dict(Counter(row.status for row in rows)),
            "issue_counts": dict(issue_counter),
            "skipped_reasons": dict(Counter(str(item.get("reason") or "unknown") for item in skipped if isinstance(item, dict))),
            "items": items[:40],
        }
        if not rows and run.status == "completed":
            review["issue_counts"]["empty_output"] = 1
        print(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
