import argparse
import json
import mimetypes
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import create_engine, text

sys.path.append(str(Path(__file__).resolve().parent / "agents"))
from ontology_artifacts import ensure_artifact_schema  # noqa: E402


DB_URL = os.environ.get(
    "ALETHEIA_PG_URL",
    f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
)
STATIC_ROOT = Path(__file__).resolve().parent / "web" / "review_workbench"


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _load_json(value, default):
    if not value:
        return default
    return json.loads(value)


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _require_reason(action, reason):
    if action in {"reject", "rejected", "needs_changes", "comment"} and not reason.strip():
        raise ValueError(f"reason is required for {action}")


def _artifact_to_dict(row):
    return {
        "id": row["id"],
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
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


class ReviewRepository:
    def __init__(self, db_url, ensure_schema=False):
        self.engine = create_engine(db_url)
        if ensure_schema:
            ensure_artifact_schema(self.engine)

    def list_artifacts(self, filters):
        conditions = []
        params = {}
        for field in ("artifact_type", "status", "source_agent"):
            value = filters.get(field)
            if value:
                conditions.append(f"{field} = :{field}")
                params[field] = value
        search = filters.get("search")
        if search:
            conditions.append(
                "(canonical_key ILIKE :search OR name ILIKE :search OR description ILIKE :search)"
            )
            params["search"] = f"%{search}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    {where}
                    ORDER BY
                      CASE status
                        WHEN 'proposed' THEN 0
                        WHEN 'needs_changes' THEN 1
                        WHEN 'draft' THEN 2
                        WHEN 'approved' THEN 3
                        WHEN 'rejected' THEN 4
                        ELSE 5
                      END,
                      artifact_type,
                      canonical_key
                    """
                ),
                params,
            ).mappings().all()
            stats = conn.execute(
                text(
                    """
                    SELECT artifact_type, status, COUNT(*) AS count
                    FROM aletheia_ontology_artifacts
                    GROUP BY artifact_type, status
                    ORDER BY artifact_type, status
                    """
                )
            ).mappings().all()
        return {
            "artifacts": [_artifact_to_dict(row) for row in rows],
            "stats": [dict(row) for row in stats],
        }

    def get_artifact(self, canonical_key):
        with self.engine.connect() as conn:
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
                {"canonical_key": canonical_key},
            ).mappings().first()
            if not artifact:
                return None
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
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"artifact_id": artifact["id"]},
            ).mappings().all()
        result = _artifact_to_dict(artifact)
        result["evidence"] = [
            {
                "evidence_type": row["evidence_type"],
                "source_ref": row["source_ref"],
                "content_hash": row["content_hash"],
                "summary": row["summary"],
                "raw_payload": _load_json(row["raw_payload_json"], {}),
                "confidence": row["confidence"],
                "created_at": str(row["created_at"]) if row["created_at"] else None,
            }
            for row in evidence
        ]
        result["reviews"] = [
            {
                "decision": row["decision"],
                "reviewer": row["reviewer"],
                "reason": row["reason"],
                "before_status": row["before_status"],
                "after_status": row["after_status"],
                "before_version": row["before_version"],
                "after_version": row["after_version"],
                "created_at": str(row["created_at"]) if row["created_at"] else None,
            }
            for row in reviews
        ]
        return result

    def review_status(self, canonical_key, status, reviewer, reason):
        _require_reason(status, reason or "")
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
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
                {"status": status, "canonical_key": canonical_key},
            )
            self._record_review_event(
                conn,
                artifact=artifact,
                decision=status,
                reviewer=reviewer,
                reason=reason,
                before_status=before_status,
                after_status=status,
                before_version=before_version,
                after_version=after_version,
                before_payload_json=before_payload_json,
                after_payload_json=before_payload_json,
            )
        return self.get_artifact(canonical_key)

    def comment(self, canonical_key, reviewer, reason):
        _require_reason("comment", reason or "")
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
            self._record_review_event(
                conn,
                artifact=artifact,
                decision="comment",
                reviewer=reviewer,
                reason=reason,
                before_status=artifact["status"],
                after_status=artifact["status"],
                before_version=artifact["version"],
                after_version=artifact["version"],
                before_payload_json=artifact["payload_json"],
                after_payload_json=artifact["payload_json"],
            )
        return self.get_artifact(canonical_key)

    def edit(self, canonical_key, reviewer, reason, name=None, description=None, payload=None):
        with self.engine.begin() as conn:
            artifact = self._fetch_for_update(conn, canonical_key)
            current_payload = _load_json(artifact["payload_json"], {})
            next_payload = current_payload if payload is None else payload
            next_name = artifact["name"] if name is None else name
            next_description = artifact["description"] if description is None else description
            after_payload_json = _json_dump(next_payload)
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
                    "name": next_name,
                    "description": next_description,
                    "payload_json": after_payload_json,
                    "canonical_key": canonical_key,
                },
            )
            self._record_review_event(
                conn,
                artifact=artifact,
                decision="edit",
                reviewer=reviewer,
                reason=reason,
                before_status=artifact["status"],
                after_status=artifact["status"],
                before_version=artifact["version"],
                after_version=after_version,
                before_payload_json=artifact["payload_json"],
                after_payload_json=after_payload_json,
            )
        return self.get_artifact(canonical_key)

    def _fetch_for_update(self, conn, canonical_key):
        artifact = conn.execute(
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
        if not artifact:
            raise KeyError(canonical_key)
        return artifact

    def _record_review_event(
        self,
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


class ReviewWorkbenchHandler(BaseHTTPRequestHandler):
    repository = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/artifacts":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            self._send_json(self.repository.list_artifacts(filters))
            return
        if parsed.path.startswith("/api/artifacts/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/artifacts/"))
            artifact = self.repository.get_artifact(canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {canonical_key}")
                return
            self._send_json(artifact)
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/artifacts/"):
            self._send_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            return
        parts = parsed.path.removeprefix("/api/artifacts/").split("/")
        if len(parts) != 2:
            self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/artifacts/<canonical_key>/<action>")
            return
        canonical_key = unquote(parts[0])
        action = parts[1]
        try:
            body = self._read_json()
            reviewer = body.get("reviewer") or "Itachi"
            reason = body.get("reason") or ""
            if action == "approve":
                result = self.repository.review_status(canonical_key, "approved", reviewer, reason)
            elif action == "reject":
                result = self.repository.review_status(canonical_key, "rejected", reviewer, reason)
            elif action == "needs-changes":
                result = self.repository.review_status(canonical_key, "needs_changes", reviewer, reason)
            elif action == "comment":
                result = self.repository.comment(canonical_key, reviewer, reason)
            elif action == "edit":
                payload = body.get("payload") if "payload" in body else None
                result = self.repository.edit(
                    canonical_key,
                    reviewer,
                    reason,
                    name=body.get("name"),
                    description=body.get("description"),
                    payload=payload,
                )
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown action: {action}")
                return
        except KeyError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {exc.args[0]}")
            return
        except json.JSONDecodeError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:  # pragma: no cover - displayed to local operator
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(result)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_static(self, request_path):
        relative_path = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        file_path = (STATIC_ROOT / relative_path).resolve()
        if not str(file_path).startswith(str(STATIC_ROOT.resolve())) or not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="Run the Aletheia Review Workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db-url", default=DB_URL)
    parser.add_argument("--ensure-schema", action="store_true", help="Create/migrate artifact tables before serving")
    args = parser.parse_args()

    ReviewWorkbenchHandler.repository = ReviewRepository(args.db_url, ensure_schema=args.ensure_schema)
    server = LocalThreadingHTTPServer((args.host, args.port), ReviewWorkbenchHandler)
    print(f"Review Workbench: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
