"""ReasoningRepository, extracted from server.py. No behavior change."""

import hashlib
import json
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, unquote, urlparse
from sqlalchemy import bindparam, create_engine, text
from aletheia.reasoning.engine import ReasoningEngine
from aletheia.reasoning.finding_framework import (
    DEEP_GRAPH_REQUIRED_STEPS as REASONING_DEEP_GRAPH_REQUIRED_STEPS,
    deep_graph_profile,
    entity_profile_aggregate_evidence,
    finding_canonical_boundary,
    plain_reasoning_conclusion,
    plain_reasoning_title,
    review_graph_scope_action,
    scoped_graph_finding,
)
from aletheia.interfaces.api.helpers import _json_dump, _load_json, _require_reason, _slug
from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache


class ReasoningRepository(_TenantScopedEngineCache):
    def __init__(self, tenant_registry, instance_repository, ensure_schema=False):
        super().__init__(tenant_registry, ensure_schema)
        self.instance_repository = instance_repository
        self._autopilot_schema_ready = set()
        self._finding_experience_schema_ready = set()

    def ensure_finding_experience_schema(self, tenant):
        key = tenant.metadata_db_url
        if key in self._finding_experience_schema_ready:
            return
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_finding_actions (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(255) NOT NULL DEFAULT 'default',
                    action_key VARCHAR(500) NOT NULL,
                    finding_key VARCHAR(500) NOT NULL,
                    title TEXT NOT NULL,
                    action_type VARCHAR(100) NOT NULL DEFAULT 'investigate',
                    owner VARCHAR(255),
                    due_at TIMESTAMP,
                    priority VARCHAR(50) NOT NULL DEFAULT 'medium',
                    status VARCHAR(50) NOT NULL DEFAULT 'open',
                    result VARCHAR(100),
                    result_detail TEXT,
                    created_from VARCHAR(100) NOT NULL DEFAULT 'approved_finding',
                    canonical_write BOOLEAN NOT NULL DEFAULT FALSE,
                    graph_write BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    closed_at TIMESTAMP
                )
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_finding_actions_project_key ON aletheia_finding_actions (project_id, action_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_finding_actions_project_finding ON aletheia_finding_actions (project_id, finding_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_finding_actions_project_status_due ON aletheia_finding_actions (project_id, status, due_at)"))
        self._finding_experience_schema_ready.add(key)

    def ensure_autopilot_schema(self, tenant):
        key = tenant.metadata_db_url
        if key in self._autopilot_schema_ready:
            return
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_sessions (
                    id SERIAL PRIMARY KEY,
                    project_id VARCHAR(255) NOT NULL,
                    session_key VARCHAR(255) NOT NULL,
                    objective TEXT NOT NULL,
                    scope_json TEXT NOT NULL DEFAULT '{}',
                    budget_json TEXT NOT NULL DEFAULT '{}',
                    safety_profile_json TEXT NOT NULL DEFAULT '{}',
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    created_by VARCHAR(255) NOT NULL DEFAULT 'Autopilot',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_hypotheses (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES aletheia_autopilot_sessions(id) ON DELETE CASCADE,
                    project_id VARCHAR(255) NOT NULL,
                    hypothesis_key VARCHAR(255) NOT NULL,
                    title TEXT NOT NULL,
                    rationale TEXT,
                    status VARCHAR(50) NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 100,
                    evidence_plan_json TEXT NOT NULL DEFAULT '[]',
                    reasoning_task_keys_json TEXT NOT NULL DEFAULT '[]',
                    pruned_reason TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS aletheia_autopilot_candidate_findings (
                    id SERIAL PRIMARY KEY,
                    session_id INTEGER NOT NULL REFERENCES aletheia_autopilot_sessions(id) ON DELETE CASCADE,
                    hypothesis_id INTEGER REFERENCES aletheia_autopilot_hypotheses(id) ON DELETE SET NULL,
                    project_id VARCHAR(255) NOT NULL,
                    canonical_key VARCHAR(255) NOT NULL,
                    title TEXT NOT NULL,
                    conclusion TEXT NOT NULL,
                    value_score FLOAT NOT NULL DEFAULT 0,
                    confidence FLOAT NOT NULL DEFAULT 0,
                    novelty_score FLOAT NOT NULL DEFAULT 0,
                    impact_score FLOAT NOT NULL DEFAULT 0,
                    evidence_chain_json TEXT NOT NULL DEFAULT '[]',
                    evidence_limits_json TEXT NOT NULL DEFAULT '[]',
                    suggested_action_json TEXT NOT NULL DEFAULT '{}',
                    status VARCHAR(50) NOT NULL DEFAULT 'draft',
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_sessions_project_key ON aletheia_autopilot_sessions (project_id, session_key)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_hypotheses_project_key ON aletheia_autopilot_hypotheses (project_id, hypothesis_key)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_autopilot_candidate_findings_project_key ON aletheia_autopilot_candidate_findings (project_id, canonical_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_autopilot_hypotheses_session ON aletheia_autopilot_hypotheses (session_id, priority, id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_autopilot_candidate_findings_session ON aletheia_autopilot_candidate_findings (session_id, value_score DESC, id)"))
        self._autopilot_schema_ready.add(key)

    def _autopilot_budget(self, raw):
        raw = raw or {}
        return {
            "max_hypotheses": max(1, min(int(raw.get("max_hypotheses") or 8), 25)),
            "max_reasoning_tasks": max(1, min(int(raw.get("max_reasoning_tasks") or raw.get("max_runs") or 5), 20)),
            "max_tool_calls": max(1, min(int(raw.get("max_tool_calls") or raw.get("max_queries") or 20), 80)),
            "max_runtime_seconds": max(5, min(int(raw.get("max_runtime_seconds") or 120), 600)),
            "sample_strategy": raw.get("sample_strategy") or "deterministic_full_table_aggregates",
        }

    def _autopilot_safety_profile(self, raw):
        raw = raw or {}
        profile = {
            "approved_only": raw.get("approved_only", True) is not False,
            "safe_views_only": raw.get("safe_views_only", True) is not False,
            "allow_sensitive_fields": False,
            "masked_fields_only": True,
            "write_scope": "draft_only",
            "canonical_writes": "disabled",
            "auto_approve_findings": False,
        }
        blocked = raw.get("blocked_fields") if "blocked_fields" in raw else ["card_verification_code_fields"]
        normalized_blocked = []
        for field in blocked:
            if field in {"cardCVV", "enteredCVV"}:
                field = "card_verification_code_fields"
            normalized_blocked.append(field)
        profile["blocked_fields"] = list(dict.fromkeys(normalized_blocked))
        return profile

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
            if decision != "approved":
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

    def _finding_action_to_dict(self, row):
        due_at = row["due_at"]
        closed_at = row["closed_at"]
        updated_at = row["updated_at"]
        created_at = row["created_at"]
        is_overdue = False
        if due_at and row["status"] not in {"closed"}:
            try:
                is_overdue = due_at < datetime.now(due_at.tzinfo)
            except Exception:
                is_overdue = str(due_at) < datetime.now().isoformat()
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "action_key": row["action_key"],
            "finding_key": row["finding_key"],
            "title": row["title"],
            "action_type": row["action_type"],
            "owner": row["owner"],
            "due_at": str(due_at) if due_at else None,
            "priority": row["priority"],
            "status": row["status"],
            "result": row["result"],
            "result_detail": row["result_detail"],
            "created_from": row["created_from"],
            "canonical_write": bool(row["canonical_write"]),
            "graph_write": bool(row["graph_write"]),
            "is_overdue": bool(is_overdue),
            "created_at": str(created_at) if created_at else None,
            "updated_at": str(updated_at) if updated_at else None,
            "closed_at": str(closed_at) if closed_at else None,
        }

    def _review_to_dict(self, row):
        return {
            "canonical_key": row.get("canonical_key"),
            "decision": row["decision"],
            "reviewer": row["reviewer"],
            "reason": row["reason"],
            "before_status": row["before_status"],
            "after_status": row["after_status"],
            "before_version": row["before_version"],
            "after_version": row["after_version"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }

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

    ACTIVE_FINDING_STATUSES = {"approved", "reaffirmed"}
    INACTIVE_FINDING_STATUSES = {"rejected", "needs_more_evidence", "needs_changes", "stale", "superseded"}

    def list_findings_overview(self, tenant, limit=50, status=None, context=None):
        conditions = ["f.project_id = :tenant_id"]
        params = {"tenant_id": tenant.tenant_id, "limit": limit}
        if status:
            conditions.append("f.status = :status")
            params["status"] = status
        elif context == "active":
            conditions.append("f.status IN ('approved', 'reaffirmed')")
        where = " AND ".join(conditions)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT f.id, f.run_id, f.project_id, f.canonical_key, f.title, f.conclusion,
                           f.confidence, f.supporting_evidence_json, f.counter_evidence_json,
                           f.recommended_action_json, f.status, f.version, f.source_agent,
                           f.created_at, f.updated_at,
                           t.canonical_key AS task_key, t.question, t.scope_json,
                           r.run_key, r.status AS run_status, r.created_at AS run_created_at
                    FROM aletheia_reasoning_findings f
                    JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                    JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                    WHERE {where}
                    ORDER BY f.updated_at DESC, f.id DESC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
        findings = []
        for row in rows:
            finding = self._finding_to_dict(row)
            finding["task_key"] = row["task_key"]
            finding["question"] = row["question"]
            finding["task_scope"] = _load_json(row["scope_json"], {})
            finding["run_key"] = row["run_key"]
            finding["run_status"] = row["run_status"]
            finding["run_created_at"] = str(row["run_created_at"]) if row["run_created_at"] else None
            self._normalize_scoped_finding_display(tenant, finding)
            findings.append(finding)
        return findings

    def list_findings_registry(self, tenant, status=None, context=None, limit=50, filters=None):
        self.ensure_finding_experience_schema(tenant)
        filters = filters or {}
        findings = self.list_findings_overview(tenant, limit=limit, status=status, context=context)
        action_map = self._finding_action_map(tenant, [finding["canonical_key"] for finding in findings])
        review_map = self._finding_latest_review_map(tenant, [finding["canonical_key"] for finding in findings])
        enriched = []
        for finding in findings:
            decorated = self._decorate_approved_finding(finding)
            decorated["actions"] = action_map.get(finding["canonical_key"], [])
            decorated["action_summary"] = self._finding_action_summary(decorated["actions"])
            decorated["latest_review"] = review_map.get(finding["canonical_key"])
            decorated["finding_type"] = self._finding_type(decorated)
            decorated["source_label"] = self._finding_source_label(decorated)
            decorated["freshness"] = self._finding_freshness(decorated)
            decorated["value_score"] = self._finding_value_score(decorated)
            decorated["evidence_count"] = len(decorated.get("supporting_evidence") or [])
            enriched.append(decorated)
        enriched = self._filter_registry_findings(enriched, filters)
        enriched = self._sort_registry_findings(enriched, filters.get("sort"))
        groups = self._group_registry_findings(enriched, filters.get("group"))
        return {
            "tenant": tenant.public_dict(),
            "context": context or "all",
            "status": status,
            "filters": filters,
            "active_statuses": sorted(self.ACTIVE_FINDING_STATUSES),
            "excluded_from_active": sorted(self.INACTIVE_FINDING_STATUSES),
            "groups": groups,
            "findings": enriched,
        }

    def _finding_action_map(self, tenant, finding_keys):
        if not finding_keys:
            return {}
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_id, action_key, finding_key, title, action_type, owner, due_at,
                           priority, status, result, result_detail, created_from, canonical_write,
                           graph_write, created_at, updated_at, closed_at
                    FROM aletheia_finding_actions
                    WHERE project_id = :tenant_id AND finding_key = ANY(:finding_keys)
                    ORDER BY updated_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "finding_keys": finding_keys},
            ).mappings().all()
        result = {}
        for row in rows:
            action = self._finding_action_to_dict(row)
            result.setdefault(action["finding_key"], []).append(action)
        return result

    def _finding_latest_review_map(self, tenant, finding_keys):
        if not finding_keys:
            return {}
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (canonical_key)
                           canonical_key, decision, reviewer, reason, before_status, after_status,
                           before_version, after_version, created_at
                    FROM aletheia_reasoning_reviews
                    WHERE project_id = :tenant_id AND canonical_key = ANY(:finding_keys)
                    ORDER BY canonical_key, created_at DESC, id DESC
                    """
                ),
                {"tenant_id": tenant.tenant_id, "finding_keys": finding_keys},
            ).mappings().all()
        return {row["canonical_key"]: self._review_to_dict(row) for row in rows}

    def _finding_action_summary(self, actions):
        if not actions:
            return {"state": "no_action", "count": 0, "open_count": 0, "closed_count": 0}
        open_actions = [a for a in actions if a["status"] not in {"closed"}]
        closed_actions = [a for a in actions if a["status"] == "closed"]
        overdue = [a for a in open_actions if a.get("is_overdue")]
        primary = overdue[0] if overdue else open_actions[0] if open_actions else closed_actions[0]
        state = "overdue_action" if overdue else "open_action" if open_actions else "closed_action"
        return {
            "state": state,
            "count": len(actions),
            "open_count": len(open_actions),
            "closed_count": len(closed_actions),
            "primary": primary,
        }

    def _finding_type(self, finding):
        action = finding.get("recommended_action") or {}
        explicit = action.get("finding_type") or action.get("type")
        if explicit in {"risk_pattern", "operational_anomaly", "quality_issue", "ontology_conflict", "investigation_prompt"}:
            return explicit
        text_value = " ".join([finding.get("title") or "", finding.get("conclusion") or ""]).lower()
        if any(word in text_value for word in ("fraud", "risk", "mismatch", "card-not-present")):
            return "risk_pattern"
        if any(word in text_value for word in ("anomaly", "unusual", "abnormal", "duplicate")):
            return "operational_anomaly"
        if any(word in text_value for word in ("missing", "quality", "degraded", "weak-control")):
            return "quality_issue"
        if any(word in text_value for word in ("ontology", "schema", "canonical", "graph")):
            return "ontology_conflict"
        return "investigation_prompt"

    def _finding_source_label(self, finding):
        action = finding.get("recommended_action") or {}
        source_agent = finding.get("source_agent") or ""
        if action.get("source_candidate_key") or "Autopilot" in source_agent:
            return "Autopilot"
        if source_agent == "FindingApprovalReviewGate":
            return "Autopilot"
        if "Manual" in source_agent:
            return "Manual review"
        return "Reasoning"

    def _finding_freshness(self, finding):
        status_value = finding.get("status")
        latest = finding.get("latest_review") or {}
        decision = latest.get("decision")
        if status_value == "stale":
            return "stale"
        if status_value == "superseded":
            return "superseded"
        if decision == "reaffirmed":
            return "reaffirmed_recently"
        if status_value in self.ACTIVE_FINDING_STATUSES:
            return "due_for_revalidation"
        return "audit_only"

    def _finding_value_score(self, finding):
        action = finding.get("recommended_action") or {}
        for key in ("value_score", "impact_score", "confidence"):
            if action.get(key) is not None:
                try:
                    return float(action.get(key))
                except (TypeError, ValueError):
                    pass
        try:
            return float(finding.get("confidence") or 0)
        except (TypeError, ValueError):
            return 0.0

    def _filter_registry_findings(self, findings, filters):
        def keep(finding):
            if filters.get("finding_type") and finding.get("finding_type") != filters["finding_type"]:
                return False
            if filters.get("source") and finding.get("source_label") != filters["source"]:
                return False
            if filters.get("action_state") and finding.get("action_summary", {}).get("state") != filters["action_state"]:
                return False
            if filters.get("freshness") and finding.get("freshness") != filters["freshness"]:
                return False
            min_conf = filters.get("min_confidence")
            max_conf = filters.get("max_confidence")
            confidence = float(finding.get("confidence") or 0)
            if min_conf is not None and confidence < float(min_conf):
                return False
            if max_conf is not None and confidence > float(max_conf):
                return False
            min_value = filters.get("min_value")
            max_value = filters.get("max_value")
            value = float(finding.get("value_score") or 0)
            if min_value is not None and value < float(min_value):
                return False
            if max_value is not None and value > float(max_value):
                return False
            return True
        return [finding for finding in findings if keep(finding)]

    def _sort_registry_findings(self, findings, sort_key):
        sort_key = sort_key or "newest_reviewed"
        if sort_key == "value_desc":
            return sorted(findings, key=lambda f: float(f.get("value_score") or 0), reverse=True)
        if sort_key == "oldest_unrevalidated":
            return sorted(findings, key=lambda f: f.get("latest_review", {}).get("created_at") or f.get("updated_at") or "")
        if sort_key == "action_due_asc":
            return sorted(findings, key=lambda f: (f.get("action_summary", {}).get("primary") or {}).get("due_at") or "9999-12-31")
        if sort_key == "confidence_desc":
            return sorted(findings, key=lambda f: float(f.get("confidence") or 0), reverse=True)
        return sorted(findings, key=lambda f: (f.get("latest_review", {}) or {}).get("created_at") or f.get("updated_at") or "", reverse=True)

    def _group_registry_findings(self, findings, group_key):
        if not group_key:
            return []
        key_map = {
            "tenant": lambda f: f.get("tenant_id"),
            "status": lambda f: f.get("status"),
            "finding_type": lambda f: f.get("finding_type"),
            "action_state": lambda f: f.get("action_summary", {}).get("state"),
            "source": lambda f: f.get("source_label"),
        }
        fn = key_map.get(group_key)
        if not fn:
            return []
        counts = {}
        for finding in findings:
            key = fn(finding) or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return [{"group": key, "count": value} for key, value in sorted(counts.items())]

    def _decorate_approved_finding(self, finding):
        if finding.get("status") in self.ACTIVE_FINDING_STATUSES:
            finding = dict(finding)
            action = finding.get("recommended_action") or {}
            finding["context_label"] = "prior_finding"
            finding["reasoning_use"] = {
                "kind": "prior_finding",
                "label": "reviewed_inference",
                "source_ref": finding.get("canonical_key"),
                "allowed_context": "active_reasoning_context",
                "canonical_write": False,
                "graph_write": False,
                "auto_business_action": False,
            }
            finding["workspace_next_action"] = action.get("workspace_next_action") or action.get("next_action") or action.get("next")
            finding["change_proposal_bridge"] = action.get("change_proposal_bridge") or {
                "available": True,
                "writes_canonical": False,
                "requires_review_gate": True,
            }
        return finding

    def active_prior_findings(self, tenant, limit=5):
        findings = self.list_findings_overview(tenant, limit=limit, context="active")
        prior = []
        for finding in findings:
            prior.append({
                "kind": "prior_finding",
                "label": "reviewed_inference",
                "summary": finding.get("conclusion") or finding.get("title"),
                "source_ref": finding.get("canonical_key"),
                "confidence": finding.get("confidence"),
                "payload": {
                    "finding_key": finding.get("canonical_key"),
                    "status": finding.get("status"),
                    "title": finding.get("title"),
                    "reviewed_inference": True,
                    "canonical_write": False,
                    "graph_write": False,
                },
            })
        return prior

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

    def run_scoped_graph_task_streaming(self, tenant, task_key):
        started = time.monotonic()
        task = self._get_task_row(tenant, task_key)
        if task is None:
            yield {"event": "error", "data": {"message": f"Task not found: {task_key}"}}
            return
        if task.get("status") == "closed":
            yield {"event": "error", "data": {"message": "Cannot run a closed task"}}
            return
        if task.get("status") == "completed":
            self.update_task_status(tenant, task_key, "active")
            task["status"] = "active"
        scope = task.get("scope") or {}
        query_plan = [
            "Validate tenant-scoped graph task and approved-only scope.",
            "Read only the selected node or edge evidence path from Graph Explorer.",
            "Propose a draft finding without approving, ingesting, or changing canonical graph data.",
        ]
        yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
        ]
        evidence_paths = list(scope.get("evidence_paths") or [])
        yield {"event": "step", "data": {"tool": "graph_query", "status": "completed" if evidence_paths else "blocked", "step": 1, "total": 3}}
        if not evidence_paths:
            tool_calls[0]["status"] = "blocked"
            output = {"summary": "Scoped graph reasoning blocked because no evidence paths were provided.", "unsupported_claims": ["missing evidence path"]}
            eval_result = {"passed": False, "approved_only": True, "draft_only": True, "unsupported_claims": ["missing evidence path"], "evidence_path_count": 0}
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        center_node = scope.get("center_node")
        scope_depth = int(scope.get("depth") or 1)
        scope_limit = int(scope.get("node_limit") or 200)
        scope_edge_limit = int(scope.get("edge_limit") or scope_limit)
        graph_context = self._scoped_graph_prompt_context(
            tenant,
            center_node,
            scope_depth,
            scope_limit,
            scope_edge_limit,
            demo_mode=self._explicit_demo_mode(scope),
        )
        yield {
            "event": "llm_request_body",
            "data": self._llm_request_trace_payload(
                tenant,
                task,
                scope,
                request_body=self._formatted_scoped_reasoning_prompt_request(
                    tenant,
                    task,
                    scope,
                    evidence_paths,
                    scope_depth,
                    scope_limit,
                    scope_edge_limit,
                    graph_context=graph_context,
                ),
            ),
        }
        if not self._approved_or_explicit_demo_graph_context(graph_context):
            tool_calls[0]["status"] = "blocked"
            tool_calls[0]["projection_source"] = graph_context.get("projection_source")
            tool_calls[0]["demo_mode"] = graph_context.get("demo_mode", False)
            tool_calls[0]["degraded_reason"] = graph_context.get("degraded_reason")
            tool_calls[1]["status"] = "skipped"
            output, eval_result = self._missing_projection_block_payload(tenant, graph_context, evidence_paths)
            yield {
                "event": "llm_response_body",
                "data": self._llm_response_trace_payload(
                    tenant,
                    task,
                    scope,
                    response_body={
                        "schema_version": "reasoning_response_v1",
                        "status": "blocked",
                        "structured_answer": None,
                        "projection_source": output["projection_source"],
                        "demo_mode": output["demo_mode"],
                        "degraded_reason": output["degraded_reason"],
                    },
                ),
            }
            run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"), depth=scope_depth, limit=scope_limit)
        structured_response = (
            self._reasoning_response_v1(tenant, task, scope, structured_answer, evidence_paths, graph_context)
            if structured_answer
            else None
        )
        yield {
            "event": "llm_response_body",
            "data": self._llm_response_trace_payload(
                tenant,
                task,
                scope,
                response_body=structured_response or {
                    "schema_version": "reasoning_response_v1",
                    "structured_answer": None,
                    "note": "No structured entity profile answer was produced; fallback finding text will be used.",
                },
            ),
        }
        if structured_answer:
            query_plan = [
                "Validate tenant-scoped entity profile task and approved-only graph scope.",
                "Read the selected entity node evidence path from the approved graph.",
                "Materialize the response metrics into controlled evidence for review.",
                "Persist a draft finding from reasoning_response_v1 with evidence limits and next validation questions.",
            ]
            yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
            tool_calls.insert(1, {"tool": "entity_profile_aggregate", "tenant_id": tenant.tenant_id, "approved_only": True, "write_scope": "read_only_source_aggregate", "status": "completed"})
            metrics = structured_answer.get("metrics") or {}
            aggregate_evidence, ranking_summary = entity_profile_aggregate_evidence(
                tenant.tenant_id,
                task_key,
                center_node,
                scope_depth,
                metrics,
            )
            evidence_paths.append(aggregate_evidence)
            yield {
                "event": "no_llm_call",
                "data": {
                    "stage": "entity_profile_aggregate",
                    "reason": "No additional LLM request is made. This stage materializes the metrics already used in reasoning_response_v1 into supporting evidence so reviewers can audit degree, related edges, source rows, and ranked paths.",
                    "output_summary": ranking_summary,
                },
            }
            yield {"event": "step", "data": {"tool": "entity_profile_aggregate", "status": "completed", "step": 2, "total": 3}}
            title = structured_response["answer"]["title"]
            conclusion = structured_response["answer"]["conclusion"]
        else:
            title, conclusion = self._edge_or_scoped_finding_text(tenant, task, scope)
        yield {"event": "evidence", "data": {"evidence_paths": evidence_paths}}
        finding = scoped_graph_finding(
            task_key,
            title,
            conclusion,
            evidence_paths,
            structured_answer,
            structured_response,
            now_ms=int(time.time() * 1000),
        )
        output = {
            "summary": conclusion,
            "finding_keys": [finding["canonical_key"]],
            "unsupported_claims": [],
            "draft_only": True,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
            "degraded_reason": graph_context.get("degraded_reason"),
            **({"structured_answer": structured_answer, "structured_response": structured_response} if structured_answer else {}),
        }
        eval_result = {
            "passed": True,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "tenant_id": tenant.tenant_id,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
        }
        if structured_response and structured_response.get("conclusion_evaluation"):
            eval_result["conclusion_evaluation"] = structured_response["conclusion_evaluation"]
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        yield {
            "event": "no_llm_call",
            "data": {
                "stage": "propose_finding",
                "reason": "No additional LLM request is made. The draft finding title, conclusion, actions, and boundaries are persisted from reasoning_response_v1 plus the supporting evidence chain.",
                "output_summary": conclusion,
            },
        }
        yield {"event": "step", "data": {"tool": "propose_finding", "status": "completed", "step": 3, "total": 3}}
        finding_row = self._record_finding(tenant, run, finding)
        yield {"event": "finding", "data": {"finding": finding_row}}
        yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [finding_row], "approved": True}}

    def _llm_request_trace_payload(self, tenant, task, scope=None, request_body=None):
        scope = scope or task.get("scope") or {}
        return {
            "request_body": request_body or {},
            "request_title": "formatted prompt request",
            "tenant_id": tenant.tenant_id,
            "task_key": task.get("task_key") or task.get("key"),
            "center_node": scope.get("center_node"),
            "center_edge": scope.get("center_edge"),
            "depth": scope.get("depth"),
            "node_limit": scope.get("node_limit") or scope.get("limit"),
            "write_boundary": "draft_only",
        }

    def _llm_response_trace_payload(self, tenant, task, scope=None, response_body=None):
        scope = scope or task.get("scope") or {}
        return {
            "response_body": response_body or {},
            "response_title": "structured reasoning response",
            "tenant_id": tenant.tenant_id,
            "task_key": task.get("task_key") or task.get("key"),
            "center_node": scope.get("center_node"),
            "depth": scope.get("depth"),
            "node_limit": scope.get("node_limit") or scope.get("limit"),
            "write_boundary": "draft_only",
        }

    def _reasoning_response_v1(self, tenant, task, scope, structured_answer, evidence_paths, graph_context=None):
        scope = scope or task.get("scope") or {}
        structured_answer = structured_answer or {}
        metrics = structured_answer.get("metrics") or {}
        source_key_profile = metrics.get("source_key_profile") or {}
        graph_context = graph_context or self._scoped_graph_prompt_context(
            tenant,
            scope.get("center_node"),
            int(scope.get("depth") or 1),
            int(scope.get("node_limit") or 200),
            int(scope.get("edge_limit") or scope.get("node_limit") or 200),
        )

        ranked_paths = []
        for idx, path in enumerate(source_key_profile.get("top_paths") or [], start=1):
            ranked_paths.append({
                "rank": idx,
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table"),
                "label_column": path.get("label_col"),
                "evidence_role": "source_key_path_metric",
            })
        if not ranked_paths:
            source_node_labels = {
                node.get("id"): node.get("label")
                for node in graph_context.get("source_backed_related_nodes") or []
                if node.get("id") and node.get("label")
            }
            seen_paths = set()
            for edge in graph_context.get("source_backed_related_edges") or []:
                target = edge.get("target")
                label = source_node_labels.get(target) or str(target or "").split(":", 1)[-1]
                metric = edge.get("metric")
                marker = (label, metric, edge.get("source_table"))
                if not label or marker in seen_paths:
                    continue
                seen_paths.add(marker)
                ranked_paths.append({
                    "rank": len(ranked_paths) + 1,
                    "label": label,
                    "metric": metric,
                    "metric_value": edge.get("metric_value"),
                    "row_count": edge.get("row_count"),
                    "source_table": edge.get("source_table"),
                    "label_column": None,
                    "evidence_role": "source_backed_graph_edge_metric",
                })
        second_hop_paths = []
        for path in source_key_profile.get("second_hop_paths") or []:
            second_hop_paths.append({
                "label": path.get("label"),
                "source_table": path.get("table"),
                "metric": path.get("metric"),
                "top_peers": path.get("top_peers") or [],
                "evidence_role": "shared_path_peer_context",
            })

        graph_degree = graph_context.get("degree") or {}
        display_label = metrics.get("label") or scope.get("center_node")
        for node in graph_context.get("related_nodes") or []:
            if node.get("id") == scope.get("center_node") and node.get("label"):
                display_label = node.get("label")
                break
        evidence_refs = []
        for item in evidence_paths or []:
            evidence_refs.append({
                "kind": item.get("kind"),
                "label": item.get("label") or item.get("title"),
                "summary": item.get("summary"),
                "source_ref": item.get("source_ref"),
                "url": item.get("url"),
            })
        plain_conclusion = self._plain_reasoning_conclusion(
            task.get("question"),
            display_label,
            structured_answer.get("profile_summary") or "",
            ranked_paths,
            second_hop_paths,
            graph_degree,
        )
        plain_title = self._plain_reasoning_title(
            task.get("question"),
            display_label,
            ranked_paths,
            second_hop_paths,
        )
        traversal_analysis = self._joint_graph_traversal_analysis(
            display_label,
            scope.get("center_node"),
            graph_context,
            metrics,
            ranked_paths,
            second_hop_paths,
        )
        edge_target_reasoning = self._edge_target_reasoning_units(
            display_label,
            scope.get("center_node"),
            graph_context,
            metrics,
            evidence_refs,
        )
        deep_conclusion = self._business_conclusion_from_traversal(
            display_label,
            metrics,
            traversal_analysis,
            plain_conclusion,
            edge_target_reasoning,
        )
        conclusion_eval = self._evaluate_reasoning_conclusion(
            deep_conclusion,
            traversal_analysis,
            evidence_refs,
            structured_answer,
            edge_target_reasoning,
        )

        return {
            "schema_version": "reasoning_response_v1",
            "answer": {
                "title": plain_title or structured_answer.get("title") or task.get("question") or "Scoped graph reasoning",
                "plain_conclusion": deep_conclusion.get("plain_conclusion") or plain_conclusion,
                "conclusion": deep_conclusion.get("conclusion") or plain_conclusion,
                "detailed_conclusion": deep_conclusion.get("detailed_conclusion") or structured_answer.get("profile_summary") or "",
                "confidence": conclusion_eval.get("confidence", 0.78),
                "status": "draft",
            },
            "scope": {
                "tenant_id": tenant.tenant_id,
                "task_key": task.get("task_key") or task.get("key") or task.get("canonical_key"),
                "question": task.get("question"),
                "center_node": scope.get("center_node"),
                "depth": int(scope.get("depth") or 1),
                "node_limit": int(scope.get("node_limit") or 200),
                "edge_limit": int(scope.get("edge_limit") or scope.get("node_limit") or 200),
                "approved_only": True,
            },
            "graph_context": {
                "center_node": graph_context.get("center_node") or scope.get("center_node"),
                "degree": {
                    "visible_graph_center": graph_degree.get("visible_graph_center", graph_degree.get("center")),
                    "center": graph_degree.get("center"),
                    "by_link": graph_degree.get("by_link") or {},
                    "neighbor_type_counts": graph_degree.get("neighbor_type_counts") or {},
                    "source_key_row_degree": graph_degree.get("source_key_row_degree"),
                    "source_key_top_path_count": graph_degree.get("source_key_top_path_count"),
                },
                "related_nodes": graph_context.get("related_nodes") or [],
                "related_edges": graph_context.get("related_edges") or [],
                "source_backed_related_nodes": graph_context.get("source_backed_related_nodes") or [],
                "source_backed_related_edges": graph_context.get("source_backed_related_edges") or [],
                "truncated": graph_context.get("truncated") or {},
            },
            "key_facts": structured_answer.get("key_facts") or [],
            "ranked_paths": ranked_paths,
            "second_hop_paths": second_hop_paths,
            "traversal_analysis": traversal_analysis,
            "edge_target_reasoning": edge_target_reasoning,
            "business_interpretation": structured_answer.get("business_interpretation") or [],
            "conclusion_evaluation": conclusion_eval,
            "evidence": evidence_refs,
            "metrics": metrics,
            "limits": structured_answer.get("evidence_limits") or [],
            "next_questions": structured_answer.get("next_questions") or [],
            "actions": [review_graph_scope_action()],
            "write_boundary": {
                "status": "draft_only",
                "approved_finding_write": "review_gate_required",
                "must_not_write": ["canonical_ontology", "formal_graph"],
            },
        }

    def _joint_graph_traversal_analysis(self, label, center_node, graph_context, metrics, ranked_paths, second_hop_paths):
        nodes = graph_context.get("related_nodes") or []
        edges = graph_context.get("related_edges") or []
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency = {}
        relation_counts = {}
        relation_neighbor_types = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            relation = edge.get("label") or edge.get("link_key") or "relation"
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            for node_id, other_id in ((source, target), (target, source)):
                other_type = (nodes_by_id.get(other_id) or {}).get("type") or "unknown"
                relation_neighbor_types.setdefault(relation, {})
                relation_neighbor_types[relation][other_type] = relation_neighbor_types[relation].get(other_type, 0) + 1
                adjacency.setdefault(node_id, []).append({**edge, "_other": other_id, "_relation": relation})

        bfs_layers = []
        visited = {center_node} if center_node else set()
        frontier = {center_node} if center_node else set()
        for depth in range(1, 4):
            next_frontier = set()
            layer_relations = {}
            layer_types = {}
            sample_nodes = []
            for node_id in frontier:
                for edge in adjacency.get(node_id, []):
                    other = edge.get("_other")
                    if not other or other in visited:
                        continue
                    visited.add(other)
                    next_frontier.add(other)
                    relation = edge.get("_relation")
                    layer_relations[relation] = layer_relations.get(relation, 0) + 1
                    other_node = nodes_by_id.get(other) or {"id": other, "label": other, "type": "unknown"}
                    other_type = other_node.get("type") or "unknown"
                    layer_types[other_type] = layer_types.get(other_type, 0) + 1
                    if len(sample_nodes) < 8:
                        sample_nodes.append({
                            "id": other,
                            "label": other_node.get("label") or other,
                            "type": other_type,
                            "via_relation": relation,
                        })
            if not next_frontier:
                break
            bfs_layers.append({
                "depth": depth,
                "node_count": len(next_frontier),
                "relation_counts": dict(sorted(layer_relations.items())),
                "node_type_counts": dict(sorted(layer_types.items())),
                "sample_nodes": sample_nodes,
            })
            frontier = next_frontier

        dfs_paths = []
        max_paths = 8

        def dfs(node_id, path, seen, remaining):
            if len(dfs_paths) >= max_paths or remaining <= 0:
                return
            def onward_count(item):
                other = item.get("_other")
                if not other:
                    return 0
                return sum(
                    1
                    for next_edge in adjacency.get(other, [])
                    if next_edge.get("_other") not in seen and next_edge.get("_other") != node_id
                )

            candidates = sorted(
                adjacency.get(node_id, []),
                key=lambda item: (
                    -onward_count(item),
                    item.get("_relation") in {"Country Chokepoint Dependency", "relation"},
                    item.get("_relation") or "",
                    item.get("_other") or "",
                ),
            )
            for edge in candidates[:12]:
                other = edge.get("_other")
                if not other or other in seen:
                    continue
                other_node = nodes_by_id.get(other) or {"id": other, "label": other, "type": "unknown"}
                step = {
                    "from": node_id,
                    "relation": edge.get("_relation"),
                    "to": other,
                    "to_label": other_node.get("label") or other,
                    "to_type": other_node.get("type") or "unknown",
                }
                next_path = [*path, step]
                if len(next_path) >= 2 or onward_count(edge) == 0:
                    dfs_paths.append(next_path)
                dfs(other, next_path, {*seen, other}, remaining - 1)
                if len(dfs_paths) >= max_paths:
                    break

        if center_node:
            dfs(center_node, [], {center_node}, 3)

        source_profile = (metrics or {}).get("source_key_profile") or {}
        top_source_metrics = [
            {
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("source_table") or path.get("table"),
            }
            for path in ranked_paths[:5]
        ]
        relation_summary = [
            {
                "relation": relation,
                "edge_count": count,
                "neighbor_types": relation_neighbor_types.get(relation) or {},
            }
            for relation, count in sorted(relation_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ]
        return {
            "strategy": "joint_bfs_dfs_approved_graph_reasoning_v1",
            "center": {"id": center_node, "label": label},
            "max_observed_depth": bfs_layers[-1]["depth"] if bfs_layers else 0,
            "breadth": {
                "visited_node_count": len(visited),
                "relation_type_count": len(relation_counts),
                "layers": bfs_layers,
            },
            "depth_paths": [
                {
                    "path_length": len(path),
                    "steps": path,
                    "path_label": " -> ".join(
                        [label, *[step.get("to_label") or step.get("to") for step in path]]
                    ),
                }
                for path in dfs_paths
            ],
            "relation_summary": relation_summary,
            "source_metric_summary": {
                "total_rows": source_profile.get("total_key_rows"),
                "related_table_count": len(source_profile.get("related_tables") or []),
                "top_metrics": top_source_metrics,
            },
            "shared_peer_paths": second_hop_paths[:5],
        }

    def _edge_target_reasoning_units(self, label, center_node, graph_context, metrics, evidence_refs):
        retrieval_context = graph_context.get("retrieval_context") or {}
        related_nodes = graph_context.get("related_nodes") or []
        related_edges = graph_context.get("related_edges") or []
        retrieval_nodes = retrieval_context.get("nodes") or []
        retrieval_edges = retrieval_context.get("edges") or []
        semantic_items = retrieval_context.get("semantic_items") or []
        prior_findings = [
            item for item in evidence_refs or []
            if str(item.get("kind") or "").lower() in {"prior_finding", "finding", "draft_finding"}
            or item.get("label")
        ]

        nodes_by_id = {}
        for node in [*related_nodes, *retrieval_nodes]:
            node_id = node.get("id")
            if node_id:
                nodes_by_id.setdefault(node_id, {}).update(
                    {key: value for key, value in node.items() if value not in (None, "", [])}
                )

        edges_by_key = {}
        for edge in [*retrieval_edges, *related_edges]:
            source = edge.get("source")
            target = edge.get("target")
            relation = edge.get("relation") or edge.get("label") or edge.get("link_key") or "relation"
            if not source or not target:
                continue
            key = (source, relation, target)
            edges_by_key.setdefault(key, {}).update(
                {field: value for field, value in edge.items() if value not in (None, "", [])}
            )
            if edge.get("id"):
                edges_by_key[key].setdefault("id", edge.get("id"))
            edges_by_key[key].setdefault("relation", relation)

        def text_blob(*items):
            return " ".join(str(item or "").lower() for item in items if item not in (None, "", []))

        def numeric_metrics(properties):
            selected = {}
            priority_terms = (
                "risk", "trade", "piracy", "geopolitical", "canal", "share",
                "v_", "q_", "impact", "likelihood", "severity", "cost", "flow",
            )
            for key, value in (properties or {}).items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    lower_key = str(key).lower()
                    if any(term in lower_key for term in priority_terms):
                        selected[key] = value
                if len(selected) >= 12:
                    break
            return selected

        def attached_semantic(source_label, relation, target_label, source_url):
            markers = text_blob(source_label, relation, target_label).split()
            result = []
            for item in semantic_items:
                item_blob = text_blob(
                    item.get("label"),
                    item.get("summary"),
                    item.get("evidence_quote"),
                    item.get("subject"),
                    item.get("target"),
                    item.get("metric_key"),
                )
                source_match = bool(source_url and item.get("source_url") == source_url)
                label_match = any(marker and len(marker) >= 3 and marker in item_blob for marker in markers)
                if source_match or label_match:
                    result.append({
                        "element_key": item.get("element_key"),
                        "element_type": item.get("element_type"),
                        "label": item.get("label"),
                        "summary": item.get("summary") or item.get("evidence_quote"),
                        "metric_key": item.get("metric_key"),
                        "status": item.get("status"),
                    })
                if len(result) >= 3:
                    break
            return result

        def attached_findings(source_label, target_label):
            markers = [str(source_label or "").lower(), str(target_label or "").lower()]
            result = []
            for finding in prior_findings:
                blob = text_blob(finding.get("label"), finding.get("summary"), finding.get("source_ref"))
                if any(marker and len(marker) >= 3 and marker in blob for marker in markers):
                    result.append({
                        "label": finding.get("label"),
                        "summary": finding.get("summary"),
                        "source_ref": finding.get("source_ref"),
                    })
                if len(result) >= 2:
                    break
            return result

        def local_business_reason(relation, target_label, target_type, edge_metrics, semantic_count, finding_count):
            relation_l = str(relation or "").lower()
            target = target_label or target_type or "the target node"
            if "systemic risk" in relation_l:
                return (
                    f"{target} is an exposure channel for systemic chokepoint risk; edge metrics should be read as local loss, disruption, or trade-at-risk signals."
                )
            if "dependency" in relation_l:
                return (
                    f"{target} depends on the chokepoint, so this edge contributes demand-side exposure and should influence country or counterparty monitoring priority."
                )
            if "deploy" in relation_l or "mine" in relation_l:
                return (
                    f"{target} represents a disruption trigger or capability near the chokepoint, making the edge relevant to event escalation and scenario thresholds."
                )
            if relation_l in {"connects", "positioned_between", "situated_at_entrance_of", "provides_access_to"}:
                return (
                    f"{target} is part of the route-propagation structure; disruption can move from the center into adjacent maritime geography rather than staying local."
                )
            if edge_metrics:
                return f"{target} carries quantified local evidence, so the edge should be weighted in the combined risk conclusion."
            if semantic_count or finding_count:
                return f"{target} has attached semantic or finding context, so the edge should be reviewed as more than a topology link."
            return f"{target} contributes an approved relation that supports traversal but needs review before operational use."

        units = []
        relation_seen = {}
        for edge in edges_by_key.values():
            source = edge.get("source")
            target = edge.get("target")
            relation = edge.get("relation") or edge.get("label") or edge.get("link_key") or "relation"
            if center_node and center_node not in {source, target} and len(units) >= 12:
                continue
            relation_seen[relation] = relation_seen.get(relation, 0) + 1
            if relation_seen[relation] > 6 and len(units) >= 18:
                continue
            source_node = nodes_by_id.get(source) or {}
            target_node = nodes_by_id.get(target) or {}
            target_label = target_node.get("label") or edge.get("target_label") or target
            source_label = source_node.get("label") or edge.get("source_label") or source
            properties = edge.get("properties") or {
                key: value for key, value in edge.items()
                if key not in {
                    "id", "source", "target", "label", "relation", "link_key", "status",
                    "projection_source", "source_label", "target_label",
                }
            }
            edge_metrics = numeric_metrics(properties)
            semantic = attached_semantic(
                source_label,
                relation,
                target_label,
                edge.get("source_url") or properties.get("source_url"),
            )
            findings = attached_findings(source_label, target_label)
            units.append({
                "unit_type": "edge_target",
                "edge_id": edge.get("id"),
                "source": source,
                "source_label": source_label,
                "relation": relation,
                "target": target,
                "target_label": target_label,
                "target_type": target_node.get("type"),
                "local_metrics": edge_metrics,
                "attached_semantic_items": semantic,
                "attached_findings": findings,
                "local_reasoning": local_business_reason(
                    relation,
                    target_label,
                    target_node.get("type"),
                    edge_metrics,
                    len(semantic),
                    len(findings),
                ),
                "evaluation": {
                    "has_edge": True,
                    "has_target_node": bool(target_node),
                    "has_local_metrics": bool(edge_metrics),
                    "has_semantic_or_finding_context": bool(semantic or findings),
                    "has_business_reasoning": True,
                },
            })
            if len(units) >= 24:
                break

        metric_units = sum(1 for unit in units if unit.get("local_metrics"))
        contextual_units = sum(
            1 for unit in units
            if unit.get("attached_semantic_items") or unit.get("attached_findings")
        )
        relation_types = sorted({unit.get("relation") for unit in units if unit.get("relation")})
        source_metric_profile = (metrics or {}).get("source_key_profile") or graph_context.get("source_key_metrics") or {}
        top_source_metrics = [
            {
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table") or path.get("source_table"),
            }
            for path in (source_metric_profile.get("top_paths") or [])[:8]
        ]
        return {
            "strategy": "per_edge_target_then_aggregate_reasoning_v1",
            "center": {"id": center_node, "label": label},
            "unit_count": len(units),
            "units": units,
            "summary": {
                "relation_type_count": len(relation_types),
                "relations": relation_types[:12],
                "units_with_local_metrics": metric_units,
                "units_with_semantic_or_finding_context": contextual_units,
                "source_metric_count": len(top_source_metrics),
                "top_source_metrics": top_source_metrics,
            },
        }

    def _business_conclusion_from_traversal(self, label, metrics, traversal_analysis, fallback, edge_target_reasoning=None):
        source_summary = traversal_analysis.get("source_metric_summary") or {}
        relation_summary = traversal_analysis.get("relation_summary") or []
        edge_target_reasoning = edge_target_reasoning or {}
        edge_summary = edge_target_reasoning.get("summary") or {}
        breadth = traversal_analysis.get("breadth") or {}
        neighbor_types = (metrics or {}).get("neighbor_types") or {}
        source_profile = ((metrics or {}).get("source_key_profile") or {})
        top_metrics = source_summary.get("top_metrics") or []
        metric_names = {str(item.get("metric") or "").lower() for item in top_metrics}
        country_count = int(neighbor_types.get("Country") or 0)
        relation_text = ", ".join(item.get("relation") for item in relation_summary[:3] if item.get("relation")) or "approved relationships"
        has_trade_exposure = any("trade_at_risk" in metric for metric in metric_names)
        has_flow_concentration = any(metric in {"v_canal", "q_canal"} for metric in metric_names)
        related_tables = source_profile.get("related_tables") or []
        is_maritime = any(str(item.get("table") or "").startswith("maritime_") for item in related_tables)
        if is_maritime or has_trade_exposure or has_flow_concentration:
            drivers = []
            if has_trade_exposure:
                drivers.append("trade-at-risk exposure")
            if has_flow_concentration:
                drivers.append("canal-flow concentration")
            if country_count:
                drivers.append(f"{country_count} country dependency links")
            if edge_target_reasoning.get("unit_count"):
                drivers.append(f"{edge_target_reasoning.get('unit_count')} edge-target local reasoning units")
            driver_text = ", ".join(drivers) or relation_text
            unit_metric_text = ""
            if edge_summary.get("units_with_local_metrics"):
                unit_metric_text = f" {edge_summary.get('units_with_local_metrics')} local edge/node unit(s) also carry attached metrics."
            plain = (
                f"{label} is a systemic maritime risk priority because multiple approved evidence channels jointly point to {driver_text}. "
                "The business risk is not the graph connectivity itself; it is that edge-level exposure, target-node dependency, and route-propagation signals can propagate into trade-flow interruption, freight-cost pressure, rerouting constraints, and cross-country exposure that should trigger monitoring escalation."
            )
            detail = (
                f"Breadth-first traversal visits {breadth.get('visited_node_count', 0)} approved nodes across "
                f"{breadth.get('relation_type_count', 0)} relation type(s), while depth-first paths show how the center connects through "
                f"{relation_text}. Controlled source metrics add {source_summary.get('total_rows')} matching rows across "
                f"{source_summary.get('related_table_count')} table(s). Per-edge/target reasoning reviewed "
                f"{edge_target_reasoning.get('unit_count', 0)} local unit(s) across {edge_summary.get('relation_type_count', 0)} relation type(s)."
                f"{unit_metric_text} The recommended business response is to review scenario thresholds, "
                "alternate-route assumptions, and watchlist escalation before treating the finding as operational guidance."
            )
            return {"plain_conclusion": plain, "conclusion": plain, "detailed_conclusion": detail}
        if traversal_analysis.get("max_observed_depth", 0) >= 2:
            plain = (
                f"{label} has multi-hop business exposure in the approved graph: its direct relationships connect into second-order counterparties, "
                "so review should focus on propagation paths rather than a single-node profile."
            )
            return {"plain_conclusion": plain, "conclusion": plain, "detailed_conclusion": fallback}
        return {"plain_conclusion": fallback, "conclusion": fallback, "detailed_conclusion": fallback}

    def _evaluate_reasoning_conclusion(self, conclusion, traversal_analysis, evidence_refs, structured_answer, edge_target_reasoning=None):
        breadth = traversal_analysis.get("breadth") or {}
        source_summary = traversal_analysis.get("source_metric_summary") or {}
        edge_target_reasoning = edge_target_reasoning or {}
        edge_summary = edge_target_reasoning.get("summary") or {}
        checks = {
            "uses_breadth_traversal": bool((breadth.get("layers") or [])),
            "uses_depth_paths": any(
                int(path.get("path_length") or 0) >= 2
                for path in traversal_analysis.get("depth_paths") or []
            ),
            "uses_multiple_relations_or_sources": (
                int(breadth.get("relation_type_count") or 0) >= 2
                or int(source_summary.get("related_table_count") or 0) >= 2
            ),
            "has_source_metrics": bool(source_summary.get("top_metrics")),
            "uses_edge_target_units": bool(edge_target_reasoning.get("units")),
            "uses_attached_edge_or_source_metrics": bool(
                edge_summary.get("units_with_local_metrics")
                or edge_summary.get("top_source_metrics")
                or source_summary.get("top_metrics")
            ),
            "uses_attached_findings_or_semantic_context": bool(edge_summary.get("units_with_semantic_or_finding_context")),
            "has_business_actionability": any(
                term in str(conclusion.get("conclusion") or "").lower()
                for term in ("monitoring", "scenario", "rerouting", "operational", "trade-flow", "cost", "escalation", "exposure")
            ),
            "states_review_boundary": bool((structured_answer or {}).get("evidence_limits")) or bool(evidence_refs),
        }
        score = round(sum(1 for value in checks.values() if value) / max(len(checks), 1), 4)
        return {
            "schema_version": "reasoning_conclusion_eval_v1",
            "score": score,
            "passed": score >= 0.75,
            "confidence": 0.82 if score >= 0.85 else 0.78 if score >= 0.75 else 0.62,
            "checks": checks,
            "limits": [
                "Evaluation checks reasoning shape and evidence linkage; it does not approve the finding.",
                "Business conclusion remains draft-only until human review.",
            ],
        }

    def _plain_reasoning_title(self, question, label, ranked_paths, second_hop_paths):
        return plain_reasoning_title(question, label, ranked_paths, second_hop_paths)

    def _plain_reasoning_conclusion(self, question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree):
        return plain_reasoning_conclusion(question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree)

    def _formatted_scoped_reasoning_prompt_request(self, tenant, task, scope, evidence_paths, scope_depth, scope_limit, scope_edge_limit, graph_context=None):
        question = task.get("question") or ""
        center_node = scope.get("center_node")
        graph_context = graph_context or self._scoped_graph_prompt_context(tenant, center_node, scope_depth, scope_limit, scope_edge_limit)
        evidence_text = json.dumps(evidence_paths, ensure_ascii=False, indent=2)
        graph_context_text = json.dumps(graph_context, ensure_ascii=False, indent=2)
        system_prompt = (
            "You are Aletheia's tenant-scoped graph reasoning agent. "
            "Use only approved graph evidence and controlled source aggregations. "
            "Do not ingest new data, approve findings, or write canonical ontology/formal graph data. "
            "Return a draft finding with evidence limits and review boundaries."
        )
        user_prompt = (
            f"Tenant: {tenant.tenant_id}\n"
            f"Question: {question}\n"
            f"Center node: {center_node or '—'}\n"
            f"Depth: {scope_depth}\n"
            f"Node limit: {scope_limit}\n"
            "Evidence paths:\n"
            f"{evidence_text}\n\n"
            "Graph scope context:\n"
            f"{graph_context_text}\n\n"
            "Expected output:\n"
            "- answer.plain_conclusion: 1-2 plain-language sentences. Explain impact first; mention only top paths and key counterparties. Do not list long metric rows here.\n"
            "- answer.detailed_conclusion: longer reasoning narrative when needed\n"
            "- ranked_paths, second_hop_paths, graph_context.degree, and key_facts carry the numbers, degree, related node data, and edge data\n"
            "- limitations / counter-evidence\n"
            "- draft-only write boundary"
        )
        return {
            "provider": "internal_reasoning_engine",
            "model": "ReasoningEngine.analyze",
            "prompt_version": "graph_scope_reasoning_v1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "parameters": {
                "tenant_id": tenant.tenant_id,
                "task_key": task.get("task_key") or task.get("key"),
                "center_node": center_node,
                "depth": scope_depth,
                "node_limit": scope_limit,
                "edge_limit": scope_edge_limit,
                "approved_only": True,
            },
            "response_contract": {
                "schema_version": "reasoning_response_v1",
                "status": "draft",
                "answer": {
                    "plain_conclusion": "1-2 human-readable sentences, not an evidence dump",
                    "detailed_conclusion": "long-form support text",
                },
                "required_sections": ["graph_context", "key_facts", "ranked_paths", "second_hop_paths", "limits", "next_questions", "write_boundary"],
                "write_boundary": "draft_only",
                "must_not_write": ["canonical_ontology", "formal_graph"],
            },
        }

    def _explicit_demo_mode(self, scope):
        scope = scope or {}
        return bool(scope.get("demo_mode") is True or scope.get("allow_demo_mode") is True or scope.get("allow_demo_fallback") is True)

    def _reasoning_projection_sources(self, *contexts):
        sources = []
        for context in contexts:
            scope = (context or {}).get("scope") or {}
            for source in (scope.get("projection_source"), (context or {}).get("projection_source")):
                if not source:
                    continue
                for part in str(source).split("+"):
                    part = part.strip()
                    if part and part not in sources:
                        sources.append(part)
        return "+".join(sources) if sources else "none"

    def _approved_or_explicit_demo_graph_context(self, graph_context):
        graph_context = graph_context or {}
        return bool(graph_context.get("approved") or graph_context.get("demo_mode"))

    def _missing_projection_block_payload(self, tenant, graph_context, evidence_paths):
        graph_context = graph_context or {}
        unsupported_claims = ["missing approved graph projection"]
        output = {
            "summary": "Scoped graph reasoning blocked because no approved graph projection is available.",
            "unsupported_claims": unsupported_claims,
            "draft_only": True,
            "projection_source": graph_context.get("projection_source") or "none",
            "demo_mode": bool(graph_context.get("demo_mode")),
            "degraded_reason": graph_context.get("degraded_reason") or self._missing_projection_reason(),
        }
        eval_result = {
            "passed": False,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": unsupported_claims,
            "evidence_path_count": len(evidence_paths or []),
            "tenant_id": tenant.tenant_id,
            "projection_source": output["projection_source"],
            "demo_mode": output["demo_mode"],
            "degraded_reason": output["degraded_reason"],
        }
        return output, eval_result

    def _missing_projection_reason(self):
        return "No reviewed SchemaGraphModelingAgent projection. Import data and run schema-to-graph modeling first."

    def _scoped_graph_prompt_context(self, tenant, center_node, depth, node_limit, edge_limit, demo_mode=False):
        demo_mode = bool(demo_mode)
        if not center_node or ":" not in str(center_node):
            projection_source = "explicit_demo_mode" if demo_mode else "none"
            return {
                "center_node": center_node,
                "nodes": [],
                "edges": [],
                "retrieval_mode": "explicit_demo_mode" if demo_mode else "degraded_no_approved_projection",
                "degree": {"center": 0},
                "approved": False,
                "projection_source": projection_source,
                "demo_mode": demo_mode,
                "degraded_reason": None if demo_mode else self._missing_projection_reason(),
            }
        object_type, instance_id = str(center_node).split(":", 1)
        node_limit = max(1, min(int(node_limit or 200), 300))
        edge_limit = max(1, min(int(edge_limit or node_limit), 300))
        depth = max(1, min(int(depth or 1), 3))
        fetch_limit = max(node_limit, edge_limit, 300)
        local_rag_context = self.instance_repository.local_rag_context(
            tenant,
            object_type,
            instance_id,
            question=None,
            depth=depth,
            limit=fetch_limit,
        ) or {}
        graph = self.instance_repository.full_graph(tenant, object_type, instance_id, limit=fetch_limit) or {}
        approved = bool(local_rag_context.get("approved") or graph.get("approved"))
        projection_source = self._reasoning_projection_sources(local_rag_context, graph)
        if demo_mode and not approved:
            projection_source = "explicit_demo_mode"
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if local_rag_context.get("approved"):
            local_nodes = local_rag_context.get("nodes") or []
            local_edges = local_rag_context.get("edges") or []
            local_node_ids = {node.get("id") for node in local_nodes if node.get("id")}
            full_nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
            for node in local_nodes:
                if node.get("id") and node.get("id") not in full_nodes_by_id:
                    nodes.append(node)
                    full_nodes_by_id[node.get("id")] = node
            edge_ids = {edge.get("id") for edge in edges if edge.get("id")}
            for edge in local_edges:
                if edge.get("id") and edge.get("id") not in edge_ids:
                    edges.append(edge)
                    edge_ids.add(edge.get("id"))
            if local_node_ids:
                nodes = sorted(nodes, key=lambda node: 0 if node.get("id") in local_node_ids else 1)
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            adjacency.setdefault(source, []).append(edge)
            adjacency.setdefault(target, []).append(edge)

        visited = {center_node}
        frontier = {center_node}
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                for edge in adjacency.get(node_id, []):
                    other = edge.get("target") if edge.get("source") == node_id else edge.get("source")
                    if other and other not in visited:
                        visited.add(other)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        center_edges = self._diversify_edges_by_relation(adjacency.get(center_node, []))
        center_neighbor_ids = []
        for edge in center_edges:
            other = edge.get("target") if edge.get("source") == center_node else edge.get("source")
            if other:
                center_neighbor_ids.append(other)
        ordered_node_ids = [center_node] + center_neighbor_ids + [node_id for node_id in visited if node_id not in {center_node, *center_neighbor_ids}]
        seen_ordered_nodes = set()
        scoped_nodes = []
        for node_id in ordered_node_ids:
            if node_id in seen_ordered_nodes or node_id not in nodes_by_id:
                continue
            seen_ordered_nodes.add(node_id)
            scoped_nodes.append(nodes_by_id[node_id])
        center_edge_budget = edge_limit
        if depth > 1 and len(center_edges) > 20:
            center_edge_budget = max(20, min(len(center_edges), edge_limit // 2))
        selected_center_edges = center_edges[:center_edge_budget]
        center_edge_ids = {edge.get("id") for edge in selected_center_edges}
        remaining_edge_budget = max(edge_limit - len(selected_center_edges), 0)
        secondary_edges = [
            edge for edge in edges
            if edge.get("id") not in center_edge_ids and edge.get("source") in visited and edge.get("target") in visited
        ][:remaining_edge_budget]
        scoped_edges = [
            *selected_center_edges,
            *secondary_edges,
        ]
        degree_by_link = {}
        neighbor_type_counts = {}
        for edge in center_edges:
            degree_by_link[edge.get("label") or edge.get("link_key") or "edge"] = degree_by_link.get(edge.get("label") or edge.get("link_key") or "edge", 0) + 1
            other = edge.get("target") if edge.get("source") == center_node else edge.get("source")
            node_type = (nodes_by_id.get(other) or {}).get("type") or "unknown"
            neighbor_type_counts[node_type] = neighbor_type_counts.get(node_type, 0) + 1

        # Source-key profiling (SQL-join aggregation across tables sharing
        # a source key) was retired along with the rest of reasoning_engine's
        # SQL retrieval core -- no graph-native replacement exists yet, so
        # this stays empty rather than calling a method that no longer exists.
        source_key_profile = None

        top_source_paths = (source_key_profile or {}).get("top_paths") or []
        source_backed_related_nodes = [
            {
                "id": f"SourcePath:{path.get('label')}",
                "type": "SourcePath",
                "label": path.get("label"),
                "source_table": path.get("table"),
                "source_pk": f"{(source_key_profile or {}).get('center_key_col', 'key')}={instance_id}; {path.get('label_col') or 'label'}={path.get('label')}",
            }
            for path in top_source_paths
        ]
        source_backed_related_edges = [
            {
                "source": center_node,
                "target": f"SourcePath:{path.get('label')}",
                "label": "source path metric",
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table"),
                "provenance": "source-key metric aggregation",
            }
            for path in top_source_paths
        ]

        def compact_node(node):
            return {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": node.get("label"),
                "source_table": node.get("source_table"),
                "source_pk": node.get("source_pk"),
                "ontology_artifact": node.get("ontology_artifact"),
                "status": node.get("status"),
            }

        def compact_edge(edge):
            properties = edge.get("properties") or {}
            return {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "label": edge.get("label"),
                "link_key": edge.get("link_key"),
                "status": edge.get("status"),
                "projection_source": edge.get("projection_source"),
                "source_url": edge.get("source_url") or properties.get("source_url"),
                "properties": {
                    key: value for key, value in properties.items()
                    if key not in {"evidence_refs", "evidence_quote"} and value not in (None, "", [])
                },
            }

        retrieval_context = None
        if local_rag_context.get("approved"):
            retrieval_context = {
                "mode": local_rag_context.get("retrieval_mode"),
                "center": local_rag_context.get("center"),
                "nodes": (local_rag_context.get("nodes") or [])[:node_limit],
                "edges": (local_rag_context.get("edges") or [])[:edge_limit],
                "semantic_items": local_rag_context.get("semantic_items") or [],
                "evidence": local_rag_context.get("evidence") or [],
                "context_text": local_rag_context.get("context_text") or "",
                "scope": local_rag_context.get("scope") or {},
                "eval": local_rag_context.get("eval") or {},
            }

        if retrieval_context:
            retrieval_mode = "local_graph_context"
        elif approved:
            retrieval_mode = "approved_graph_scope"
        elif demo_mode:
            retrieval_mode = "explicit_demo_mode"
        else:
            retrieval_mode = "degraded_no_approved_projection"

        return {
            "center_node": center_node,
            "retrieval_mode": retrieval_mode,
            "approved": approved,
            "projection_source": projection_source,
            "demo_mode": demo_mode,
            "degraded_reason": None if approved or demo_mode else self._missing_projection_reason(),
            "depth": depth,
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "degree": {
                "center": len(center_edges),
                "visible_graph_center": len(center_edges),
                "by_link": degree_by_link,
                "neighbor_type_counts": neighbor_type_counts,
                "source_key_row_degree": (source_key_profile or {}).get("total_key_rows"),
                "source_key_top_path_count": len(top_source_paths),
            },
            "related_nodes": [compact_node(node) for node in scoped_nodes[:node_limit]],
            "related_edges": [compact_edge(edge) for edge in scoped_edges[:edge_limit]],
            "source_backed_related_nodes": source_backed_related_nodes[:node_limit],
            "source_backed_related_edges": source_backed_related_edges[:edge_limit],
            "truncated": {
                "nodes": len(scoped_nodes) > node_limit,
                "edges": len(scoped_edges) > edge_limit,
                "source_graph": (graph.get("limits") or {}).get("truncated"),
            },
            "source_key_metrics": source_key_profile,
            "retrieval_context": retrieval_context,
            "context_text": (retrieval_context or {}).get("context_text", ""),
        }

    def _diversify_edges_by_relation(self, edges):
        buckets = {}
        for edge in edges or []:
            relation = edge.get("label") or edge.get("link_key") or edge.get("kind") or "relation"
            buckets.setdefault(relation, []).append(edge)
        ordered = []
        bucket_items = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
        while bucket_items:
            next_items = []
            for relation, values in bucket_items:
                if values:
                    ordered.append(values.pop(0))
                if values:
                    next_items.append((relation, values))
            bucket_items = next_items
        return ordered

    def run_scoped_graph_task(self, tenant, task_key):
        started = time.monotonic()
        task = self._get_task_row(tenant, task_key)
        if task is None:
            return None
        if task.get("status") == "closed":
            raise ValueError("Cannot run a closed task")
        if task.get("status") == "completed":
            self.update_task_status(tenant, task_key, "active")
            task["status"] = "active"
        scope = task.get("scope") or {}
        query_plan = [
            "Validate tenant-scoped graph task and approved-only scope.",
            "Read only the selected node or edge evidence path from Graph Explorer.",
            "Propose a draft finding without approving, ingesting, or changing canonical graph data.",
        ]
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
        ]
        evidence_paths = list(scope.get("evidence_paths") or [])
        if not evidence_paths:
            tool_calls[0]["status"] = "blocked"
            output = {
                "summary": "Scoped graph reasoning blocked because no evidence paths were provided.",
                "unsupported_claims": ["missing evidence path"],
            }
            eval_result = {
                "passed": False,
                "approved_only": True,
                "draft_only": True,
                "unsupported_claims": ["missing evidence path"],
                "evidence_path_count": 0,
            }
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}
        center_node = scope.get("center_node")
        scope_depth = int(scope.get("depth") or 1)
        scope_limit = int(scope.get("node_limit") or 200)
        scope_edge_limit = int(scope.get("edge_limit") or scope_limit)
        graph_context = self._scoped_graph_prompt_context(
            tenant,
            center_node,
            scope_depth,
            scope_limit,
            scope_edge_limit,
            demo_mode=self._explicit_demo_mode(scope),
        )
        if not self._approved_or_explicit_demo_graph_context(graph_context):
            tool_calls[0]["status"] = "blocked"
            tool_calls[0]["projection_source"] = graph_context.get("projection_source")
            tool_calls[0]["demo_mode"] = graph_context.get("demo_mode", False)
            tool_calls[0]["degraded_reason"] = graph_context.get("degraded_reason")
            tool_calls[1]["status"] = "skipped"
            output, eval_result = self._missing_projection_block_payload(tenant, graph_context, evidence_paths)
            run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "blocked", started)
            return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"), depth=scope_depth, limit=scope_limit)
        structured_response = (
            self._reasoning_response_v1(tenant, task, scope, structured_answer, evidence_paths, graph_context)
            if structured_answer
            else None
        )
        if structured_answer:
            query_plan = [
                "Validate tenant-scoped entity profile task and approved-only graph scope.",
                "Read the selected entity node evidence path from the approved graph.",
                "Materialize the response metrics into controlled evidence for review.",
                "Persist a draft finding from reasoning_response_v1 with evidence limits and next validation questions.",
            ]
            tool_calls.insert(
                1,
                {
                    "tool": "entity_profile_aggregate",
                    "tenant_id": tenant.tenant_id,
                    "approved_only": True,
                    "write_scope": "read_only_source_aggregate",
                    "status": "completed",
                },
            )
            metrics = structured_answer.get("metrics") or {}
            aggregate_evidence, ranking_summary = entity_profile_aggregate_evidence(
                tenant.tenant_id,
                task_key,
                center_node,
                scope_depth,
                metrics,
            )
            evidence_paths.append(aggregate_evidence)
            title = structured_response["answer"]["title"]
            conclusion = structured_response["answer"]["conclusion"]
        else:
            title, conclusion = self._edge_or_scoped_finding_text(tenant, task, scope)
        finding = scoped_graph_finding(
            task_key,
            title,
            conclusion,
            evidence_paths,
            structured_answer,
            structured_response,
            now_ms=int(time.time() * 1000),
        )
        output = {
            "summary": conclusion,
            "finding_keys": [finding["canonical_key"]],
            "unsupported_claims": [],
            "draft_only": True,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
            "degraded_reason": graph_context.get("degraded_reason"),
            **({"structured_answer": structured_answer, "structured_response": structured_response} if structured_answer else {}),
        }
        eval_result = {
            "passed": True,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "tenant_id": tenant.tenant_id,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
        }
        if structured_response and structured_response.get("conclusion_evaluation"):
            eval_result["conclusion_evaluation"] = structured_response["conclusion_evaluation"]
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        finding_row = self._record_finding(tenant, run, finding)
        return {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [finding_row], "approved": True}

    def _edge_or_scoped_finding_text(self, tenant, task, scope):
        center_edge = scope.get("center_edge") or {}
        question = task.get("question") or "the scoped graph question"
        if center_edge.get("source") and center_edge.get("target"):
            source = center_edge["source"]
            target = center_edge["target"]
            edge = self.instance_repository.edge_detail(tenant, source, target)
            title = f"{source} -> {target} approved edge evidence"
            conclusion = (
                f'For the question "{question}", the approved graph contains the selected '
                f"{source} -> {target} relationship. "
            )
            if edge:
                conclusion += (
                    f"The relationship is supported by {edge.get('source_ref') or 'source-row evidence'} "
                    f"and ontology link {edge.get('ontology_link') or edge.get('link_key') or 'link'}. "
                )
            conclusion += "This is a draft answer for review and does not change canonical ontology or graph."
            return title, conclusion
        center = scope.get("center_node") or f"{center_edge.get('source', 'scope')} -> {center_edge.get('target', 'scope')}"
        return (
            f"Scoped answer for {center}",
            (
                f'For the question "{question}", the run is constrained to the selected approved graph scope. '
                "This is a draft answer for review and does not change canonical ontology or graph."
            ),
        )

    def _is_legacy_scoped_finding(self, finding):
        title = (finding.get("title") or "").lower()
        conclusion = (finding.get("conclusion") or "").lower()
        return (
            "scoped graph reasoning remains draft-only" in title
            or "created from graph explorer evidence" in conclusion
            or "work snapshot" in title
            or "approved order relationships" in title
            or "loaded in the current evidence scope" in conclusion
        )

    def _normalize_scoped_finding_display(self, tenant, finding):
        task = finding.get("task") or {}
        if not task:
            task = {
                "question": finding.get("question"),
                "scope": finding.get("task_scope") or {},
            }
        scope = task.get("scope") or finding.get("task_scope") or {}
        structured_answer = finding.get("structured_answer") or (finding.get("recommended_action") or {}).get("structured_answer")
        structured_response = finding.get("structured_response") or (finding.get("recommended_action") or {}).get("structured_response")
        center_node = scope.get("center_node")
        if not structured_answer:
            engine = ReasoningEngine(self.instance_repository)
            structured_answer = engine.analyze(
                tenant,
                center_node,
                task.get("question"),
                depth=int(scope.get("depth") or 1),
                limit=int(scope.get("node_limit") or 200),
            )
            if structured_answer:
                raw_recommended_action = finding.get("recommended_action") or {}
                finding["recommended_action"] = {
                    **raw_recommended_action,
                    "structured_answer": structured_answer,
                }
                finding["structured_answer"] = structured_answer
                for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                    finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        elif center_node and not ((structured_answer.get("metrics") or {}).get("source_key_profile") or {}).get("related_tables"):
            try:
                refreshed_answer = ReasoningEngine(self.instance_repository).analyze(
                    tenant,
                    center_node,
                    task.get("question"),
                    depth=int(scope.get("depth") or 1),
                    limit=int(scope.get("node_limit") or 200),
                )
            except Exception:
                refreshed_answer = None
            if refreshed_answer:
                structured_answer = refreshed_answer
                raw_recommended_action = finding.get("recommended_action") or {}
                finding["recommended_action"] = {
                    **raw_recommended_action,
                    "structured_answer": structured_answer,
                }
                finding["structured_answer"] = structured_answer
                for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                    finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        if not structured_answer and not self._is_legacy_scoped_finding(finding):
            return finding
        raw_title = finding.get("title")
        raw_conclusion = finding.get("conclusion")
        if structured_answer:
            existing_graph_context = (structured_response or {}).get("graph_context") if isinstance(structured_response, dict) else None
            structured_response = self._reasoning_response_v1(
                tenant,
                task,
                scope,
                structured_answer,
                finding.get("supporting_evidence") or [],
                graph_context=existing_graph_context,
            )
            raw_recommended_action = finding.get("recommended_action") or {}
            finding["recommended_action"] = {
                **raw_recommended_action,
                "structured_response": structured_response,
            }
            finding["structured_response"] = structured_response
            answer = structured_response.get("answer") or {}
            title = answer.get("title") or structured_answer.get("title") or raw_title
            conclusion = answer.get("conclusion") or structured_answer.get("profile_summary") or raw_conclusion
            finding["confidence"] = max(float(finding.get("confidence") or 0), 0.78)
            metrics = structured_answer.get("metrics") or {}
            evidence_paths = list(finding.get("supporting_evidence") or [])
            if not any(path.get("kind") == "controlled_aggregate" for path in evidence_paths):
                rankings = metrics.get("rankings") or []
                label_val = metrics.get("label") or scope.get("center_node")
                if rankings:
                    ranking_text = "; ".join(
                        f"{r['my_count']} {r['target_type']} (#{r['rank']}/{r['total_peers']}, {r['level']})"
                        for r in rankings if r.get("my_count", 0) > 0
                    ) or "no ranked relationships"
                    summary_text = f"{label_val}: {ranking_text}"
                else:
                    neighbor_types = metrics.get("neighbor_types") or {}
                    neighbor_text = ", ".join(f"{c} {t}" for t, c in sorted(neighbor_types.items())) if neighbor_types else "scope data"
                    summary_text = f"{label_val} has {metrics.get('neighbor_count', 0)} related entities ({neighbor_text})"
                evidence_paths.append(
                    {
                        "kind": "controlled_aggregate",
                        "label": f"{label_val} Business Profile",
                        "summary": summary_text,
                        "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task.get('canonical_key') or '')}",
                        "source_ref": f"{metrics.get('object_type', 'entity')} + peer ranking",
                        "payload": metrics,
                    }
                )
                finding["supporting_evidence"] = evidence_paths
        else:
            title, conclusion = self._edge_or_scoped_finding_text(tenant, task, scope)
        finding["raw_title"] = raw_title
        finding["raw_conclusion"] = raw_conclusion
        finding["title"] = title
        finding["conclusion"] = conclusion
        finding["display_normalized"] = True
        return finding

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

    def _record_run(self, tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, status, started):
        run_key = f"{task['canonical_key']}:run:{int(time.time() * 1000)}"
        latency_ms = int((time.monotonic() - started) * 1000)
        prompt_version = "graph-scope-reasoning-v1"
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_runs
                    (task_id, project_id, run_key, agent_name, prompt_version,
                     query_plan_json, tool_calls_json, evidence_paths_json,
                    output_json, eval_result_json, status, latency_ms, cost_estimate, created_at)
                    VALUES
                    (:task_id, :tenant_id, :run_key, 'ReasoningWorkbenchAgent', :prompt_version,
                     :query_plan_json, :tool_calls_json, :evidence_paths_json,
                     :output_json, :eval_result_json, :status, :latency_ms, 0.0, NOW())
                    RETURNING id, project_id, run_key, agent_name, prompt_version,
                              query_plan_json, tool_calls_json, evidence_paths_json,
                              output_json, eval_result_json, status, latency_ms,
                              cost_estimate, created_at
                    """
                ),
                {
                    "task_id": task["id"],
                    "tenant_id": tenant.tenant_id,
                    "run_key": run_key,
                    "prompt_version": prompt_version,
                    "query_plan_json": _json_dump(query_plan),
                    "tool_calls_json": _json_dump(tool_calls),
                    "evidence_paths_json": _json_dump(evidence_paths),
                    "output_json": _json_dump(output),
                    "eval_result_json": _json_dump(eval_result),
                    "status": status,
                    "latency_ms": latency_ms,
                },
            ).mappings().first()
            if status == "completed":
                conn.execute(
                    text("UPDATE aletheia_reasoning_tasks SET status = 'completed', updated_at = NOW() WHERE id = :task_id AND status = 'active'"),
                    {"task_id": task["id"]},
                )
                task["status"] = "completed"
        return self._run_to_dict(row)

    def _record_finding(self, tenant, run, finding):
        with self.metadata_engine_for(tenant).begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_reasoning_findings
                    (run_id, project_id, canonical_key, title, conclusion, confidence,
                     supporting_evidence_json, counter_evidence_json, recommended_action_json,
                     status, version, source_agent, created_at, updated_at)
                    VALUES
                    (:run_id, :tenant_id, :canonical_key, :title, :conclusion, :confidence,
                     :supporting_evidence_json, :counter_evidence_json, :recommended_action_json,
                     'draft', 1, 'ReasoningWorkbenchAgent', NOW(), NOW())
                    ON CONFLICT (project_id, canonical_key) DO UPDATE SET
                      run_id = EXCLUDED.run_id,
                      title = EXCLUDED.title,
                      conclusion = EXCLUDED.conclusion,
                      confidence = EXCLUDED.confidence,
                      supporting_evidence_json = EXCLUDED.supporting_evidence_json,
                      counter_evidence_json = EXCLUDED.counter_evidence_json,
                      recommended_action_json = EXCLUDED.recommended_action_json,
                      status = 'draft',
                      version = aletheia_reasoning_findings.version + 1,
                      updated_at = NOW()
                    RETURNING id, run_id, project_id, canonical_key, title, conclusion, confidence,
                              supporting_evidence_json, counter_evidence_json, recommended_action_json,
                              status, version, source_agent, created_at, updated_at
                    """
                ),
                {
                    "run_id": run["id"],
                    "tenant_id": tenant.tenant_id,
                    "canonical_key": finding["canonical_key"],
                    "title": finding["title"],
                    "conclusion": finding["conclusion"],
                    "confidence": finding["confidence"],
                    "supporting_evidence_json": _json_dump(finding["supporting_evidence"]),
                    "counter_evidence_json": _json_dump(finding["counter_evidence"]),
                    "recommended_action_json": _json_dump(finding["recommended_action"]),
                },
            ).mappings().first()
        return self._finding_to_dict(row)

    def _task_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "question": row["question"],
            "scope": _load_json(row["scope_json"], {}),
            "allowed_tools": _load_json(row["allowed_tools_json"], []),
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _run_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "run_key": row["run_key"],
            "agent_name": row["agent_name"],
            "prompt_version": row["prompt_version"],
            "query_plan": _load_json(row["query_plan_json"], []),
            "tool_calls": _load_json(row["tool_calls_json"], []),
            "evidence_paths": _load_json(row["evidence_paths_json"], []),
            "output": _load_json(row["output_json"], {}),
            "eval_result": _load_json(row["eval_result_json"], {}),
            "status": row["status"],
            "latency_ms": row["latency_ms"],
            "cost_estimate": row["cost_estimate"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
        }

    def _finding_to_dict(self, row):
        recommended_action = _load_json(row["recommended_action_json"], {})
        structured_answer = recommended_action.get("structured_answer") or {}
        structured_response = recommended_action.get("structured_response") or {}
        supporting_evidence = _load_json(row["supporting_evidence_json"], [])
        deep_graph_profile = recommended_action.get("deep_graph_profile") or self._deep_graph_profile(supporting_evidence)
        finding = {
            "id": row["id"],
            "run_id": row["run_id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "title": row["title"],
            "conclusion": row["conclusion"],
            "confidence": row["confidence"],
            "supporting_evidence": supporting_evidence,
            "deep_graph_profile": deep_graph_profile,
            "finding_emphasis": recommended_action.get("finding_emphasis") or deep_graph_profile.get("finding_emphasis"),
            "counter_evidence": _load_json(row["counter_evidence_json"], []),
            "recommended_action": recommended_action,
            "status": row["status"],
            "version": row["version"],
            "source_agent": row["source_agent"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }
        if structured_answer:
            finding["structured_answer"] = structured_answer
            for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        if structured_response:
            finding["structured_response"] = structured_response
        return finding

    def _autopilot_session_to_dict(self, row):
        return {
            "id": row["id"],
            "tenant_id": row["project_id"],
            "session_key": row["session_key"],
            "objective": row["objective"],
            "scope": _load_json(row["scope_json"], {}),
            "budget": _load_json(row["budget_json"], {}),
            "safety_profile": _load_json(row["safety_profile_json"], {}),
            "status": row["status"],
            "created_by": row["created_by"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _autopilot_hypothesis_to_dict(self, row):
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "tenant_id": row["project_id"],
            "hypothesis_key": row["hypothesis_key"],
            "title": row["title"],
            "rationale": row["rationale"],
            "status": row["status"],
            "priority": row["priority"],
            "evidence_plan": _load_json(row["evidence_plan_json"], []),
            "reasoning_task_keys": _load_json(row["reasoning_task_keys_json"], []),
            "pruned_reason": row["pruned_reason"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }

    def _autopilot_candidate_to_dict(self, row):
        evidence_chain = _load_json(row["evidence_chain_json"], [])
        deep_graph_profile = self._deep_graph_profile(evidence_chain)
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "hypothesis_id": row["hypothesis_id"],
            "tenant_id": row["project_id"],
            "canonical_key": row["canonical_key"],
            "title": row["title"],
            "conclusion": row["conclusion"],
            "value_score": row["value_score"],
            "confidence": row["confidence"],
            "novelty_score": row["novelty_score"],
            "impact_score": row["impact_score"],
            "evidence_chain": evidence_chain,
            "deep_graph_profile": deep_graph_profile,
            "finding_emphasis": deep_graph_profile["finding_emphasis"],
            "evidence_limits": _load_json(row["evidence_limits_json"], []),
            "suggested_action": _load_json(row["suggested_action_json"], {}),
            "status": row["status"],
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        }
