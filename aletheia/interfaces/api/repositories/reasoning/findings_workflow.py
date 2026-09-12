"""FindingsWorkflowMixin: the finding lifecycle -- listing findings for a task,
reading a single finding/finding-detail/run-overview, review_finding's
approve/reject/reaffirm/mark-stale/supersede transitions (and the task
auto-completion side effect), and the workspace-action + change-proposal +
revalidation-queue/batch-revalidate follow-on workflow built on top of an
approved finding.

Extracted from the monolithic reasoning.py -- part of the reasoning/ package
split. No behavior change.
"""

from sqlalchemy import text
from aletheia.interfaces.api.helpers import _json_dump, _load_json, _require_reason, _slug


class FindingsWorkflowMixin:
    def list_findings(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE f.project_id = :tenant_id AND t.canonical_key = :task_key
                    ORDER BY f.updated_at DESC, f.id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key},
            ).mappings().all()
        return [self._finding_to_dict(row) for row in rows]

    def latest_run(self, tenant, task_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id AND t.canonical_key = :task_key
                    ORDER BY r.created_at DESC, r.id DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant.tenant_id, "task_key": task_key},
            ).mappings().first()
        return self._run_to_dict(row) if row else None

    def review_finding(self, tenant, canonical_key, status, reviewer, reason):
        status_aliases = {
            "needs_changes": "needs_more_evidence",
            "needs-evidence": "needs_more_evidence",
            "needs-more-evidence": "needs_more_evidence",
            "reject": "rejected",
            "approve": "approved",
            "mark-stale": "stale",
            "supersede": "superseded",
            "reaffirm": "reaffirmed",
        }
        decision = status_aliases.get(status, status)
        _require_reason(status, reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            finding = conn.execute(
                text(
                    """
                    SELECT id, project_id, canonical_key, status, version
                    FROM aletheia_reasoning_findings
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
            if not finding:
                raise KeyError(canonical_key)
            before_status = finding["status"]
            before_version = finding["version"]
            after_version = before_version if decision == "comment" else before_version + 1
            after_status = before_status if decision == "comment" else "approved" if decision == "reaffirmed" else decision
            if decision != "comment":
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_reasoning_findings
                        SET status = :status, version = version + 1, updated_at = NOW()
                        WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key, "status": after_status},
                )
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_reviews
                    (finding_id, project_id, canonical_key, decision, reviewer, reason,
                     before_status, after_status, before_version, after_version, created_at)
                    VALUES
                    (:finding_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                     :before_status, :after_status, :before_version, :after_version, NOW())
                    """
                ),
                {
                    "finding_id": finding["id"],
                    "project_id": finding["project_id"],
                    "canonical_key": finding["canonical_key"],
                    "decision": decision,
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": before_status,
                    "after_status": after_status,
                    "before_version": before_version,
                    "after_version": after_version,
                },
            )
            if after_status in ("approved", "rejected"):
                self._maybe_complete_task(conn, tenant.tenant_id, finding["id"])
        return self.get_finding(tenant, canonical_key)

    def _maybe_complete_task(self, conn, tenant_id, finding_id):
        row = conn.execute(
            text(
                """
                SELECT t.id AS task_id, t.status AS task_status
                FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                WHERE f.id = :finding_id AND f.project_id = :tenant_id
                """
            ),
            {"finding_id": finding_id, "tenant_id": tenant_id},
        ).mappings().first()
        if not row or row["task_status"] != "active":
            return
        task_id = row["task_id"]
        counts = conn.execute(
            text(
                """
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE f.status NOT IN ('approved', 'rejected')) AS pending
                FROM aletheia_reasoning_findings f
                JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                WHERE r.task_id = :task_id AND f.project_id = :tenant_id
                """
            ),
            {"task_id": task_id, "tenant_id": tenant_id},
        ).mappings().first()
        if counts["total"] > 0 and counts["pending"] == 0:
            conn.execute(
                text("UPDATE aletheia_reasoning_tasks SET status = 'completed', updated_at = NOW() WHERE id = :task_id"),
                {"task_id": task_id},
            )

    def get_finding(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, run_id, project_id, canonical_key, title, conclusion, confidence,
                           supporting_evidence_json, counter_evidence_json, recommended_action_json,
                           status, version, source_agent, created_at, updated_at
                    FROM aletheia_reasoning_findings
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
            if not row:
                return None
            context = conn.execute(
                text(
                    """
                    SELECT t.canonical_key AS task_key, t.question, t.scope_json,
                           r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id AND r.id = :run_id
                    """
                ),
                {"tenant_id": tenant.tenant_id, "run_id": row["run_id"]},
            ).mappings().first()
            reviews = conn.execute(
                text(
                    """
                    SELECT decision, reviewer, reason, before_status, after_status,
                           before_version, after_version, created_at
                    FROM aletheia_reasoning_reviews
                    WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                    ORDER BY created_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().all()
        finding = self._finding_to_dict(row)
        if context:
            finding["task"] = {
                "canonical_key": context["task_key"],
                "question": context["question"],
                "scope": _load_json(context["scope_json"], {}),
            }
            finding["run"] = self._run_to_dict(context)
            self._normalize_scoped_finding_display(tenant, finding)
        finding["reviews"] = [dict(review) for review in reviews]
        return finding

    def finding_detail(self, tenant, canonical_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at,
                           t.canonical_key AS task_key, t.question, t.scope_json,
                           r.run_key, r.agent_name, r.prompt_version, r.query_plan_json,
                           r.tool_calls_json, r.evidence_paths_json, r.output_json,
                           r.eval_result_json, r.status AS run_status, r.latency_ms,
                           r.cost_estimate, r.created_at AS run_created_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE f.project_id = :tenant_id AND f.canonical_key = :canonical_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
            ).mappings().first()
        if not row:
            return None
        finding = self._finding_to_dict(row)
        finding["task"] = {
            "canonical_key": row["task_key"],
            "question": row["question"],
            "scope": _load_json(row["scope_json"], {}),
        }
        finding["run"] = self._run_to_dict(
            {
                "id": row["run_id"],
                "project_id": row["project_id"],
                "run_key": row["run_key"],
                "agent_name": row["agent_name"],
                "prompt_version": row["prompt_version"],
                "query_plan_json": row["query_plan_json"],
                "tool_calls_json": row["tool_calls_json"],
                "evidence_paths_json": row["evidence_paths_json"],
                "output_json": row["output_json"],
                "eval_result_json": row["eval_result_json"],
                "status": row["run_status"],
                "latency_ms": row["latency_ms"],
                "cost_estimate": row["cost_estimate"],
                "created_at": row["run_created_at"],
            }
        )
        self._normalize_scoped_finding_display(tenant, finding)
        return finding

    def list_runs_overview(self, tenant, limit=30):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT r.id, r.project_id, r.run_key, r.agent_name, r.prompt_version,
                           r.query_plan_json, r.tool_calls_json, r.evidence_paths_json,
                           r.output_json, r.eval_result_json, r.status, r.latency_ms,
                           r.cost_estimate, r.created_at,
                           t.canonical_key AS task_key, t.question, t.scope_json
                    FROM aletheia_reasoning_runs r
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE r.project_id = :tenant_id
                    ORDER BY r.created_at DESC, r.id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": limit},
            ).mappings().all()
        runs = []
        for row in rows:
            run = self._run_to_dict(row)
            run["task_key"] = row["task_key"]
            run["question"] = row["question"]
            run["task_scope"] = _load_json(row["scope_json"], {})
            runs.append(run)
        return runs

    def finding_workspace_action(self, tenant, canonical_key, payload=None):
        self.ensure_finding_experience_schema(tenant)
        payload = payload or {}
        finding = self.get_finding(tenant, canonical_key)
        if not finding:
            raise KeyError(canonical_key)
        if finding.get("status") not in self.ACTIVE_FINDING_STATUSES:
            raise ValueError("workspace action can only be created from active approved/reaffirmed findings")
        recommended = finding.get("recommended_action") or {}
        action = recommended.get("workspace_next_action") or {
            "type": "case_next_action",
            "label": "Review approved finding and assign owner",
            "status": "ready_for_dispatch",
            "writes_canonical": False,
        }
        title = payload.get("title") or action.get("label") or action.get("title") or "Review approved finding"
        action_type = payload.get("action_type") or action.get("action_type") or "investigate"
        priority = payload.get("priority") or action.get("priority") or "medium"
        owner = payload.get("owner") or action.get("owner")
        due_at = payload.get("due_at") or action.get("due_at")
        action_key = payload.get("action_key") or f"action:{_slug(canonical_key)}:{_slug(title)}"
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_finding_actions
                    (project_id, action_key, finding_key, title, action_type, owner, due_at,
                     priority, status, created_from, canonical_write, graph_write, created_at, updated_at)
                    VALUES
                    (:tenant_id, :action_key, :finding_key, :title, :action_type, :owner,
                     CAST(:due_at AS TIMESTAMP), :priority, 'open', 'approved_finding', FALSE, FALSE, NOW(), NOW())
                    ON CONFLICT (project_id, action_key) DO UPDATE SET
                      title = EXCLUDED.title,
                      action_type = EXCLUDED.action_type,
                      owner = EXCLUDED.owner,
                      due_at = EXCLUDED.due_at,
                      priority = EXCLUDED.priority,
                      updated_at = NOW()
                    RETURNING id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                              priority, status, result, result_detail, created_from, canonical_write,
                              graph_write, created_at, updated_at, closed_at
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "action_key": action_key,
                    "finding_key": canonical_key,
                    "title": title,
                    "action_type": action_type,
                    "owner": owner,
                    "due_at": due_at,
                    "priority": priority,
                },
            ).mappings().first()
            self._append_finding_usage_review(
                conn,
                tenant,
                canonical_key,
                decision="action_created",
                reviewer=payload.get("reviewer") or "Itachi",
                reason=f"Workspace action created: {title}",
            )
        return {
            "tenant": tenant.public_dict(),
            "finding_key": canonical_key,
            "workspace_next_action": self._finding_action_to_dict(row),
        }

    def update_finding_action(self, tenant, action_key, action, payload=None):
        self.ensure_finding_experience_schema(tenant)
        payload = payload or {}
        valid_transitions = {
            "start": {"open": "in_progress", "reopened": "in_progress", "blocked": "in_progress"},
            "block": {"open": "blocked", "in_progress": "blocked"},
            "close": {"in_progress": "closed"},
            "reopen": {"closed": "reopened"},
            "update": {},
        }
        close_results = {"confirmed_risk", "false_positive", "evidence_added", "proposal_created", "no_action_needed", "rerun_scheduled"}
        with self.metadata_engine_for(tenant).begin() as conn:
            current = conn.execute(
                text(
                    """
                    SELECT id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                           priority, status, result, result_detail, created_from, canonical_write,
                           graph_write, created_at, updated_at, closed_at
                    FROM aletheia_finding_actions
                    WHERE project_id = :tenant_id AND action_key = :action_key
                    FOR UPDATE
                    """
                ),
                {"tenant_id": tenant.tenant_id, "action_key": action_key},
            ).mappings().first()
            if not current:
                raise KeyError(action_key)
            before_status = current["status"]
            new_status = before_status
            result = payload.get("result") if "result" in payload else current["result"]
            result_detail = payload.get("result_detail") if "result_detail" in payload else current["result_detail"]
            if action == "update":
                pass
            else:
                transition = valid_transitions.get(action)
                if transition is None or before_status not in transition:
                    raise ValueError(f"Invalid action transition: {before_status} -> {action}")
                new_status = transition[before_status]
            if new_status == "closed":
                if not result:
                    raise ValueError("closing an action requires result")
                if result not in close_results:
                    raise ValueError(f"invalid close result: {result}")
            closed_at_expr = "NOW()" if new_status == "closed" else "NULL" if action == "reopen" else "closed_at"
            row = conn.execute(
                text(
                    f"""
                    UPDATE aletheia_finding_actions
                    SET title = COALESCE(:title, title),
                        action_type = COALESCE(:action_type, action_type),
                        owner = COALESCE(:owner, owner),
                        due_at = COALESCE(CAST(:due_at AS TIMESTAMP), due_at),
                        priority = COALESCE(:priority, priority),
                        status = :status,
                        result = :result,
                        result_detail = :result_detail,
                        closed_at = {closed_at_expr},
                        updated_at = NOW()
                    WHERE project_id = :tenant_id AND action_key = :action_key
                    RETURNING id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                              priority, status, result, result_detail, created_from, canonical_write,
                              graph_write, created_at, updated_at, closed_at
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "action_key": action_key,
                    "title": payload.get("title"),
                    "action_type": payload.get("action_type"),
                    "owner": payload.get("owner"),
                    "due_at": payload.get("due_at"),
                    "priority": payload.get("priority"),
                    "status": new_status,
                    "result": result,
                    "result_detail": result_detail,
                },
            ).mappings().first()
            decision = f"action_{action}"
            reason = payload.get("reason") or f"Workspace action {action}: {action_key}"
            self._append_finding_usage_review(
                conn,
                tenant,
                current["finding_key"],
                decision=decision,
                reviewer=payload.get("reviewer") or "Itachi",
                reason=reason,
            )
        return {
            "tenant": tenant.public_dict(),
            "workspace_next_action": self._finding_action_to_dict(row),
            "finding_status_unchanged": True,
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _append_finding_usage_review(self, conn, tenant, canonical_key, decision, reviewer, reason):
        finding = conn.execute(
            text(
                """
                SELECT id, status, version
                FROM aletheia_reasoning_findings
                WHERE project_id = :tenant_id AND canonical_key = :canonical_key
                """
            ),
            {"tenant_id": tenant.tenant_id, "canonical_key": canonical_key},
        ).mappings().first()
        if not finding:
            raise KeyError(canonical_key)
        conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_reviews
                (finding_id, project_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version, created_at)
                VALUES
                (:finding_id, :project_id, :canonical_key, :decision, :reviewer, :reason,
                 :status, :status, :version, :version, NOW())
                """
            ),
            {
                "finding_id": finding["id"],
                "project_id": tenant.tenant_id,
                "canonical_key": canonical_key,
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "status": finding["status"],
                "version": finding["version"],
            },
        )

    def finding_change_proposal(self, tenant, canonical_key, payload=None):
        finding = self.get_finding(tenant, canonical_key)
        if not finding:
            raise KeyError(canonical_key)
        if finding.get("status") not in self.ACTIVE_FINDING_STATUSES:
            raise ValueError("change proposal can only be drafted from active approved/reaffirmed findings")
        proposal_type = (payload or {}).get("proposal_type") or "ontology_rule"
        return {
            "tenant": tenant.public_dict(),
            "finding_key": canonical_key,
            "proposal": {
                "proposal_key": f"proposal:{proposal_type}:{_slug(canonical_key)}",
                "proposal_type": proposal_type,
                "status": "proposal_draft",
                "source_finding_key": canonical_key,
                "summary": finding.get("conclusion"),
                "writes_canonical": False,
                "requires_governance_review": True,
                "boundary": self._finding_canonical_boundary(),
            },
        }

    def finding_revalidation_queue(self, tenant, status=None, limit=50):
        self.ensure_finding_experience_schema(tenant)
        findings = self.list_findings_registry(tenant, context=None, limit=limit, filters={"sort": "oldest_unrevalidated"}).get("findings", [])
        queue = []
        for finding in findings:
            if status and finding.get("status") != status:
                continue
            if finding.get("status") not in {"approved", "reaffirmed", "stale", "superseded"}:
                continue
            latest = finding.get("latest_review") or {}
            actions = finding.get("actions") or []
            reason = "aging_threshold"
            if finding.get("status") == "stale":
                reason = "already_stale"
            elif finding.get("status") == "superseded":
                reason = "superseded_audit"
            elif any(action.get("is_overdue") for action in actions):
                reason = "action_overdue"
            elif latest.get("decision") == "reaffirmed":
                reason = "reaffirmed_recently"
            queue.append({
                "finding_key": finding["canonical_key"],
                "title": finding["title"],
                "status": finding["status"],
                "reason": reason,
                "last_review": latest,
                "last_reaffirmed_at": latest.get("created_at") if latest.get("decision") == "reaffirmed" else None,
                "action_summary": finding.get("action_summary"),
                "affected_downstream": {
                    "actions": len(actions),
                    "reasoning_context": finding.get("status") in self.ACTIVE_FINDING_STATUSES,
                },
                "suggested_batch_operation": "reaffirm" if finding.get("status") in self.ACTIVE_FINDING_STATUSES else "mark_stale",
                "canonical_write": False,
                "graph_write": False,
            })
        return {"tenant": tenant.public_dict(), "queue": queue[:limit], "canonical_boundary": self._finding_canonical_boundary()}

    def batch_revalidate_findings(self, tenant, payload):
        self.ensure_finding_experience_schema(tenant)
        keys = payload.get("finding_keys") or []
        action = payload.get("action") or "reaffirm"
        reviewer = payload.get("reviewer") or "Itachi"
        reason = payload.get("reason") or f"batch revalidation: {action}"
        owner = payload.get("owner")
        if not keys:
            raise ValueError("finding_keys is required")
        if action not in {"reaffirm", "mark_stale", "assign_owner"}:
            raise ValueError("batch action must be reaffirm, mark_stale, or assign_owner")
        results = []
        for key in keys:
            if action == "reaffirm":
                finding = self.review_finding(tenant, key, "reaffirmed", reviewer, reason)
                results.append({"finding_key": key, "status": finding.get("status"), "decision": "reaffirmed"})
            elif action == "mark_stale":
                finding = self.review_finding(tenant, key, "stale", reviewer, reason)
                results.append({"finding_key": key, "status": finding.get("status"), "decision": "stale"})
            else:
                if not owner:
                    raise ValueError("owner is required for assign_owner")
                action_result = self.finding_workspace_action(
                    tenant,
                    key,
                    {
                        "title": "Revalidate approved finding",
                        "action_type": "rerun_autopilot",
                        "owner": owner,
                        "priority": payload.get("priority") or "medium",
                        "due_at": payload.get("due_at"),
                        "reviewer": reviewer,
                    },
                )
                results.append({"finding_key": key, "decision": "assign_owner", "workspace_next_action": action_result["workspace_next_action"]})
        return {
            "tenant": tenant.public_dict(),
            "action": action,
            "results": results,
            "canonical_boundary": self._finding_canonical_boundary(),
        }
