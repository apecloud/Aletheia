"""Loop-engineering harness for ontology enrichment.

This module keeps measurement and next-step selection outside the enrichment
agent's extraction path. The agent produces runs and traces; the harness turns
them into repeatable metrics and a concrete next focus for the following cycle.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text


DEFAULT_LOOP_CONFIG: dict[str, Any] = {
    "loop_id": "ontology-enrichment-loop-v1",
    "coverage_targets": {
        "class_with_object_ratio": 0.9,
        "object_with_class_ratio": 0.95,
        "object_with_relation_ratio": 0.8,
        "object_with_property_ratio": 0.8,
        "isolated_object_ratio_max": 0.2,
        "unsupported_class_count_max": 0,
        "unclassified_object_ratio_max": 0.1,
    },
    "latency_targets": {
        "identity_index_sec_max": 3,
        "semantic_extraction_sec_max": 60,
        "dedup_sec_max": 10,
        "cycle_sec_max": 900,
    },
    "next_action_policy": {
        "if_no_frontier": "rebuild_frontier_from_uncovered_approved_objects",
        "if_many_isolated_objects": "relation_completion",
        "if_many_unclassified_objects": "class_assignment",
        "if_many_unsupported_classes": "ontology_shape_repair",
        "if_many_objects_without_properties": "property_completion",
        "if_dedup_conflicts_high": "dedup_verifier_review",
        "if_latency_high": "stage_bottleneck_diagnosis",
        "if_run_failed": "run_health_diagnosis",
        "if_targets_met": "continue_frontier_enrichment",
    },
}


ONTOLOGY_PARTS = {
    "abstract_class",
    "class",
    "concrete_object",
    "object_instance",
    "instance",
    "relation",
    "property",
    "event",
    "action",
    "function",
    "policy",
}


SEMANTIC_ELEMENT_TYPES = {
    "situation",
    "metric_observation",
    "metric_change_observation",
    "impact_claim",
    "indicator_claim",
    "recommendation",
}


def load_loop_config(path: str | Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_LOOP_CONFIG))
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


def evaluate_enrichment_loop(
    metadata_db_url: str,
    tenant_id: str,
    *,
    run_key: str | None = None,
    config: dict[str, Any] | None = None,
    session_key: str | None = None,
) -> dict[str, Any]:
    config = _deep_merge(json.loads(json.dumps(DEFAULT_LOOP_CONFIG)), config or {})
    engine = create_engine(metadata_db_url)
    with engine.connect() as conn:
        run = _load_run(conn, tenant_id, run_key)
        run_elements = _load_elements(conn, tenant_id, run["id"]) if run else []
        tenant_elements = _load_elements(conn, tenant_id, None)
        session = _load_session(conn, tenant_id, session_key) if session_key else None

    run_metrics = _run_metrics(run, run_elements)
    tenant_metrics = _tenant_metrics(tenant_elements)
    latency = _latency_metrics(run)
    verdict = _decide_next_focus(
        run=run,
        run_metrics=run_metrics,
        tenant_metrics=tenant_metrics,
        latency=latency,
        config=config,
        session=session,
    )
    return {
        "loop_id": config.get("loop_id"),
        "tenant": tenant_id,
        "session_key": session_key,
        "run_key": run.get("run_key") if run else run_key,
        "status": run.get("status") if run else "missing_run",
        "run_metrics": run_metrics,
        "tenant_metrics": tenant_metrics,
        "stage_latency_sec": latency,
        "verdict": verdict,
    }


def _load_run(conn, tenant_id: str, run_key: str | None) -> dict[str, Any] | None:
    if run_key:
        row = conn.execute(
            text(
                """
                SELECT id, run_key, status, objective, proposed_count, pruned_count,
                       finding_count, expansion_trace_json, safety_profile_json,
                       skipped_sources_json, started_at, finished_at, error
                FROM aletheia_iterative_graph_enrichment_runs
                WHERE project_id = :tenant_id AND run_key = :run_key
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "run_key": run_key},
        ).mappings().first()
    else:
        row = conn.execute(
            text(
                """
                SELECT id, run_key, status, objective, proposed_count, pruned_count,
                       finding_count, expansion_trace_json, safety_profile_json,
                       skipped_sources_json, started_at, finished_at, error
                FROM aletheia_iterative_graph_enrichment_runs
                WHERE project_id = :tenant_id
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id},
        ).mappings().first()
    return dict(row) if row else None


def _load_session(conn, tenant_id: str, session_key: str | None) -> dict[str, Any] | None:
    if not session_key:
        return None
    try:
        row = conn.execute(
            text(
                """
                SELECT session_key, status, config_json, frontier_json, last_run_key,
                       cycle_count, updated_at
                FROM aletheia_continuous_enrichment_sessions
                WHERE project_id = :tenant_id AND session_key = :session_key
                LIMIT 1
                """
            ),
            {"tenant_id": tenant_id, "session_key": session_key},
        ).mappings().first()
    except Exception:
        return None
    if not row:
        return None
    return {
        **dict(row),
        "config": _json_load(row["config_json"], {}),
        "frontier": _json_load(row["frontier_json"], []),
    }


def _load_elements(conn, tenant_id: str, run_id: int | None) -> list[dict[str, Any]]:
    params = {"tenant_id": tenant_id}
    run_filter = ""
    if run_id is not None:
        run_filter = "AND run_id = :run_id"
        params["run_id"] = run_id
    rows = conn.execute(
        text(
            f"""
            SELECT element_key, element_type, name, payload_json, evidence_refs_json,
                   source_url, confidence, status, iteration, created_at
            FROM aletheia_proposed_graph_elements
            WHERE project_id = :tenant_id
              {run_filter}
            """
        ),
        params,
    ).mappings().all()
    result = []
    for row in rows:
        payload = _json_load(row["payload_json"], {})
        result.append({**dict(row), "payload": payload, "evidence_refs": _json_load(row["evidence_refs_json"], [])})
    return result


def _run_metrics(run: dict[str, Any] | None, elements: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "") for item in elements)
    type_counts = Counter(str(item.get("element_type") or "") for item in elements)
    ontology_part_counts = Counter(_ontology_part(item) for item in elements if _ontology_part(item))
    duplicate_counts = Counter(_dedup_decision(item) for item in elements if _dedup_decision(item))
    semantic_count = sum(type_counts.get(item, 0) for item in SEMANTIC_ELEMENT_TYPES)
    return {
        "element_count": len(elements),
        "status_counts": dict(sorted(status_counts.items())),
        "element_type_counts": dict(sorted(type_counts.items())),
        "ontology_part_counts": dict(sorted(ontology_part_counts.items())),
        "semantic_item_count": semantic_count,
        "ontology_candidate_count": type_counts.get("ontology_concept", 0),
        "dedup_decision_counts": dict(sorted(duplicate_counts.items())),
        "proposed_count": int((run or {}).get("proposed_count") or 0),
        "pruned_count": int((run or {}).get("pruned_count") or 0),
        "finding_count": int((run or {}).get("finding_count") or 0),
        "skipped_source_count": len(_json_load((run or {}).get("skipped_sources_json"), [])),
    }


def _tenant_metrics(elements: list[dict[str, Any]]) -> dict[str, Any]:
    approved = [item for item in elements if str(item.get("status") or "").lower() == "approved"]
    ontology = [item for item in approved if item.get("element_type") == "ontology_concept"]
    objects = [item for item in ontology if _is_concrete_object(item)]
    classes = [item for item in ontology if _is_class(item)]
    relations = [item for item in ontology if _is_relation(item)]
    properties = [item for item in ontology if _ontology_part(item) == "property"]

    object_records = [_object_record(item) for item in objects]
    object_records = [item for item in object_records if item["label"]]
    class_labels = {_normalize_label(_class_label(item)) for item in classes if _class_label(item)}
    object_class_labels = {_normalize_label(item["class_label"]) for item in object_records if item["class_label"]}
    class_labels.update(label for label in object_class_labels if label)

    relation_pairs = [_relation_record(item) for item in relations]
    property_targets = {_normalize_label(value) for item in properties for value in _property_target_labels(item)}
    relation_endpoint_labels = {
        _normalize_label(value)
        for rel in relation_pairs
        for value in [rel.get("source_label"), rel.get("target_label")]
        if value
    }

    objects_with_class = [item for item in object_records if _normalize_label(item["class_label"]) in class_labels]
    objects_with_relation = [item for item in object_records if _object_matches(item, relation_endpoint_labels)]
    objects_with_property = [item for item in object_records if _object_matches(item, property_targets)]
    classes_with_object = [label for label in class_labels if label in object_class_labels]
    unsupported_classes = sorted(label for label in class_labels if label not in object_class_labels)
    unclassified_objects = [
        item for item in object_records
        if not item["class_label"] or _normalize_label(item["class_label"]) == "unclassifiedontologyobject"
    ]

    object_count = len(object_records)
    class_count = len(class_labels)
    return {
        "approved_ontology_concepts": len(ontology),
        "approved_classes": class_count,
        "approved_concrete_objects": object_count,
        "approved_relations": len(relation_pairs),
        "approved_properties": len(properties),
        "approved_events": sum(1 for item in ontology if _ontology_part(item) == "event"),
        "approved_actions": sum(1 for item in ontology if _ontology_part(item) == "action"),
        "approved_functions": sum(1 for item in ontology if _ontology_part(item) == "function"),
        "approved_policies": sum(1 for item in ontology if _ontology_part(item) == "policy"),
        "classes_with_object": len(classes_with_object),
        "unsupported_class_count": len(unsupported_classes),
        "unsupported_classes": unsupported_classes[:50],
        "objects_with_class": len(objects_with_class),
        "objects_with_relation": len(objects_with_relation),
        "objects_with_property": len(objects_with_property),
        "isolated_objects": max(0, object_count - len(objects_with_relation)),
        "unclassified_objects": len(unclassified_objects),
        "class_with_object_ratio": _ratio(len(classes_with_object), class_count),
        "object_with_class_ratio": _ratio(len(objects_with_class), object_count),
        "object_with_relation_ratio": _ratio(len(objects_with_relation), object_count),
        "object_with_property_ratio": _ratio(len(objects_with_property), object_count),
        "isolated_object_ratio": _ratio(max(0, object_count - len(objects_with_relation)), object_count),
        "unclassified_object_ratio": _ratio(len(unclassified_objects), object_count),
    }


def _latency_metrics(run: dict[str, Any] | None) -> dict[str, Any]:
    if not run:
        return {}
    safety = _json_load(run.get("safety_profile_json"), {})
    trace = safety.get("runtime_trace") if isinstance(safety, dict) else []
    if not isinstance(trace, list):
        trace = []
    stage_pairs = {
        "identity_index": ("identity_index_start", "identity_index_done"),
        "frontier_search": ("frontier_search_start", "frontier_search_done"),
        "extraction": ("extraction_start", "extraction_done"),
        "candidate_elements": ("candidate_elements_start", "candidate_elements_done"),
        "semantic_extraction": ("semantic_pass_start", "semantic_pass_done"),
        "dedup": ("candidate_dedup_start", "candidate_dedup_done"),
        "duplicate_cleanup": ("duplicate_cleanup_start", "duplicate_cleanup_done"),
    }
    totals = {name: 0.0 for name in stage_pairs}
    starts: dict[str, list[datetime]] = defaultdict(list)
    event_to_stage = {}
    for stage, (start, done) in stage_pairs.items():
        event_to_stage[start] = (stage, "start")
        event_to_stage[done] = (stage, "done")
    for event in trace:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        stage_info = event_to_stage.get(event_type)
        if not stage_info:
            continue
        ts = _parse_ts(event.get("ts"))
        if not ts:
            continue
        stage, marker = stage_info
        if marker == "start":
            starts[stage].append(ts)
        elif starts[stage]:
            start_ts = starts[stage].pop()
            totals[stage] += max(0.0, (ts - start_ts).total_seconds())
    cycle_sec = 0.0
    started_at = _parse_ts(run.get("started_at"))
    finished_at = _parse_ts(run.get("finished_at"))
    if started_at and finished_at:
        cycle_sec = max(0.0, (finished_at - started_at).total_seconds())
    return {**{key: round(value, 3) for key, value in totals.items() if value > 0}, "cycle": round(cycle_sec, 3)}


def _decide_next_focus(
    *,
    run: dict[str, Any] | None,
    run_metrics: dict[str, Any],
    tenant_metrics: dict[str, Any],
    latency: dict[str, Any],
    config: dict[str, Any],
    session: dict[str, Any] | None,
) -> dict[str, Any]:
    targets = config.get("coverage_targets") or {}
    latency_targets = config.get("latency_targets") or {}
    policy = config.get("next_action_policy") or {}
    reasons = []
    focus = None

    if not run or run.get("status") != "completed":
        focus = policy.get("if_run_failed", "run_health_diagnosis")
        reasons.append("latest run is missing or not completed")
    elif _exceeds_latency(latency, latency_targets):
        focus = policy.get("if_latency_high", "stage_bottleneck_diagnosis")
        reasons.extend(_latency_reasons(latency, latency_targets))

    frontier_count = len((session or {}).get("frontier") or [])
    if not focus and session is not None and frontier_count == 0:
        focus = policy.get("if_no_frontier", "rebuild_frontier_from_uncovered_approved_objects")
        reasons.append("continuous enrichment frontier is empty")

    if not focus and tenant_metrics.get("unsupported_class_count", 0) > int(targets.get("unsupported_class_count_max", 0)):
        focus = policy.get("if_many_unsupported_classes", "ontology_shape_repair")
        reasons.append("approved classes exist without approved concrete objects")

    if not focus and tenant_metrics.get("unclassified_object_ratio", 0.0) > float(targets.get("unclassified_object_ratio_max", 0.1)):
        focus = policy.get("if_many_unclassified_objects", "class_assignment")
        reasons.append("too many approved objects have no stable class assignment")

    if not focus and tenant_metrics.get("isolated_object_ratio", 0.0) > float(targets.get("isolated_object_ratio_max", 0.2)):
        focus = policy.get("if_many_isolated_objects", "relation_completion")
        reasons.append("too many approved ontology objects have no approved relation")

    if not focus and tenant_metrics.get("object_with_property_ratio", 1.0) < float(targets.get("object_with_property_ratio", 0.8)):
        focus = policy.get("if_many_objects_without_properties", "property_completion")
        reasons.append("approved ontology objects lack approved property coverage")

    dedup_needs_review = run_metrics.get("dedup_decision_counts", {}).get("needs_review", 0)
    if not focus and dedup_needs_review >= 5:
        focus = policy.get("if_dedup_conflicts_high", "dedup_verifier_review")
        reasons.append("dedup produced many needs_review decisions")

    if not focus:
        focus = policy.get("if_targets_met", "continue_frontier_enrichment")
        reasons.append("coverage targets are currently satisfied or no stronger failure signal was found")

    return {
        "decision": "continue",
        "next_focus": focus,
        "reasons": reasons,
        "frontier_count": frontier_count if session is not None else None,
    }


def _exceeds_latency(latency: dict[str, Any], targets: dict[str, Any]) -> bool:
    return bool(_latency_reasons(latency, targets))


def _latency_reasons(latency: dict[str, Any], targets: dict[str, Any]) -> list[str]:
    checks = [
        ("identity_index", "identity_index_sec_max"),
        ("semantic_extraction", "semantic_extraction_sec_max"),
        ("dedup", "dedup_sec_max"),
        ("cycle", "cycle_sec_max"),
    ]
    reasons = []
    for metric, target_key in checks:
        value = float(latency.get(metric) or 0.0)
        target = targets.get(target_key)
        if target is not None and value > float(target):
            reasons.append(f"{metric} latency {value:.3f}s exceeds target {float(target):.3f}s")
    return reasons


def _json_load(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text_value = str(value)
    if text_value.endswith("Z"):
        text_value = text_value[:-1]
    try:
        return datetime.fromisoformat(text_value)
    except Exception:
        return None


def _ontology_part(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
    return str(payload.get("ontology_part") or candidate.get("ontology_part") or "").strip().lower()


def _artifact_type(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
    return str(payload.get("artifact_type") or payload.get("source_artifact_type") or candidate.get("artifact_type") or "").strip().lower()


def _is_concrete_object(item: dict[str, Any]) -> bool:
    artifact_type = _artifact_type(item)
    part = _ontology_part(item)
    return artifact_type in {"object", "entity", "instance"} and part in {"concrete_object", "object_instance", "instance"}


def _is_class(item: dict[str, Any]) -> bool:
    artifact_type = _artifact_type(item)
    part = _ontology_part(item)
    return artifact_type == "class" or part in {"class", "abstract_class"}


def _is_relation(item: dict[str, Any]) -> bool:
    artifact_type = _artifact_type(item)
    part = _ontology_part(item)
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return (artifact_type in {"link", "relation"} or part == "relation") and bool(payload.get("source_label")) and bool(payload.get("target_label"))


def _class_label(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
    for value in [payload.get("label"), candidate.get("label"), item.get("name")]:
        value = str(value or "").strip()
        if value:
            return value
    return ""


def _object_record(item: dict[str, Any]) -> dict[str, str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
    label = _first_text(payload.get("label"), candidate.get("label"), identity.get("label"), item.get("name"))
    class_label = _first_text(
        payload.get("object_type"),
        payload.get("ontology_type"),
        payload.get("class_name"),
        payload.get("class"),
        payload.get("type"),
        identity.get("entity_type"),
        candidate.get("object_type"),
        candidate.get("ontology_type"),
        candidate.get("class_name"),
        candidate.get("class"),
        candidate.get("type"),
    )
    aliases = [str(value or "").strip() for value in (identity.get("aliases") or payload.get("aliases") or [])]
    return {"label": label, "class_label": class_label, "aliases": aliases, "element_key": item.get("element_key") or ""}


def _relation_record(item: dict[str, Any]) -> dict[str, str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "source_label": str(payload.get("source_label") or "").strip(),
        "target_label": str(payload.get("target_label") or "").strip(),
        "relation": str(payload.get("relation") or payload.get("label") or item.get("name") or "").strip(),
    }


def _property_target_labels(item: dict[str, Any]) -> list[str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
    labels = []
    for key in (
        "object_label",
        "target_label",
        "source_label",
        "subject",
        "domain_label",
        "applies_to",
        "target_object",
        "target_object_type",
        "object_type",
    ):
        value = payload.get(key, candidate.get(key))
        if isinstance(value, list):
            labels.extend(str(item or "").strip() for item in value)
        elif value:
            labels.append(str(value).strip())
    return [label for label in labels if label]


def _dedup_decision(item: dict[str, Any]) -> str:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return str(payload.get("dedup_decision") or payload.get("llm_dedup_decision_override") or "").strip().lower()


def _object_matches(record: dict[str, Any], normalized_labels: set[str]) -> bool:
    candidates = [record.get("label"), record.get("element_key"), *(record.get("aliases") or [])]
    return any(_normalize_label(value) in normalized_labels for value in candidates if value)


def _first_text(*values: Any) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return ""


def _normalize_label(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(float(numerator) / float(denominator), 4)
