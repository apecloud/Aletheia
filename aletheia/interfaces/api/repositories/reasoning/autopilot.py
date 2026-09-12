"""AutopilotMixin: Autopilot session/hypothesis/candidate-finding CRUD, and the
human review gate for Autopilot candidate findings (review_autopilot_candidate),
which promotes an approved candidate into a formal reasoning task/run/finding.

review_autopilot_candidate is decomposed (pure extraction, no behavior change)
into a slim dispatcher plus one branch method per decision:
_review_autopilot_comment, _review_autopilot_reject, _review_autopilot_needs_evidence,
_review_autopilot_approve. The dispatcher still owns the transaction boundary --
_review_autopilot_approve only performs writes and hands back the new finding's
canonical_key; the dispatcher reads it back with self.get_finding() only after
the `with ... begin()` block has committed, exactly like the original method
(a get_finding() call issued through a second connection while the insert
transaction is still open would not see the uncommitted row under Postgres'
default READ COMMITTED isolation).

Extracted from the monolithic reasoning.py -- part of the reasoning/ package
split. No behavior change beyond this documented, deliberate decomposition.
"""

import time
from sqlalchemy import text
from aletheia.reasoning.finding_framework import (
    DEEP_GRAPH_REQUIRED_STEPS as REASONING_DEEP_GRAPH_REQUIRED_STEPS,
    deep_graph_profile,
    finding_canonical_boundary,
)
from aletheia.interfaces.api.helpers import _json_dump, _require_reason, _slug


