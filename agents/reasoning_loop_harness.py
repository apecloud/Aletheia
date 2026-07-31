"""Loop-engineering harness for the reasoning process.

The reasoning workbench already records tasks, runs, evidence paths, eval
results, and draft findings. This harness turns those traces into repeatable
health metrics and a concrete next focus for the next reasoning cycle without
approving findings or mutating graph data.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import create_engine, text


DEFAULT_REASONING_LOOP_CONFIG: dict[str, Any] = {
    "loop_id": "reasoning-process-loop-v1",
    "targets": {
        "min_completed_run_ratio": 0.9,
        "min_eval_pass_ratio": 0.9,
        "min_evidence_path_ratio": 0.9,
        "min_structured_response_ratio": 0.8,
        "min_trace_observability_ratio": 0.9,
        "min_final_result_state_ratio": 0.9,
        "min_observable_retry_path_ratio": 1.0,
        "min_finding_evidence_ratio": 0.95,
        "min_findings_per_completed_run": 0.8,
        "max_pending_review_ratio": 0.5,
        "max_low_confidence_finding_ratio": 0.2,
        "low_confidence_threshold": 0.55,
        "max_latency_ms": 120000,
    },
    "next_action_policy": {
        "if_run_failed": "run_health_diagnosis",
        "if_eval_contract_failed": "eval_contract_repair",
        "if_evidence_missing": "evidence_path_repair",
        "if_structured_response_missing": "response_schema_repair",
        "if_trace_observability_low": "trace_observability_repair",
        "if_final_result_state_missing": "final_result_state_repair",
        "if_retry_path_observability_low": "retry_path_observability_repair",
        "if_finding_generation_low": "finding_generation_repair",
        "if_finding_evidence_low": "finding_evidence_repair",
        "if_low_confidence_high": "confidence_calibration_repair",
        "if_review_queue_high": "review_queue_drain",
        "if_latency_high": "reasoning_latency_diagnosis",
        "if_targets_met": "continue_reasoning_monitoring",
    },
    "repair_policy": {
        "max_items": 100,
        "frontier_priority": 150,
    },
}


def load_reasoning_loop_config(path: str | Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_REASONING_LOOP_CONFIG))
    if not path:
        return config
    with open(path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    return _deep_merge(config, loaded if isinstance(loaded, dict) else {})


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def evaluate_reasoning_loop(
    metadata_db_url: str,
    tenant_id: str,
    *,
    config: dict[str, Any] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    config = _deep_merge(json.loads(json.dumps(DEFAULT_REASONING_LOOP_CONFIG)), config or {})
    engine = create_engine(metadata_db_url)
    with engine.connect() as conn:
        tasks = _load_tasks(conn, tenant_id, limit=limit)
        runs = _load_runs(conn, tenant_id, limit=limit)
        findings = _load_findings(conn, tenant_id, limit=limit)
    metrics = _metrics(tasks, runs, findings, config)
    verdict = _verdict(metrics, config)
    repair_plan = build_reasoning_repair_plan(
        tenant_id,
        tasks,
        runs,
        findings,
        next_focus=verdict.get("next_focus"),
        config=config,
    )
    return {
        "loop_id": config.get("loop_id"),
        "tenant": tenant_id,
        "status": "ready" if tasks or runs or findings else "no_reasoning_traces",
        "metrics": metrics,
        "verdict": verdict,
        "repair_plan": repair_plan,
    }


def build_reasoning_repair_plan(
    tenant_id: str,
    tasks: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    *,
    next_focus: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _deep_merge(json.loads(json.dumps(DEFAULT_REASONING_LOOP_CONFIG)), config or {})
    max_items = max(1, int(((config.get("repair_policy") or {}).get("max_items")) or 100))
    task_by_id = {item["id"]: item for item in tasks}
    findings_by_run: dict[int, list[dict[str, Any]]] = {}
    for finding in findings:
        findings_by_run.setdefault(int(finding["run_id"]), []).append(finding)

    items: list[dict[str, Any]] = []
    if next_focus == "run_health_diagnosis":
        for run in runs:
            if str(run.get("status") or "").lower() in {"blocked", "failed", "error"}:
                items.append(_run_repair_item(tenant_id, run, task_by_id, "reasoning run did not complete"))
            if len(items) >= max_items:
                break
    elif next_focus == "eval_contract_repair":
        for run in runs:
            eval_result = run.get("eval_result") or {}
            if not _eval_passed(eval_result) or not eval_result.get("approved_only") or not eval_result.get("draft_only"):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "run eval contract failed or omitted boundaries"))
            if len(items) >= max_items:
                break
    elif next_focus == "evidence_path_repair":
        for run in runs:
            if not run.get("evidence_paths"):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "run has no evidence paths"))
            if len(items) >= max_items:
                break
    elif next_focus == "response_schema_repair":
        for run in runs:
            if not _has_structured_response(run):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "run output lacks reasoning_response_v1"))
            if len(items) >= max_items:
                break
    elif next_focus == "trace_observability_repair":
        for run in runs:
            if not _has_observable_trace(run):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "run lacks observable reasoning trace fields"))
            if len(items) >= max_items:
                break
    elif next_focus == "final_result_state_repair":
        for run in runs:
            if str(run.get("status") or "").lower() == "completed" and not _has_final_result_state(run):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "completed run lacks explicit final result state"))
            if len(items) >= max_items:
                break
    elif next_focus == "retry_path_observability_repair":
        for run in runs:
            if _has_retryable_tool_error(run) and not _has_observable_retry_path(run):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "retryable tool failure lacks observable retry path"))
            if len(items) >= max_items:
                break
    elif next_focus == "finding_generation_repair":
        for run in runs:
            if str(run.get("status") or "").lower() == "completed" and not findings_by_run.get(int(run["id"])):
                items.append(_run_repair_item(tenant_id, run, task_by_id, "completed run produced no finding"))
            if len(items) >= max_items:
                break
    elif next_focus in {"finding_evidence_repair", "confidence_calibration_repair", "review_queue_drain"}:
        for finding in findings:
            if next_focus == "finding_evidence_repair" and finding.get("supporting_evidence"):
                continue
            if next_focus == "confidence_calibration_repair" and not _is_low_confidence(finding, config):
                continue
            if next_focus == "review_queue_drain" and str(finding.get("status") or "").lower() not in {"draft", "needs_more_evidence"}:
                continue
            items.append(_finding_repair_item(tenant_id, finding, next_focus))
            if len(items) >= max_items:
                break
    elif next_focus == "reasoning_latency_diagnosis":
        max_latency = int((config.get("targets") or {}).get("max_latency_ms") or 120000)
        for run in runs:
            if int(run.get("latency_ms") or 0) > max_latency:
                items.append(_run_repair_item(tenant_id, run, task_by_id, "run latency exceeds target"))
            if len(items) >= max_items:
                break

    return {
        "focus": next_focus,
        "actionable": bool(items),
        "item_count": len(items),
        "items": items,
    }


def _load_tasks(conn, tenant_id: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id, project_id, canonical_key, question, scope_json,
                   allowed_tools_json, status, created_at, updated_at
            FROM aletheia_reasoning_tasks
            WHERE project_id = :tenant_id
            ORDER BY updated_at DESC, id DESC
            LIMIT :limit
            """
        ),
        {"tenant_id": tenant_id, "limit": max(1, int(limit or 200))},
    ).mappings().all()
    return [
        {
            **dict(row),
            "scope": _json_load(row["scope_json"], {}),
            "allowed_tools": _json_load(row["allowed_tools_json"], []),
        }
        for row in rows
    ]


