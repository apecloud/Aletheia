"""TasksMixin: reasoning-task CRUD (create/list/get/close/delete/bulk-close) and the
run_task/run_task_streaming dispatchers, which delegate to TraversalMixin's
run_scoped_graph_task[_streaming] (resolved across the composed ReasoningRepository's
MRO at call time). Extracted from the monolithic reasoning.py -- part of the
reasoning/ package split. No behavior change.
"""

import hashlib
from urllib.parse import quote
from sqlalchemy import text
from aletheia.interfaces.api.helpers import _json_dump, _slug


class TasksMixin:
    def list_tasks(self, tenant, status_filter=None):
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id}
        if status_filter:
            conditions.append("status = :status_filter")
            params["status_filter"] = status_filter
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE {where}
                    ORDER BY updated_at DESC, id DESC
                """),
                params,
            ).mappings().all()
        tasks = [self._task_to_dict(row) for row in rows]
        return {
            "tenant": tenant.public_dict(),
            "tasks": [
                {
                    **task,
                    "latest_run": self.latest_run(tenant, task["canonical_key"]),
                }
                for task in tasks
            ],
        }

    def create_question_task(self, tenant, payload):
        question = (payload.get("question") or "").strip()
        if not question:
            raise ValueError("question is required")
        scope = payload.get("scope") or {}
        center_node = scope.get("center_node") or payload.get("center_node")
        depth = int(scope.get("depth") or payload.get("depth") or 1)
        limit = int(scope.get("limit") or payload.get("limit") or 200)
        if not center_node:
            types = self.instance_repository.types(tenant).get("types") or []
            if not types:
                raise ValueError(f"No approved object types are available for tenant {tenant.tenant_id}")
            first_type = types[0]["type"]
            instances = self.instance_repository.search(tenant, first_type, "", limit=1).get("instances") or []
            if not instances:
                raise ValueError(f"No source instances are available for tenant {tenant.tenant_id} type {first_type}")
            center_node = instances[0]["id"]
        if ":" not in center_node:
            raise ValueError("center_node must be like ObjectType:ID")
        object_type, instance_id = center_node.split(":", 1)
        tenant_types = self.instance_repository.types(tenant).get("types") or []
        allowed_types = {str(t.get("type") or "") for t in tenant_types}
        if allowed_types and object_type not in allowed_types:
            raise ValueError(f"center_node {center_node} is not an approved object type for tenant {tenant.tenant_id}")
        graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
        if not graph or not graph.get("approved"):
            raise ValueError(f"center_node {center_node} is outside the approved graph scope (node not found or not approved)")
        graph_type = graph.get("scope", {}).get("type") or object_type
        graph_url = (
            scope.get("graph_url")
            or graph.get("graph_url")
            or f"/graph.html?tenant={quote(tenant.tenant_id)}&type={quote(graph_type)}&id={quote(str(instance_id))}&depth={depth}&limit={limit}"
        )
        inner_scope = {
            "source": "question_center",
            "center_node": center_node,
            "depth": depth,
            "node_limit": limit,
            "edge_limit": limit,
            "allowed_node_types": graph.get("scope", {}).get("allowed_node_types") or [graph_type],
            "allowed_link_keys": graph.get("scope", {}).get("allowed_link_keys") or [],
            "approved_only": True,
            "evidence_paths": [
                {
                    "kind": "question_scope",
                    "label": center_node,
                    "summary": f"Question Center scoped task for: {question}",
                    "url": graph_url,
                    "source_ref": "question_center",
                    "payload": {"scope": scope.get("type") or "tenant", "center_node": center_node},
                }
            ],
        }
        language = payload.get("language") or scope.get("language")
        if language:
            inner_scope["language"] = language
        if scope.get("nonce"):
            inner_scope["nonce"] = scope["nonce"]
        inner_scope["question"] = question
        return self.create_scoped_task_from_graph(
            tenant,
            {
                "question": question,
                "source": "question_center",
                "graph_url": graph_url,
                "scope": inner_scope,
            },
        )

    def get_task(self, tenant, task_key):
        task = self._get_task_row(tenant, task_key)
        if task is None:
            return None
        latest_run = self.latest_run(tenant, task_key)
        findings = self.list_findings(tenant, task_key)
        for finding in findings:
            finding["task"] = task
            finding["run"] = latest_run or {}
            self._normalize_scoped_finding_display(tenant, finding)
        return {
            "tenant": tenant.public_dict(),
            "task": task,
            "latest_run": latest_run,
            "findings": findings,
        }

    def create_scoped_task_from_graph(self, tenant, payload):
        scope = payload.get("scope") or {}
        center_node = scope.get("center_node")
        center_edge = scope.get("center_edge")
        if not center_node and not center_edge:
            raise ValueError("center_node or center_edge is required")
        depth = max(1, min(int(scope.get("depth") or 1), 3))
        node_limit = max(1, min(int(scope.get("node_limit") or 100), 300))
        edge_limit = max(1, min(int(scope.get("edge_limit") or 100), 300))
        key_source = center_node or f"{center_edge.get('source')}->{center_edge.get('target')}"
        task_source = scope.get("source") or payload.get("source") or "graph_explorer"
        evidence_paths = scope.get("evidence_paths") or []
        evidence_kind = evidence_paths[0].get("kind") if evidence_paths else ("graph_edge" if center_edge else "graph_node")
        identity_parts = [tenant.tenant_id, task_source, evidence_kind, key_source, f"d{depth}", f"n{node_limit}", f"e{edge_limit}"]
        if task_source == "question_center":
            question_hash = hashlib.sha1((payload.get("question") or "").encode("utf-8")).hexdigest()[:10]
            identity_parts.append(f"q{question_hash}")
        nonce = scope.get("nonce") or payload.get("nonce")
        if nonce:
            identity_parts.append(f"r{nonce}")
        canonical_key = f"reasoning:graph-scope:{'-'.join(_slug(part) for part in identity_parts)}"
        question = payload.get("question") or (
            f"Explain the approved graph evidence around {key_source} and identify any workload, concentration, or provenance risk."
        )
        graph_scope = {}
        if center_node:
            if ":" not in center_node:
                raise ValueError("center_node must be in the form Type:Id")
            object_type, instance_id = center_node.split(":", 1)
            graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=node_limit)
            if not graph or not graph.get("approved"):
                raise ValueError(f"center_node {center_node} is outside the approved graph scope (node not found or not approved)")
            graph_scope = (graph or {}).get("scope") or {}
        if center_edge:
            source = center_edge.get("source")
            target = center_edge.get("target")
            if not source or not target or not self.instance_repository.edge_detail(tenant, source, target):
                raise ValueError("center_edge is outside the approved graph scope")
        task_scope = {
            "source": task_source,
            "tenant_id": tenant.tenant_id,
            "center_node": center_node,
            "center_edge": center_edge,
            "depth": depth,
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "allowed_node_types": scope.get("allowed_node_types") if scope.get("allowed_node_types") is not None else (graph_scope.get("allowed_node_types") or []),
            "allowed_link_keys": scope.get("allowed_link_keys") if scope.get("allowed_link_keys") is not None else (graph_scope.get("allowed_link_keys") or []),
            "approved_only": True,
            "evidence_paths": evidence_paths,
            "review_gate": "draft_only",
            "graph_url": payload.get("graph_url"),
            "question": question,
        }
        language = payload.get("language") or scope.get("language")
        if language:
            task_scope["language"] = language
        prior_findings = self.active_prior_findings(tenant, limit=5)
        if prior_findings:
            task_scope["prior_findings"] = prior_findings
            task_scope["evidence_paths"] = [*evidence_paths, *prior_findings]
        allowed_tools = ["graph_query", "instance_lookup", "edge_lookup", "artifact_lookup", "propose_finding", "propose_action"]
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_tasks
                    (project_id, canonical_key, question, scope_json, allowed_tools_json, status, created_at, updated_at)
                    VALUES (:tenant_id, :canonical_key, :question, :scope_json, :allowed_tools_json, 'active', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      question = EXCLUDED.question,
                      scope_json = EXCLUDED.scope_json,
                      allowed_tools_json = EXCLUDED.allowed_tools_json,
                      status = aletheia_reasoning_tasks.status,
                      updated_at = NOW()
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": canonical_key,
                    "question": question,
                    "scope_json": _json_dump(task_scope),
                    "allowed_tools_json": _json_dump(allowed_tools),
                },
            )
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
        task = self._task_to_dict(row)
        return {
            "tenant": tenant.public_dict(),
            "task": task,
            "reasoning_url": f"/reasoning.html?tenant={tenant.tenant_id}&task={canonical_key}",
        }

    def _get_task_row(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, question, scope_json, allowed_tools_json,
                           status, created_at, updated_at
                    FROM aletheia_reasoning_tasks
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": task_key},
            ).mappings().first()
        return self._task_to_dict(row) if row else None

    def update_task_status(self, tenant, task_key, new_status):
        valid = {"active", "completed", "closed"}
        if new_status not in valid:
            raise ValueError(f"Invalid task status: {new_status}; expected one of {valid}")
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE aletheia_reasoning_tasks
                    SET status = :new_status, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :task_key
                    RETURNING id, project_id, canonical_key, question, scope_json,
                              allowed_tools_json, status, created_at, updated_at
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key, "new_status": new_status},
            ).mappings().first()
        if not row:
            return None
        return self._task_to_dict(row)

    def delete_task(self, tenant, task_key):
        with self.metadata_engine_for(tenant).begin() as conn:
            task_row = conn.execute(
                text("SELECT id FROM aletheia_reasoning_tasks WHERE project_id = :tid AND canonical_key = :key"),
                {"tid": tenant.tenant_id, "key": task_key},
            ).mappings().first()
            if not task_row:
                return None
            wh = "t.project_id = :tid AND t.canonical_key = :key"
            params = {"tid": tenant.tenant_id, "key": task_key}
            self._delete_task_cascade(conn, wh, params)
            conn.execute(text("DELETE FROM aletheia_reasoning_tasks WHERE project_id = :tid AND canonical_key = :key"), params)
        return {"deleted": True, "canonical_key": task_key}

    def _delete_task_cascade(self, conn, where_clause, params):
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_reviews
            WHERE finding_id IN (
                SELECT f.id FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE {where_clause}
            )
        """), params)
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_findings
            WHERE run_id IN (
                SELECT r.id FROM aletheia_reasoning_runs r
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE {where_clause}
            )
        """), params)
        conn.execute(text(f"""
            DELETE FROM aletheia_reasoning_runs
            WHERE task_id IN (
                SELECT t.id FROM aletheia_reasoning_tasks t WHERE {where_clause}
            )
        """), params)

    def bulk_delete_closed_tasks(self, tenant):
        wh = "t.project_id = :tid AND t.status = 'closed'"
        wh_task = "project_id = :tid AND status = 'closed'"
        params = {"tid": tenant.tenant_id}
        with self.metadata_engine_for(tenant).begin() as conn:
            self._delete_task_cascade(conn, wh, params)
            result = conn.execute(text(f"DELETE FROM aletheia_reasoning_tasks WHERE {wh_task}"), params)
        return {"deleted_count": result.rowcount}

    def bulk_close_tasks(self, tenant, keys=None, before=None):
        conditions = ["project_id = :tenant_id", "status != 'closed'"]
        params = {"tenant_id": tenant.tenant_id}
        if keys:
            conditions.append("canonical_key IN :keys")
            params["keys"] = tuple(keys)
        if before:
            conditions.append("updated_at < :before")
            params["before"] = before
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).begin() as conn:
            result = conn.execute(
                text(f"UPDATE aletheia_reasoning_tasks SET status = 'closed', updated_at = NOW() WHERE {where}"),
                params,
            )
        return {"closed_count": result.rowcount}

    def run_task(self, tenant, task_key):
        return self.run_scoped_graph_task(tenant, task_key)

    def run_task_streaming(self, tenant, task_key):
        yield from self.run_scoped_graph_task_streaming(tenant, task_key)