class AutopilotMixin:
    def create_autopilot_session(self, tenant, payload):
        objective = (payload.get("objective") or "").strip()
        if not objective:
            raise ValueError("objective is required")
        self.ensure_autopilot_schema(tenant)
        scope = payload.get("scope") or {}
        budget = self._autopilot_budget(payload.get("budget") or {})
        safety = self._autopilot_safety_profile(payload.get("safety_profile") or payload.get("safety") or {})
        nonce = payload.get("nonce") or int(time.time() * 1000)
        session_key = payload.get("session_key") or f"autopilot:{tenant.tenant_id}:{_slug(objective)}:{nonce}"
        created_by = payload.get("created_by") or "Autopilot"
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_sessions
                    (project_id, session_key, objective, scope_json, budget_json, safety_profile_json,
                     status, created_by, created_at, updated_at)
                    VALUES
                    (:tenant_id, :session_key, :objective, :scope_json, :budget_json, :safety_profile_json,
                     'draft', :created_by, NOW(), NOW())
                    ON CONFLICT (project_id, session_key) DO UPDATE SET
                      objective = EXCLUDED.objective,
                      scope_json = EXCLUDED.scope_json,
                      budget_json = EXCLUDED.budget_json,
                      safety_profile_json = EXCLUDED.safety_profile_json,
                      status = aletheia_autopilot_sessions.status,
                      updated_at = NOW()
                    RETURNING id, project_id, session_key, objective, scope_json, budget_json,
                              safety_profile_json, status, created_by, created_at, updated_at
                """),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "objective": objective,
                    "scope_json": _json_dump(scope),
                    "budget_json": _json_dump(budget),
                    "safety_profile_json": _json_dump(safety),
                    "created_by": created_by,
                },
            ).mappings().first()
        for item in payload.get("hypotheses") or []:
            self.add_autopilot_hypothesis(tenant, session_key, item)
        for item in payload.get("candidate_findings") or []:
            self.add_autopilot_candidate_finding(tenant, session_key, item)
        return self.get_autopilot_session(tenant, session_key)

    def run_creditcardfraud_playbook(self, tenant, payload):
        """Start (or resume, via session_key upsert -- see create_autopilot_session's
        ON CONFLICT DO UPDATE) an Autopilot session pre-seeded with the
        creditcardfraud tenant's fixed scope/safety profile, so the UI's "Run
        creditcardfraud playbook" button doesn't need to duplicate that
        scope/safety wiring itself. Reuses create_autopilot_session rather
        than reimplementing session creation."""
        objective = (payload.get("objective") or "").strip() or "Discover high-value credit card fraud risk findings"
        return self.create_autopilot_session(tenant, {
            "objective": objective,
            "session_key": payload.get("session_key") or None,
            "scope": {
                "tenant": tenant.tenant_id,
                "approved_only": True,
                "source_surface": "creditcardfraud_playbook",
                "table": "credit_card_transactions_safe",
            },
            "budget": payload.get("budget") or {},
            "safety_profile": {
                "approved_only": True,
                "safe_views_only": True,
                "allow_sensitive_fields": False,
                "blocked_fields": ["card_verification_code_fields"],
            },
            "created_by": "Creditcardfraud Playbook",
        })

    def list_autopilot_sessions(self, tenant, status=None, limit=50):
        self.ensure_autopilot_schema(tenant)
        conditions = ["project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id, "limit": max(1, min(int(limit or 50), 100))}
        if status:
            conditions.append("status = :status")
            params["status"] = status
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(f"""
                    SELECT id, project_id, session_key, objective, scope_json, budget_json,
                           safety_profile_json, status, created_by, created_at, updated_at
                    FROM aletheia_autopilot_sessions
                    WHERE {where}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT :limit
                """),
                params,
            ).mappings().all()
        return {"tenant": tenant.public_dict(), "sessions": [self._autopilot_session_to_dict(row) for row in rows]}

    def get_autopilot_session(self, tenant, session_key):
        self.ensure_autopilot_schema(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            session = conn.execute(
                text("""
                    SELECT id, project_id, session_key, objective, scope_json, budget_json,
                           safety_profile_json, status, created_by, created_at, updated_at
                    FROM aletheia_autopilot_sessions
                    WHERE project_id = :tenant_id AND session_key = :session_key
                """),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()
            if not session:
                return None
            hypotheses = conn.execute(
                text("""
                    SELECT id, session_id, project_id, hypothesis_key, title, rationale, status,
                           priority, evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                           created_at, updated_at
                    FROM aletheia_autopilot_hypotheses
                    WHERE project_id = :tenant_id AND session_id = :session_id
                    ORDER BY priority ASC, id ASC
                """),
                {"tenant_id": tenant.tenant_id, "session_id": session["id"]},
            ).mappings().all()
            candidates = conn.execute(
                text("""
                    SELECT id, session_id, hypothesis_id, project_id, canonical_key, title, conclusion,
                           value_score, confidence, novelty_score, impact_score, evidence_chain_json,
                           evidence_limits_json, suggested_action_json, status, created_at, updated_at
                    FROM aletheia_autopilot_candidate_findings
                    WHERE project_id = :tenant_id AND session_id = :session_id
                    ORDER BY value_score DESC, confidence DESC, id ASC
                """),
                {"tenant_id": tenant.tenant_id, "session_id": session["id"]},
            ).mappings().all()
        return {
            "tenant": tenant.public_dict(),
            "session": self._autopilot_session_to_dict(session),
            "hypotheses": [self._autopilot_hypothesis_to_dict(row) for row in hypotheses],
            "candidate_findings": [self._autopilot_candidate_to_dict(row) for row in candidates],
        }

    def add_autopilot_hypothesis(self, tenant, session_key, payload):
        self.ensure_autopilot_schema(tenant)
        session = self._autopilot_session_row(tenant, session_key)
        if not session:
            raise KeyError(session_key)
        title = (payload.get("title") or "").strip()
        if not title:
            raise ValueError("hypothesis title is required")
        hypothesis_key = payload.get("hypothesis_key") or f"{session_key}:hypothesis:{_slug(title)}"
        status = payload.get("status") or "queued"
        if status not in {"queued", "running", "completed", "pruned"}:
            raise ValueError("hypothesis status must be queued, running, completed, or pruned")
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_hypotheses
                    (session_id, project_id, hypothesis_key, title, rationale, status, priority,
                     evidence_plan_json, reasoning_task_keys_json, pruned_reason, created_at, updated_at)
                    VALUES
                    (:session_id, :tenant_id, :hypothesis_key, :title, :rationale, :status, :priority,
                     :evidence_plan_json, :reasoning_task_keys_json, :pruned_reason, NOW(), NOW())
                    ON CONFLICT (project_id, hypothesis_key) DO UPDATE SET
                      title = EXCLUDED.title,
                      rationale = EXCLUDED.rationale,
                      status = EXCLUDED.status,
                      priority = EXCLUDED.priority,
                      evidence_plan_json = EXCLUDED.evidence_plan_json,
                      reasoning_task_keys_json = EXCLUDED.reasoning_task_keys_json,
                      pruned_reason = EXCLUDED.pruned_reason,
                      updated_at = NOW()
                    RETURNING id, session_id, project_id, hypothesis_key, title, rationale, status,
                              priority, evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                              created_at, updated_at
                """),
                {
                    "session_id": session["id"],
                    "tenant_id": tenant.tenant_id,
                    "hypothesis_key": hypothesis_key,
                    "title": title,
                    "rationale": payload.get("rationale"),
                    "status": status,
                    "priority": int(payload.get("priority") or 100),
                    "evidence_plan_json": _json_dump(payload.get("evidence_plan") or []),
                    "reasoning_task_keys_json": _json_dump(payload.get("reasoning_task_keys") or []),
                    "pruned_reason": payload.get("pruned_reason"),
                },
            ).mappings().first()
        return {"tenant": tenant.public_dict(), "hypothesis": self._autopilot_hypothesis_to_dict(row)}

    def add_autopilot_candidate_finding(self, tenant, session_key, payload):
        self.ensure_autopilot_schema(tenant)
        session = self._autopilot_session_row(tenant, session_key)
        if not session:
            raise KeyError(session_key)
        title = (payload.get("title") or "").strip()
        conclusion = (payload.get("conclusion") or "").strip()
        if not title or not conclusion:
            raise ValueError("candidate finding title and conclusion are required")
        canonical_key = payload.get("canonical_key") or f"candidate:autopilot:{_slug(session_key)}:{_slug(title)}"
        status = payload.get("status") or "draft"
        if status not in {"draft", "needs_more_evidence", "rejected", "promoted"}:
            raise ValueError("candidate finding status must be draft, needs_more_evidence, rejected, or promoted")
        if status == "promoted":
            raise ValueError("candidate findings cannot be auto-promoted by the Autopilot API")
        hypothesis_id = payload.get("hypothesis_id")
        hypothesis_key = payload.get("hypothesis_key")
        if hypothesis_key and not hypothesis_id:
            hypothesis = self._autopilot_hypothesis_row(tenant, hypothesis_key)
            hypothesis_id = hypothesis["id"] if hypothesis else None
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text("""
                    INSERT INTO aletheia_autopilot_candidate_findings
                    (session_id, hypothesis_id, project_id, canonical_key, title, conclusion,
                     value_score, confidence, novelty_score, impact_score, evidence_chain_json,
                     evidence_limits_json, suggested_action_json, status, created_at, updated_at)
                    VALUES
                    (:session_id, :hypothesis_id, :tenant_id, :canonical_key, :title, :conclusion,
                     :value_score, :confidence, :novelty_score, :impact_score, :evidence_chain_json,
                     :evidence_limits_json, :suggested_action_json, :status, NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      hypothesis_id = EXCLUDED.hypothesis_id,
                      title = EXCLUDED.title,
                      conclusion = EXCLUDED.conclusion,
                      value_score = EXCLUDED.value_score,
                      confidence = EXCLUDED.confidence,
                      novelty_score = EXCLUDED.novelty_score,
                      impact_score = EXCLUDED.impact_score,
                      evidence_chain_json = EXCLUDED.evidence_chain_json,
                      evidence_limits_json = EXCLUDED.evidence_limits_json,
                      suggested_action_json = EXCLUDED.suggested_action_json,
                      status = EXCLUDED.status,
                      updated_at = NOW()
                    RETURNING id, session_id, hypothesis_id, project_id, canonical_key, title,
                              conclusion, value_score, confidence, novelty_score, impact_score,
                              evidence_chain_json, evidence_limits_json, suggested_action_json,
                              status, created_at, updated_at
                """),
                {
                    "session_id": session["id"],
                    "hypothesis_id": hypothesis_id,
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": canonical_key,
                    "title": title,
                    "conclusion": conclusion,
                    "value_score": float(payload.get("value_score") or 0),
                    "confidence": float(payload.get("confidence") or 0),
                    "novelty_score": float(payload.get("novelty_score") or 0),
                    "impact_score": float(payload.get("impact_score") or 0),
                    "evidence_chain_json": _json_dump(payload.get("evidence_chain") or []),
                    "evidence_limits_json": _json_dump(payload.get("evidence_limits") or []),
                    "suggested_action_json": _json_dump(payload.get("suggested_action") or {}),
                    "status": status,
                },
            ).mappings().first()
        return {"tenant": tenant.public_dict(), "candidate_finding": self._autopilot_candidate_to_dict(row)}

    def review_autopilot_candidate(self, tenant, candidate_key, action, reviewer, reason):
        self.ensure_autopilot_schema(tenant)
        decision_aliases = {
            "approve": "approved",
            "reject": "rejected",
            "needs-evidence": "needs_more_evidence",
            "needs-more-evidence": "needs_more_evidence",
            "needs_more_evidence": "needs_more_evidence",
            "comment": "comment",
        }
        decision = decision_aliases.get(action, action)
        if decision not in {"approved", "rejected", "needs_more_evidence", "comment"}:
            raise ValueError(f"Unsupported candidate review action: {action}")
        if decision != "approved":
            _require_reason(decision, reason or "")
        with self.metadata_engine_for(tenant).begin() as conn:
            candidate = conn.execute(
                text(
                    """
                    SELECT c.*, s.session_key, s.objective, h.hypothesis_key
                    FROM aletheia_autopilot_candidate_findings c
                    JOIN aletheia_autopilot_sessions s ON c.session_id = s.id
                    LEFT JOIN aletheia_autopilot_hypotheses h ON c.hypothesis_id = h.id
                    WHERE c.project_id = :tenant_id AND c.canonical_key = :candidate_key
                    FOR UPDATE OF c
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key},
            ).mappings().first()
            if not candidate:
                raise KeyError(candidate_key)
            candidate_dict = self._autopilot_candidate_to_dict(candidate)
            before_status = candidate_dict["status"]
            if decision == "comment":
                return self._review_autopilot_comment(conn, tenant, candidate_key, candidate, candidate_dict, decision, reviewer, reason)
            conn.execute(
                text(
                    """
                    UPDATE aletheia_autopilot_candidate_findings
                    SET status = :status, updated_at = NOW()
                    WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key, "status": decision},
            )
            if decision == "rejected":
                return self._review_autopilot_reject(conn, tenant, candidate_key, candidate, decision, before_status, reviewer, reason)
            if decision == "needs_more_evidence":
                return self._review_autopilot_needs_evidence(conn, tenant, candidate_key, candidate, decision, before_status, reviewer, reason)
            formal_key = self._review_autopilot_approve(conn, tenant, candidate_key, candidate, candidate_dict, before_status, reviewer, reason)
        approved = self.get_finding(tenant, formal_key)
        return {
            "tenant": tenant.public_dict(),
            "candidate_finding": self.get_autopilot_candidate(tenant, candidate_key),
            "finding": self._decorate_approved_finding(approved),
            "registry_entry": {
                "finding_key": formal_key,
                "context_label": "prior_finding",
                "reasoning_label": "reviewed_inference",
                "active_context": True,
            },
            "workspace_next_action": approved.get("recommended_action", {}).get("workspace_next_action") if approved else None,
            "change_proposal_bridge": approved.get("recommended_action", {}).get("change_proposal_bridge") if approved else None,
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _review_autopilot_comment(self, conn, tenant, candidate_key, candidate, candidate_dict, decision, reviewer, reason):
        evidence_limits = list(candidate_dict.get("evidence_limits") or [])
        evidence_limits.append(f"Reviewer note by {reviewer}: {reason.strip()}")
        conn.execute(
            text(
                """
                UPDATE aletheia_autopilot_candidate_findings
                SET evidence_limits_json = :evidence_limits_json, updated_at = NOW()
                WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                """
            ),
            {
                "tenant_id": tenant.tenant_id,
                "candidate_key": candidate_key,
                "evidence_limits_json": _json_dump(evidence_limits),
            },
        )
        return {
            "tenant": tenant.public_dict(),
            "candidate_finding": self._autopilot_candidate_to_dict({
                **candidate,
                "evidence_limits_json": _json_dump(evidence_limits),
            }),
            "review": {"decision": decision, "reviewer": reviewer, "reason": reason},
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _review_autopilot_reject(self, conn, tenant, candidate_key, candidate, decision, before_status, reviewer, reason):
        return {
            "tenant": tenant.public_dict(),
            "candidate_finding": self._autopilot_candidate_to_dict({**candidate, "status": decision}),
            "review": {
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_status": decision,
            },
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _review_autopilot_needs_evidence(self, conn, tenant, candidate_key, candidate, decision, before_status, reviewer, reason):
        return {
            "tenant": tenant.public_dict(),
            "candidate_finding": self._autopilot_candidate_to_dict({**candidate, "status": decision}),
            "review": {
                "decision": decision,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_status": decision,
            },
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _review_autopilot_approve(self, conn, tenant, candidate_key, candidate, candidate_dict, before_status, reviewer, reason):
        evidence_chain = candidate_dict.get("evidence_chain") or []
        if not evidence_chain:
            raise ValueError("approved candidate requires evidence_chain")
        formal_key = f"finding:approved:{_slug(candidate_key)}"
        task_key = f"reasoning:approved-finding:{_slug(candidate_key)}"
        run_key = f"{task_key}:run:{int(time.time() * 1000)}"
        now_scope = {
            "source": "autopilot_candidate_review_gate",
            "tenant_id": tenant.tenant_id,
            "candidate_key": candidate_key,
            "autopilot_session_key": candidate["session_key"],
            "hypothesis_key": candidate.get("hypothesis_key"),
            "approved_only": True,
            "review_gate": "human_finding_approval",
            "canonical_writes": False,
            "graph_writes": False,
        }
        conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_tasks
                (project_id, canonical_key, question, scope_json, allowed_tools_json, status, created_at, updated_at)
                VALUES (:tenant_id, :task_key, :question, :scope_json, :allowed_tools_json, 'completed', NOW(), NOW())
                ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                  question = EXCLUDED.question,
                  scope_json = EXCLUDED.scope_json,
                  allowed_tools_json = EXCLUDED.allowed_tools_json,
                  status = 'completed',
                  updated_at = NOW()
                RETURNING id
                """
            ),
            {
                "tenant_id": tenant.tenant_id,
                "task_key": task_key,
                "question": f"Human-approved Autopilot finding: {candidate_dict['title']}",
                "scope_json": _json_dump(now_scope),
                "allowed_tools_json": _json_dump(["prior_finding_registry", "propose_action", "propose_change_proposal"]),
            },
        )
        task = conn.execute(
            text("SELECT id FROM aletheia_reasoning_tasks WHERE project_id = :tenant_id AND canonical_key = :task_key"),
            {"tenant_id": tenant.tenant_id, "task_key": task_key},
        ).mappings().first()
        run = conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_runs
                (task_id, project_id, run_key, agent_name, prompt_version,
                 query_plan_json, tool_calls_json, evidence_paths_json,
                 output_json, eval_result_json, status, latency_ms, cost_estimate, created_at)
                VALUES
                (:task_id, :tenant_id, :run_key, 'FindingApprovalReviewGate', 'finding-approval-v1',
                 :query_plan_json, :tool_calls_json, :evidence_paths_json,
                 :output_json, :eval_result_json, 'completed', 0, 0.0, NOW())
                RETURNING id, project_id, run_key, agent_name, prompt_version,
                          query_plan_json, tool_calls_json, evidence_paths_json,
                          output_json, eval_result_json, status, latency_ms, cost_estimate, created_at
                """
            ),
            {
                "task_id": task["id"],
                "tenant_id": tenant.tenant_id,
                "run_key": run_key,
                "query_plan_json": _json_dump([
                    {"step": "review_candidate", "boundary": "human review gate"},
                    {"step": "register_approved_finding", "writes_canonical": False},
                ]),
                "tool_calls_json": _json_dump([
                    {"tool": "autopilot_candidate_read", "source_ref": candidate_key, "safe_view_only": True},
                    {"tool": "finding_registry_write", "status": "approved", "canonical_write": False},
                ]),
                "evidence_paths_json": _json_dump([
                    *evidence_chain,
                    {
                        "kind": "autopilot_candidate",
                        "label": "Reviewed Autopilot candidate",
                        "source_ref": candidate_key,
                        "payload": {
                            "session_key": candidate["session_key"],
                            "hypothesis_key": candidate.get("hypothesis_key"),
                        },
                    },
                ]),
                "output_json": _json_dump({
                    "answer": candidate_dict["conclusion"],
                    "reviewed_inference": True,
                    "prior_finding": formal_key,
                }),
                "eval_result_json": _json_dump({"passed": True, "checks": ["evidence_chain_present", "human_review_present", "canonical_write_disabled"]}),
            },
        ).mappings().first()
        recommended_action = self._approved_finding_action(candidate_dict, candidate, reason)
        finding = conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_findings
                (run_id, project_id, canonical_key, title, conclusion, confidence,
                 supporting_evidence_json, counter_evidence_json, recommended_action_json,
                 status, version, source_agent, created_at, updated_at)
                VALUES
                (:run_id, :tenant_id, :canonical_key, :title, :conclusion, :confidence,
                 :supporting_evidence_json, :counter_evidence_json, :recommended_action_json,
                 'approved', 1, 'FindingApprovalReviewGate', NOW(), NOW())
                ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                  run_id = EXCLUDED.run_id,
                  title = EXCLUDED.title,
                  conclusion = EXCLUDED.conclusion,
                  confidence = EXCLUDED.confidence,
                  supporting_evidence_json = EXCLUDED.supporting_evidence_json,
                  counter_evidence_json = EXCLUDED.counter_evidence_json,
                  recommended_action_json = EXCLUDED.recommended_action_json,
                  status = 'approved',
                  version = aletheia_reasoning_findings.version + 1,
                  source_agent = 'FindingApprovalReviewGate',
                  updated_at = NOW()
                RETURNING id, run_id, project_id, canonical_key, title, conclusion, confidence,
                          supporting_evidence_json, counter_evidence_json, recommended_action_json,
                          status, version, source_agent, created_at, updated_at
                """
            ),
            {
                "run_id": run["id"],
                "tenant_id": tenant.tenant_id,
                "canonical_key": formal_key,
                "title": candidate_dict["title"],
                "conclusion": candidate_dict["conclusion"],
                "confidence": candidate_dict["confidence"],
                "supporting_evidence_json": _json_dump(evidence_chain),
                "counter_evidence_json": _json_dump(candidate_dict.get("evidence_limits") or []),
                "recommended_action_json": _json_dump(recommended_action),
            },
        ).mappings().first()
        conn.execute(
            text(
                """
                INSERT INTO aletheia_reasoning_reviews
                (finding_id, project_id, canonical_key, decision, reviewer, reason,
                 before_status, after_status, before_version, after_version, created_at)
                VALUES
                (:finding_id, :project_id, :canonical_key, 'approved', :reviewer, :reason,
                 :before_status, 'approved', 0, :after_version, NOW())
                """
            ),
            {
                "finding_id": finding["id"],
                "project_id": tenant.tenant_id,
                "canonical_key": formal_key,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_version": finding["version"],
            },
        )
        return formal_key

    def get_autopilot_candidate(self, tenant, candidate_key):
        self.ensure_autopilot_schema(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT *
                    FROM aletheia_autopilot_candidate_findings
                    WHERE project_id = :tenant_id AND canonical_key = :candidate_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "candidate_key": candidate_key},
            ).mappings().first()
        return self._autopilot_candidate_to_dict(row) if row else None

    def _approved_finding_action(self, candidate_dict, candidate_row, reason):
        suggested = candidate_dict.get("suggested_action") or {}
        deep_graph_profile = candidate_dict.get("deep_graph_profile") or self._deep_graph_profile(candidate_dict.get("evidence_chain") or [])
        return {
            "type": "reviewed_inference",
            "prior_insight_label": "approved finding",
            "source_candidate_key": candidate_dict.get("canonical_key"),
            "autopilot_session_key": candidate_row.get("session_key"),
            "hypothesis_key": candidate_row.get("hypothesis_key"),
            "finding_emphasis": candidate_dict.get("finding_emphasis") or deep_graph_profile.get("finding_emphasis"),
            "deep_graph_profile": deep_graph_profile,
            "review_reason": reason,
            "next_action": suggested,
            "workspace_next_action": {
                "type": "case_next_action",
                "label": suggested.get("next") or suggested.get("label") or "Review approved finding and assign follow-up owner",
                "source": "approved_finding",
                "status": "ready_for_dispatch",
                "writes_canonical": False,
            },
            "change_proposal_bridge": {
                "available": True,
                "proposal_types": ["ontology_rule", "graph_edge", "review_playbook"],
                "writes_canonical": False,
                "requires_governance_review": True,
            },
            "canonical_boundary": self._finding_canonical_boundary(),
        }

    def _finding_canonical_boundary(self):
        return finding_canonical_boundary()

    DEEP_GRAPH_REQUIRED_STEPS = REASONING_DEEP_GRAPH_REQUIRED_STEPS

    def _deep_graph_profile(self, evidence_chain):
        return deep_graph_profile(evidence_chain)

    def _autopilot_session_row(self, tenant, session_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM aletheia_autopilot_sessions WHERE project_id = :tenant_id AND session_key = :session_key"),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()

    def _autopilot_hypothesis_row(self, tenant, hypothesis_key):
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text("SELECT * FROM aletheia_autopilot_hypotheses WHERE project_id = :tenant_id AND hypothesis_key = :hypothesis_key"),
                {"tenant_id": tenant.tenant_id, "hypothesis_key": hypothesis_key},
            ).mappings().first()