def _load_runs(conn, tenant_id: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT r.id, r.task_id, r.project_id, r.run_key, r.agent_name,
                   r.prompt_version, r.query_plan_json, r.tool_calls_json,
                   r.evidence_paths_json, r.output_json, r.eval_result_json,
                   r.status, r.latency_ms, r.cost_estimate, r.created_at,
                   t.canonical_key AS task_key, t.question
            FROM aletheia_reasoning_runs r
            JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
            WHERE r.project_id = :tenant_id
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT :limit
            """
        ),
        {"tenant_id": tenant_id, "limit": max(1, int(limit or 200))},
    ).mappings().all()
    result = []
    for row in rows:
        result.append(
            {
                **dict(row),
                "query_plan": _json_load(row["query_plan_json"], []),
                "tool_calls": _json_load(row["tool_calls_json"], []),
                "evidence_paths": _json_load(row["evidence_paths_json"], []),
                "output": _json_load(row["output_json"], {}),
                "eval_result": _json_load(row["eval_result_json"], {}),
            }
        )
    return result


def _load_findings(conn, tenant_id: str, *, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id, run_id, project_id, canonical_key, title, conclusion,
                   confidence, supporting_evidence_json, counter_evidence_json,
                   recommended_action_json, status, version, source_agent,
                   created_at, updated_at
            FROM aletheia_reasoning_findings
            WHERE project_id = :tenant_id
            ORDER BY updated_at DESC, id DESC
            LIMIT :limit
            """
        ),
        {"tenant_id": tenant_id, "limit": max(1, int(limit or 200))},
    ).mappings().all()
    return [
        {
            **dict(row),
            "supporting_evidence": _json_load(row["supporting_evidence_json"], []),
            "counter_evidence": _json_load(row["counter_evidence_json"], []),
            "recommended_action": _json_load(row["recommended_action_json"], {}),
        }
        for row in rows
    ]


