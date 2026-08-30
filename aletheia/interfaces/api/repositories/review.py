"""ReviewRepository, extracted from server.py. No behavior change."""

from urllib.parse import parse_qs, quote, unquote, urlparse
from sqlalchemy import bindparam, create_engine, text
from aletheia.interfaces.api.helpers import _artifact_to_dict, _json_dump, _load_json, _ontology_source_schema, _require_reason, _safe_error_message
from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache


class ReviewRepository(_TenantScopedEngineCache):
    def list_artifacts(self, tenant, filters):
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id}
        for field in ("artifact_type", "status", "source_agent"):
            value = filters.get(field)
            if value:
                if field == "status" and value == "proposed":
                    conditions.append("status IN (:status, :draft_status)")
                    params["status"] = value
                    params["draft_status"] = "draft"
                else:
                    conditions.append(f"{field} = :{field}")
                    params[field] = value
        search = filters.get("search")
        if search:
            conditions.append(
                "(canonical_key ILIKE :search OR name ILIKE :search OR description ILIKE :search)"
            )
            params["search"] = f"%{search}%"
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.metadata_engine_for(tenant).connect() as conn:
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
                    WHERE project_id = :tenant_id
                    GROUP BY artifact_type, status
                    ORDER BY artifact_type, status
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "artifacts": [_artifact_to_dict(row) for row in rows],
            "stats": [dict(row) for row in stats],
        }

    def list_web_enrichment(self, tenant, target_artifact_key=None, limit=50):
        params = {"tenant_id": tenant.tenant_id, "limit": limit}
        conditions = ["p.project_id = :tenant_id"]
        if target_artifact_key:
            conditions.append("p.target_artifact_key = :target_artifact_key")
            params["target_artifact_key"] = target_artifact_key
        where = " AND ".join(conditions)
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT p.proposal_key, p.target_artifact_key, p.source_url,
                               p.source_title, p.summary, p.raw_payload_json,
                               p.content_hash, p.confidence, p.status, p.created_at,
                               r.run_key, r.search_provider, r.safety_profile_json,
                               r.budget_json, r.skipped_sources_json
                        FROM aletheia_web_enrichment_proposals p
                        JOIN aletheia_web_enrichment_runs r ON r.id = p.run_id
                        WHERE {where}
                        ORDER BY p.created_at DESC, p.id DESC
                        LIMIT :limit
                        """
                    ),
                    params,
                ).mappings().all()
        except Exception as exc:
            return {
                "tenant": tenant.public_dict(),
                "proposals": [],
                "degraded": True,
                "degraded_reason": _safe_error_message(exc),
            }
        return {
            "tenant": tenant.public_dict(),
            "proposals": [
                {
                    "proposal_key": row["proposal_key"],
                    "target_artifact_key": row["target_artifact_key"],
                    "source_url": row["source_url"],
                    "source_title": row["source_title"],
                    "summary": row["summary"],
                    "raw_payload": _load_json(row["raw_payload_json"], {}),
                    "content_hash": row["content_hash"],
                    "confidence": row["confidence"],
                    "status": row["status"],
                    "created_at": str(row["created_at"]) if row["created_at"] else None,
                    "run_key": row["run_key"],
                    "search_provider": row["search_provider"],
                    "safety_profile": _load_json(row["safety_profile_json"], {}),
                    "budget": _load_json(row["budget_json"], {}),
                    "skipped_sources": _load_json(row["skipped_sources_json"], []),
                }
                for row in rows
            ],
        }

    def get_artifact(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            artifact = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, artifact_type, name, description,
                           payload_json, confidence, source_refs_json, status, version,
                           source_agent, created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
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
        result["tenant"] = tenant.public_dict()
        result["source_schema"] = _ontology_source_schema(result)
        result["canonical"] = {
            "status": result["status"],
            "version": result["version"],
            "graph_ingestion_eligible": result["status"] == "approved",
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
        }
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
        result["used_by"] = self.used_by(tenant, result)
        result["web_enrichment"] = self.list_web_enrichment(tenant, canonical_key, limit=20).get("proposals", [])
        return result

    def used_by(self, tenant, artifact):
        canonical_key = artifact.get("canonical_key")
        payload = artifact.get("payload") or {}
        used_by = []
        if canonical_key and canonical_key.startswith("link:"):
            source = payload.get("source_object_name") or payload.get("source_object_key") or "source"
            target = payload.get("target_object_name") or payload.get("target_object_key") or "target"
            used_by.append(
                {
                    "kind": "graph_path",
                    "label": f"{source} -> {target} approved graph paths",
                    "href": f"/?screen=graph&tenant={quote(tenant.tenant_id)}&ontology_basis={quote(canonical_key)}",
                    "summary": "Approved links are eligible for graph path projection when a matching reviewed schema projection exists.",
                }
            )
            used_by.append(
                {
                    "kind": "reasoning",
                    "label": f"{source} -> {target} scoped reasoning",
                    "href": f"/?screen=reasoning&tenant={quote(tenant.tenant_id)}&ontology_basis={quote(canonical_key)}",
                    "summary": "Reasoning may cite this reviewed link through tenant-scoped projection metadata.",
                }
            )
        elif canonical_key and canonical_key.startswith("object:"):
            object_type = canonical_key.removeprefix("object:").capitalize()
            used_by.append(
                {
                    "kind": "graph_scope",
                    "label": f"{object_type} graph scopes",
                    "href": f"/?screen=graph&tenant={quote(tenant.tenant_id)}&type={quote(object_type)}",
                    "summary": "Approved object types are eligible for graph and instance views.",
                }
            )
        return used_by

    def review_status(self, tenant, canonical_key, status, reviewer, reason):
        if status != "approved":
            _require_reason(status, reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
            before_status = artifact["status"]
            before_version = artifact["version"]
            before_payload_json = artifact["payload_json"]
            after_version = before_version + 1
            conn.execute(
                text(
                    """
                    UPDATE aletheia_ontology_artifacts
                    SET status = :status, version = version + 1, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "status": status, "canonical_key": canonical_key},
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
        if (
            status == "approved"
            and artifact["artifact_type"] in ("object", "link")
            and artifact["source_agent"] == "GraphNativeTypeRegistrar"
        ):
            self._sync_graph_native_schema(tenant)
        return self.get_artifact(tenant, canonical_key)

    def comment(self, tenant, canonical_key, reviewer, reason):
        _require_reason("comment", reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
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
        return self.get_artifact(tenant, canonical_key)

    def edit(self, tenant, canonical_key, reviewer, reason, name=None, description=None, payload=None):
        with self.metadata_engine_for(tenant).begin() as conn:
            artifact = self._fetch_for_update(conn, tenant, canonical_key)
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
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
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
        return self.get_artifact(tenant, canonical_key)

    def _fetch_for_update(self, conn, tenant, canonical_key):
        artifact = conn.execute(
            text(
                """
                SELECT id, project_id, canonical_key, artifact_type, name, description, payload_json,
                       status, version, source_agent
                FROM aletheia_ontology_artifacts
                WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                FOR UPDATE
                """
            ),
            {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
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
                (artifact_id, project_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version,
                 before_payload_json, after_payload_json, created_at)
                VALUES
                (:artifact_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                 :before_status, :after_status, :before_version, :after_version,
                 :before_payload_json, :after_payload_json, NOW())
                """
            ),
            {
                "artifact_id": artifact["id"],
                "project_id": artifact["project_id"],
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
