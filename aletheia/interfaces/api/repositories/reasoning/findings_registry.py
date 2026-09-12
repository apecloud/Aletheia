"""FindingsRegistryMixin: the decorated/filterable/groupable findings registry view
(list_findings_registry and its supporting decoration, filtering, sorting, and
grouping helpers) plus the ACTIVE_FINDING_STATUSES/INACTIVE_FINDING_STATUSES
classification used across the reasoning repository to distinguish findings
that are live reasoning context from ones that are audit-only.

Extracted from the monolithic reasoning.py -- part of the reasoning/ package
split. No behavior change.
"""

from sqlalchemy import text
from aletheia.interfaces.api.helpers import _load_json


class FindingsRegistryMixin:
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
