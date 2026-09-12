"""SchemaMixin: metadata-table DDL (finding-actions, Autopilot session/hypothesis/
candidate tables) and Autopilot budget/safety-profile normalization helpers.
Extracted from the monolithic reasoning.py -- part of the reasoning/ package split.
No behavior change.
"""

from sqlalchemy import text


class SchemaMixin:
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
