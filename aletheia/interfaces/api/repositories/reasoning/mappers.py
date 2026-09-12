"""MappersMixin: row-dict translation helpers -- persisting reasoning runs/findings
and converting DB rows (tasks, runs, findings, finding actions, reviews, Autopilot
sessions/hypotheses/candidates) into the JSON-shaped dicts the API returns.
Extracted from the monolithic reasoning.py -- part of the reasoning/ package split.
No behavior change.
"""

import time
from datetime import datetime
from sqlalchemy import text
from aletheia.interfaces.api.helpers import _json_dump, _load_json


class MappersMixin:
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