def _metrics(tasks: list[dict[str, Any]], runs: list[dict[str, Any]], findings: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    task_count = len(tasks)
    run_count = len(runs)
    finding_count = len(findings)
    completed_runs = [r for r in runs if str(r.get("status") or "").lower() == "completed"]
    blocked_runs = [r for r in runs if str(r.get("status") or "").lower() in {"blocked", "failed", "error"}]
    eval_passed_runs = [r for r in runs if _eval_passed(r.get("eval_result") or {})]
    evidence_runs = [r for r in runs if bool(r.get("evidence_paths"))]
    structured_runs = [r for r in runs if _has_structured_response(r)]
    observable_runs = [r for r in runs if _has_observable_trace(r)]
    linked_retry_runs = [r for r in runs if _has_linked_retry(r)]
    final_result_runs = [r for r in runs if _has_final_result_state(r)]
    draft_only_runs = [r for r in runs if (r.get("eval_result") or {}).get("draft_only") is True]
    approved_only_runs = [r for r in runs if (r.get("eval_result") or {}).get("approved_only") is True]
    unsupported_claim_runs = [r for r in runs if (r.get("eval_result") or {}).get("unsupported_claims")]
    retryable_tool_error_runs = [r for r in runs if _has_retryable_tool_error(r)]
    observable_retry_path_runs = [r for r in runs if _has_observable_retry_path(r)]
    runs_with_findings = {int(f["run_id"]) for f in findings}
    findings_with_evidence = [f for f in findings if bool(f.get("supporting_evidence"))]
    pending_findings = [f for f in findings if str(f.get("status") or "").lower() in {"draft", "needs_more_evidence"}]
    low_confidence = [f for f in findings if _is_low_confidence(f, config)]
    latencies = [int(r.get("latency_ms") or 0) for r in runs]
    status_counts = _status_counts(tasks)
    finding_status_counts = _status_counts(findings)
    return {
        "task_count": task_count,
        "active_task_count": status_counts.get("active", 0),
        "completed_task_count": status_counts.get("completed", 0),
        "closed_task_count": status_counts.get("closed", 0),
        "run_count": run_count,
        "completed_run_count": len(completed_runs),
        "blocked_run_count": len(blocked_runs),
        "completed_run_ratio": _ratio(len(completed_runs), run_count),
        "eval_pass_ratio": _ratio(len(eval_passed_runs), run_count),
        "evidence_path_ratio": _ratio(len(evidence_runs), run_count),
        "structured_response_ratio": _ratio(len(structured_runs), run_count),
        "observable_trace_count": len(observable_runs),
        "trace_observability_ratio": _ratio(len(observable_runs), run_count),
        "linked_retry_run_count": len(linked_retry_runs),
        "final_result_state_count": len(final_result_runs),
        "final_result_state_ratio": _ratio(len(final_result_runs), run_count),
        "draft_only_ratio": _ratio(len(draft_only_runs), run_count),
        "approved_only_ratio": _ratio(len(approved_only_runs), run_count),
        "unsupported_claim_run_count": len(unsupported_claim_runs),
        "retryable_tool_error_run_count": len(retryable_tool_error_runs),
        "observable_retry_path_run_count": len(observable_retry_path_runs),
        "observable_retry_path_ratio": _ratio(len(observable_retry_path_runs), len(retryable_tool_error_runs)),
        "avg_evidence_paths_per_run": round(mean([len(r.get("evidence_paths") or []) for r in runs]), 3) if runs else 0.0,
        "avg_latency_ms": round(mean(latencies), 1) if latencies else 0.0,
        "max_latency_ms": max(latencies) if latencies else 0,
        "finding_count": finding_count,
        "findings_per_completed_run": _ratio(finding_count, len(completed_runs)),
        "completed_runs_without_findings": sum(1 for r in completed_runs if int(r["id"]) not in runs_with_findings),
        "finding_with_evidence_ratio": _ratio(len(findings_with_evidence), finding_count),
        "pending_review_count": len(pending_findings),
        "pending_review_ratio": _ratio(len(pending_findings), finding_count),
        "low_confidence_finding_count": len(low_confidence),
        "low_confidence_finding_ratio": _ratio(len(low_confidence), finding_count),
        "finding_status_counts": finding_status_counts,
    }


def _verdict(metrics: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    targets = config.get("targets") or {}
    policy = config.get("next_action_policy") or {}
    reasons: list[str] = []
    focus = None
    if metrics["blocked_run_count"] > 0 or metrics["completed_run_ratio"] < float(targets.get("min_completed_run_ratio", 0.9)):
        focus = policy.get("if_run_failed", "run_health_diagnosis")
        reasons.append("reasoning runs are blocked, failed, or below completion target")
    elif metrics["trace_observability_ratio"] < float(targets.get("min_trace_observability_ratio", 0.9)):
        focus = policy.get("if_trace_observability_low", "trace_observability_repair")
        reasons.append("reasoning runs lack observable query plan, tool calls, evidence paths, eval result, or structured output")
    elif metrics["final_result_state_ratio"] < float(targets.get("min_final_result_state_ratio", 0.9)):
        focus = policy.get("if_final_result_state_missing", "final_result_state_repair")
        reasons.append("completed reasoning runs lack explicit final result state")
    elif metrics["observable_retry_path_ratio"] < float(targets.get("min_observable_retry_path_ratio", 1.0)):
        focus = policy.get("if_retry_path_observability_low", "retry_path_observability_repair")
        reasons.append("retryable tool failures are not linked to observable retry paths")
    elif metrics["eval_pass_ratio"] < float(targets.get("min_eval_pass_ratio", 0.9)) or metrics["draft_only_ratio"] < 1.0 or metrics["approved_only_ratio"] < 1.0:
        focus = policy.get("if_eval_contract_failed", "eval_contract_repair")
        reasons.append("run eval contract is missing pass, draft_only, or approved_only guarantees")
    elif metrics["evidence_path_ratio"] < float(targets.get("min_evidence_path_ratio", 0.9)):
        focus = policy.get("if_evidence_missing", "evidence_path_repair")
        reasons.append("reasoning runs lack evidence paths")
    elif metrics["structured_response_ratio"] < float(targets.get("min_structured_response_ratio", 0.8)):
        focus = policy.get("if_structured_response_missing", "response_schema_repair")
        reasons.append("reasoning runs lack reasoning_response_v1 structured output")
    elif metrics["findings_per_completed_run"] < float(targets.get("min_findings_per_completed_run", 0.8)):
        focus = policy.get("if_finding_generation_low", "finding_generation_repair")
        reasons.append("completed reasoning runs are not producing reviewable findings")
    elif metrics["finding_with_evidence_ratio"] < float(targets.get("min_finding_evidence_ratio", 0.95)):
        focus = policy.get("if_finding_evidence_low", "finding_evidence_repair")
        reasons.append("reasoning findings lack supporting evidence")
    elif metrics["low_confidence_finding_ratio"] > float(targets.get("max_low_confidence_finding_ratio", 0.2)):
        focus = policy.get("if_low_confidence_high", "confidence_calibration_repair")
        reasons.append("too many reasoning findings fall below confidence target")
    elif metrics["pending_review_ratio"] > float(targets.get("max_pending_review_ratio", 0.5)):
        focus = policy.get("if_review_queue_high", "review_queue_drain")
        reasons.append("reasoning finding review queue is above target")
    elif metrics["max_latency_ms"] > int(targets.get("max_latency_ms") or 120000):
        focus = policy.get("if_latency_high", "reasoning_latency_diagnosis")
        reasons.append("reasoning latency exceeds target")
    else:
        focus = policy.get("if_targets_met", "continue_reasoning_monitoring")
        reasons.append("reasoning loop targets met")
    return {"next_focus": focus, "reasons": reasons}


def _run_repair_item(tenant_id: str, run: dict[str, Any], task_by_id: dict[int, dict[str, Any]], reason: str) -> dict[str, Any]:
    task = task_by_id.get(int(run.get("task_id") or 0), {})
    task_key = run.get("task_key") or task.get("canonical_key")
    return {
        "kind": "reasoning_run_repair",
        "tenant": tenant_id,
        "run_key": run.get("run_key"),
        "task_key": task_key,
        "status": run.get("status"),
        "reason": reason,
        "frontier_item": {
            "key": f"reasoning-loop:{task_key or run.get('run_key')}",
            "source": "reasoning_loop",
            "source_kind": "reasoning_loop_repair",
            "priority": 150,
            "payload": {
                "repair_reason": reason,
                "run_key": run.get("run_key"),
                "task_key": task_key,
                "question": task.get("question") or run.get("question"),
            },
        },
    }


def _finding_repair_item(tenant_id: str, finding: dict[str, Any], focus: str) -> dict[str, Any]:
    return {
        "kind": "reasoning_finding_repair",
        "tenant": tenant_id,
        "finding_key": finding.get("canonical_key"),
        "status": finding.get("status"),
        "confidence": finding.get("confidence"),
        "reason": focus,
    }


def _has_structured_response(run: dict[str, Any]) -> bool:
    output = run.get("output") or {}
    response = output.get("structured_response")
    return isinstance(response, dict) and response.get("schema_version") == "reasoning_response_v1"


def _has_observable_trace(run: dict[str, Any]) -> bool:
    query_plan = run.get("query_plan")
    tool_calls = run.get("tool_calls")
    evidence_paths = run.get("evidence_paths")
    eval_result = run.get("eval_result")
    if not isinstance(query_plan, list) or not query_plan:
        return False
    if not isinstance(tool_calls, list) or not tool_calls:
        return False
    if not isinstance(evidence_paths, list) or not evidence_paths:
        return False
    if not isinstance(eval_result, dict) or not isinstance(eval_result.get("passed"), bool):
        return False
    if not _has_structured_response(run):
        return False
    for call in tool_calls:
        if not isinstance(call, dict):
            return False
        if not _tool_call_observable_id(call):
            return False
        if not _tool_call_step(call):
            return False
        if not str(call.get("status") or "").strip():
            return False
    return True


def _has_linked_retry(run: dict[str, Any]) -> bool:
    tool_calls = run.get("tool_calls")
    if not isinstance(tool_calls, list):
        return False
    failures: dict[str, dict[str, Any]] = {}
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        call_id = _tool_call_observable_id(call)
        if call_id and _is_retryable_tool_failure(call):
            failures[call_id] = call
    if not failures:
        return False
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        retry_of = _tool_call_retry_target(call)
        if retry_of not in failures:
            continue
        if not _is_successful_tool_call(call):
            continue
        if not _is_later_retry_attempt(call, failures[retry_of]):
            continue
        if _tool_call_retry_scope(call) != _tool_call_retry_scope(failures[retry_of]):
            continue
        return True
    return False


def _has_final_result_state(run: dict[str, Any]) -> bool:
    output = run.get("output") or {}
    final_result = output.get("final_result")
    if not isinstance(final_result, dict):
        return False
    if str(run.get("status") or "").lower() != "completed":
        return False
    return str(final_result.get("status") or "").lower() in {"answered", "completed", "succeeded"}


def _has_retryable_tool_error(run: dict[str, Any]) -> bool:
    for call in run.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        if _is_retryable_tool_failure(call):
            return True
    return False


def _has_observable_retry_path(run: dict[str, Any]) -> bool:
    calls = [call for call in (run.get("tool_calls") or []) if isinstance(call, dict)]
    failed_by_id: dict[str, dict[str, Any]] = {}
    for call in calls:
        call_id = _tool_call_observable_id(call)
        if call_id and _is_retryable_tool_failure(call):
            failed_by_id[call_id] = call

    for call in calls:
        if not _is_successful_tool_call(call):
            continue
        retry_of = _tool_call_retry_target(call)
        if retry_of not in failed_by_id:
            continue
        failed = failed_by_id[retry_of]
        if _tool_call_retry_scope(call) != _tool_call_retry_scope(failed):
            continue
        if not _is_later_retry_attempt(call, failed):
            continue
        if call.get("idempotency_key") and failed.get("idempotency_key"):
            if call.get("idempotency_key") != failed.get("idempotency_key"):
                continue
        return True
    return False


def _is_retryable_tool_failure(call: dict[str, Any]) -> bool:
    status = str(call.get("status") or "").lower()
    error = call.get("error") if isinstance(call.get("error"), dict) else {}
    return status in {"failed", "error"} and (call.get("retryable") is True or error.get("retryable") is True)


def _is_successful_tool_call(call: dict[str, Any]) -> bool:
    return str(call.get("status") or "").lower() in {"completed", "succeeded", "success", "ok"}


def _is_later_retry_attempt(retry_call: dict[str, Any], failed_call: dict[str, Any]) -> bool:
    try:
        failed_attempt = int(failed_call.get("attempt") or 1)
        retry_attempt = int(retry_call.get("attempt") or 0)
    except (TypeError, ValueError):
        return False
    return retry_attempt > failed_attempt


def _tool_call_retry_target(call: dict[str, Any]) -> str | None:
    for key in ("retry_of", "retry_of_step_id", "retry_of_tool_call_id"):
        value = call.get(key)
        if value:
            return str(value).strip()
    return None


def _tool_call_observable_id(call: dict[str, Any]) -> str | None:
    for key in ("id", "step_id", "tool_call_id"):
        value = call.get(key)
        if value:
            return str(value)
    return None


def _tool_call_step(call: dict[str, Any]) -> str | None:
    for key in ("step", "step_id"):
        value = call.get(key)
        if value:
            return str(value)
    return None


def _tool_call_retry_scope(call: dict[str, Any]) -> str | None:
    for key in ("step", "tool_name"):
        value = call.get(key)
        if value:
            return str(value)
    return _tool_call_step(call)


def _eval_passed(eval_result: dict[str, Any]) -> bool:
    return eval_result.get("passed") is True and not eval_result.get("unsupported_claims")


def _is_low_confidence(finding: dict[str, Any], config: dict[str, Any]) -> bool:
    threshold = float((config.get("targets") or {}).get("low_confidence_threshold") or 0.55)
    return float(finding.get("confidence") or 0.0) < threshold


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown").lower()
        counts[status] = counts.get(status, 0) + 1
    return counts


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 1.0
    return round(float(numerator) / float(denominator), 4)


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default
