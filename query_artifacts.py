import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

sys.path.append(str(Path(__file__).resolve().parent / "agents"))
from ontology_artifacts import ensure_artifact_schema  # noqa: E402


DB_URL = os.environ.get(
    "ALETHEIA_PG_URL",
    f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
)


def get_engine():
    return create_engine(DB_URL)


def _load_json(value, default):
    if not value:
        return default
    return json.loads(value)


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fetch_snapshot(conn):
    artifacts = conn.execute(
        text(
            """
            SELECT id, project_id, canonical_key, artifact_type, name, description,
                   payload_json, confidence, source_refs_json, status, version,
                   source_agent, created_at, updated_at
            FROM aletheia_ontology_artifacts
            ORDER BY canonical_key
            """
        )
    ).mappings().all()
    evidence = conn.execute(
        text(
            """
            SELECT artifact_id, evidence_type, source_ref, content_hash, summary,
                   raw_payload_json, confidence, created_at
            FROM aletheia_artifact_evidence
            ORDER BY artifact_id, source_ref, content_hash
            """
        )
    ).mappings().all()
    evidence_by_artifact = {}
    for row in evidence:
        evidence_by_artifact.setdefault(row["artifact_id"], []).append(
            {
                "evidence_type": row["evidence_type"],
                "source_ref": row["source_ref"],
                "content_hash": row["content_hash"],
                "summary": row["summary"],
                "raw_payload": _load_json(row["raw_payload_json"], {}),
                "confidence": row["confidence"],
            }
        )

    return {
        "artifacts": [
            {
                "project_id": row["project_id"],
                "canonical_key": row["canonical_key"],
                "artifact_type": row["artifact_type"],
                "name": row["name"],
                "description": row["description"],
                "payload": _load_json(row["payload_json"], {}),
                "confidence": row["confidence"],
                "source_refs": _load_json(row["source_refs_json"], []),
                "status": row["status"],
                "version": row["version"],
                "source_agent": row["source_agent"],
                "evidence": evidence_by_artifact.get(row["id"], []),
            }
            for row in artifacts
        ]
    }


def export_snapshot(args):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.connect() as conn:
        snapshot = fetch_snapshot(conn)
    body = json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(body + "\n", encoding="utf-8")
    else:
        print(body)


def list_artifacts(args):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT canonical_key, artifact_type, name, status, version, source_agent
                FROM aletheia_ontology_artifacts
                ORDER BY artifact_type, canonical_key
                """
            )
        ).mappings().all()
    for row in rows:
        print(
            f"{row['canonical_key']} [{row['artifact_type']}] "
            f"status={row['status']} version={row['version']} source={row['source_agent']} name={row['name']}"
        )


def _fetch_artifact_for_update(conn, canonical_key):
    row = conn.execute(
        text(
            """
            SELECT id, canonical_key, artifact_type, name, description, payload_json,
                   status, version
            FROM aletheia_ontology_artifacts
            WHERE canonical_key = :canonical_key
            FOR UPDATE
            """
        ),
        {"canonical_key": canonical_key},
    ).mappings().first()
    if not row:
        raise SystemExit(f"Artifact not found: {canonical_key}")
    return row


def _record_review_event(
    conn,
    *,
    artifact,
    decision,
    reviewer,
    reason,
    before_status,
    after_status,
    before_version,
    after_version,
    before_payload_json,
    after_payload_json,
):
    conn.execute(
        text(
            """
            INSERT INTO aletheia_artifact_reviews
            (artifact_id, canonical_key, decision, reviewer, reason,
             before_status, after_status, before_version, after_version,
             before_payload_json, after_payload_json, created_at)
            VALUES
            (:artifact_id, :canonical_key, :decision, :reviewer, :reason,
             :before_status, :after_status, :before_version, :after_version,
             :before_payload_json, :after_payload_json, NOW())
            """
        ),
        {
            "artifact_id": artifact["id"],
            "canonical_key": artifact["canonical_key"],
            "decision": decision,
            "reviewer": reviewer,
            "reason": reason,
            "before_status": before_status,
            "after_status": after_status,
            "before_version": before_version,
            "after_version": after_version,
            "before_payload_json": before_payload_json,
            "after_payload_json": after_payload_json,
        },
    )


def _review_status(args, status):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.begin() as conn:
        artifact = _fetch_artifact_for_update(conn, args.canonical_key)
        before_status = artifact["status"]
        before_version = artifact["version"]
        before_payload_json = artifact["payload_json"]
        after_version = before_version + 1
        conn.execute(
            text(
                """
                UPDATE aletheia_ontology_artifacts
                SET status = :status, version = version + 1, updated_at = NOW()
                WHERE canonical_key = :canonical_key
                """
            ),
            {"status": status, "canonical_key": args.canonical_key},
        )
        _record_review_event(
            conn,
            artifact=artifact,
            decision=status,
            reviewer=args.reviewer,
            reason=getattr(args, "reason", None),
            before_status=before_status,
            after_status=status,
            before_version=before_version,
            after_version=after_version,
            before_payload_json=before_payload_json,
            after_payload_json=before_payload_json,
        )
    print(f"{args.canonical_key} status={status} reviewer={args.reviewer}")


def update_status(args):
    _review_status(args, args.status)


def approve_artifact(args):
    _review_status(args, "approved")


def reject_artifact(args):
    _review_status(args, "rejected")


def needs_changes(args):
    _review_status(args, "needs_changes")


def comment_artifact(args):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.begin() as conn:
        artifact = _fetch_artifact_for_update(conn, args.canonical_key)
        _record_review_event(
            conn,
            artifact=artifact,
            decision="comment",
            reviewer=args.reviewer,
            reason=args.reason,
            before_status=artifact["status"],
            after_status=artifact["status"],
            before_version=artifact["version"],
            after_version=artifact["version"],
            before_payload_json=artifact["payload_json"],
            after_payload_json=artifact["payload_json"],
        )
    print(f"{args.canonical_key} comment reviewer={args.reviewer}")


def edit_artifact(args):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.begin() as conn:
        artifact = _fetch_artifact_for_update(conn, args.canonical_key)
        payload = _load_json(artifact["payload_json"], {})
        if args.payload_json:
            payload = json.loads(args.payload_json)
        if args.payload_file:
            payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        name = args.name if args.name is not None else artifact["name"]
        description = args.description if args.description is not None else artifact["description"]
        after_payload_json = _json_dump(payload)
        after_version = artifact["version"] + 1
        conn.execute(
            text(
                """
                UPDATE aletheia_ontology_artifacts
                SET name = :name,
                    description = :description,
                    payload_json = :payload_json,
                    version = version + 1,
                    updated_at = NOW()
                WHERE canonical_key = :canonical_key
                """
            ),
            {
                "name": name,
                "description": description,
                "payload_json": after_payload_json,
                "canonical_key": args.canonical_key,
            },
        )
        _record_review_event(
            conn,
            artifact=artifact,
            decision="edit",
            reviewer=args.reviewer,
            reason=args.reason,
            before_status=artifact["status"],
            after_status=artifact["status"],
            before_version=artifact["version"],
            after_version=after_version,
            before_payload_json=artifact["payload_json"],
            after_payload_json=after_payload_json,
        )
    print(f"{args.canonical_key} edited reviewer={args.reviewer}")


def show_artifact(args):
    engine = get_engine()
    ensure_artifact_schema(engine)
    with engine.connect() as conn:
        artifact = conn.execute(
            text(
                """
                SELECT id, project_id, canonical_key, artifact_type, name, description,
                       payload_json, confidence, source_refs_json, status, version,
                       source_agent, created_at, updated_at
                FROM aletheia_ontology_artifacts
                WHERE canonical_key = :canonical_key
                """
            ),
            {"canonical_key": args.canonical_key},
        ).mappings().first()
        if not artifact:
            raise SystemExit(f"Artifact not found: {args.canonical_key}")
        evidence = conn.execute(
            text(
                """
                SELECT evidence_type, source_ref, content_hash, summary,
                       raw_payload_json, confidence, created_at
                FROM aletheia_artifact_evidence
                WHERE artifact_id = :artifact_id
                ORDER BY source_ref, content_hash
                """
            ),
            {"artifact_id": artifact["id"]},
        ).mappings().all()
        reviews = conn.execute(
            text(
                """
                SELECT decision, reviewer, reason, before_status, after_status,
                       before_version, after_version, created_at
                FROM aletheia_artifact_reviews
                WHERE artifact_id = :artifact_id
                ORDER BY created_at, id
                """
            ),
            {"artifact_id": artifact["id"]},
        ).mappings().all()

    result = {
        "project_id": artifact["project_id"],
        "canonical_key": artifact["canonical_key"],
        "artifact_type": artifact["artifact_type"],
        "name": artifact["name"],
        "description": artifact["description"],
        "payload": _load_json(artifact["payload_json"], {}),
        "confidence": artifact["confidence"],
        "source_refs": _load_json(artifact["source_refs_json"], []),
        "status": artifact["status"],
        "version": artifact["version"],
        "source_agent": artifact["source_agent"],
        "evidence": [
            {
                "evidence_type": row["evidence_type"],
                "source_ref": row["source_ref"],
                "content_hash": row["content_hash"],
                "summary": row["summary"],
                "raw_payload": _load_json(row["raw_payload_json"], {}),
                "confidence": row["confidence"],
            }
            for row in evidence
        ],
        "reviews": [dict(row) for row in reviews],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str))


def _artifact_map(snapshot):
    return {item["canonical_key"]: item for item in snapshot.get("artifacts", [])}


def diff_snapshots(args):
    before = _artifact_map(json.loads(Path(args.before).read_text(encoding="utf-8")))
    after = _artifact_map(json.loads(Path(args.after).read_text(encoding="utf-8")))
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    removed = sorted(before_keys - after_keys)
    changed = []
    for key in sorted(before_keys & after_keys):
        if before[key] != after[key]:
            changed.append(
                {
                    "canonical_key": key,
                    "before_status": before[key]["status"],
                    "after_status": after[key]["status"],
                    "before_version": before[key]["version"],
                    "after_version": after[key]["version"],
                    "payload_changed": before[key]["payload"] != after[key]["payload"],
                    "evidence_changed": before[key]["evidence"] != after[key]["evidence"],
                }
            )
    report = {"added": added, "removed": removed, "changed": changed}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="Aletheia ontology artifact inspection and review CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("snapshot", help="Export artifact snapshot as JSON")
    export_parser.add_argument("--output", "-o", help="Write snapshot to a file")
    export_parser.set_defaults(func=export_snapshot)

    list_parser = subparsers.add_parser("list", help="List artifact status and versions")
    list_parser.set_defaults(func=list_artifacts)

    show_parser = subparsers.add_parser("show", help="Show one artifact with evidence and review history")
    show_parser.add_argument("canonical_key")
    show_parser.set_defaults(func=show_artifact)

    status_parser = subparsers.add_parser("status", help="Set artifact status and record a review event")
    status_parser.add_argument("canonical_key")
    status_parser.add_argument("status", choices=["draft", "proposed", "approved", "rejected", "needs_changes", "deprecated"])
    status_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    status_parser.add_argument("--reason")
    status_parser.set_defaults(func=update_status)

    approve_parser = subparsers.add_parser("approve", help="Approve an artifact for canonical/ingestion use")
    approve_parser.add_argument("canonical_key")
    approve_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    approve_parser.add_argument("--reason")
    approve_parser.set_defaults(func=approve_artifact)

    reject_parser = subparsers.add_parser("reject", help="Reject an artifact")
    reject_parser.add_argument("canonical_key")
    reject_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    reject_parser.add_argument("--reason", required=True)
    reject_parser.set_defaults(func=reject_artifact)

    changes_parser = subparsers.add_parser("needs-changes", help="Mark an artifact as needing changes")
    changes_parser.add_argument("canonical_key")
    changes_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    changes_parser.add_argument("--reason", required=True)
    changes_parser.set_defaults(func=needs_changes)

    comment_parser = subparsers.add_parser("comment", help="Add a review/audit comment without changing status")
    comment_parser.add_argument("canonical_key")
    comment_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    comment_parser.add_argument("--reason", required=True)
    comment_parser.set_defaults(func=comment_artifact)

    edit_parser = subparsers.add_parser("edit", help="Edit artifact name/description/payload and record audit history")
    edit_parser.add_argument("canonical_key")
    edit_parser.add_argument("--reviewer", default=os.environ.get("USER", "unknown"))
    edit_parser.add_argument("--reason", required=True)
    edit_parser.add_argument("--name")
    edit_parser.add_argument("--description")
    payload_group = edit_parser.add_mutually_exclusive_group()
    payload_group.add_argument("--payload-json")
    payload_group.add_argument("--payload-file")
    edit_parser.set_defaults(func=edit_artifact)

    diff_parser = subparsers.add_parser("diff", help="Diff two artifact snapshot JSON files")
    diff_parser.add_argument("before")
    diff_parser.add_argument("after")
    diff_parser.set_defaults(func=diff_snapshots)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
