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
from urllib.parse import unquote

from sqlalchemy import create_engine, text

try:
    from ontology_quality import concrete_object_quality as _concrete_object_quality
    from ontology_quality import normalize_label as _normalize_label
except ModuleNotFoundError:
    from agents.ontology_quality import concrete_object_quality as _concrete_object_quality
    from agents.ontology_quality import normalize_label as _normalize_label


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
        "if_low_quality_concrete_objects": "concrete_object_quality_repair",
        "if_many_objects_without_properties": "property_completion",
        "if_dedup_conflicts_high": "dedup_verifier_review",
        "if_latency_high": "stage_bottleneck_diagnosis",
        "if_run_failed": "run_health_diagnosis",
        "if_targets_met": "continue_frontier_enrichment",
    },
    "repair_policy": {
        "ontology_shape_repair": {
            "default_action": "needs_more_evidence",
            "max_items": 200,
            "reviewer": "Loop Harness",
            "reason": "Loop harness shape repair: approved ontology class has no approved concrete object instance.",
        },
        "class_assignment": {
            "max_items": 200,
            "min_score": 4.0,
            "auto_apply_min_score": 8.0,
            "reviewer": "Loop Harness",
            "reason": "Loop harness class assignment: approved concrete object had no stable class, and the assigned class matched object evidence text.",
        },
        "concrete_object_quality_repair": {
            "default_action": "needs_more_evidence",
            "max_items": 200,
            "reviewer": "Loop Harness",
            "reason": "Loop harness quality repair: approved concrete object looks like a semantic phrase, metric, claim, or class-shaped abstraction rather than a stable referential object.",
        },
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
    repair_plan = build_repair_plan(
        tenant_id,
        tenant_elements,
        next_focus=verdict.get("next_focus"),
        config=config,
    )
    verdict = _advance_unactionable_focus(verdict, repair_plan, tenant_metrics, config)
    if verdict.get("next_focus") != repair_plan.get("focus"):
        repair_plan = build_repair_plan(
            tenant_id,
            tenant_elements,
            next_focus=verdict.get("next_focus"),
            config=config,
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
        "repair_plan": repair_plan,
    }


def _advance_unactionable_focus(
    verdict: dict[str, Any],
    repair_plan: dict[str, Any],
    tenant_metrics: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    focus = verdict.get("next_focus")
    if repair_plan.get("actionable", False):
        return verdict
    if focus not in {"class_assignment", "ontology_shape_repair"}:
        return verdict
    targets = config.get("coverage_targets") or {}
    policy = config.get("next_action_policy") or {}
    reasons = list(verdict.get("reasons") or [])
    reasons.append(f"{focus} has no automatically actionable repair items; remaining candidates require review")
    if tenant_metrics.get("isolated_object_ratio", 0.0) > float(targets.get("isolated_object_ratio_max", 0.2)):
        return {**verdict, "next_focus": policy.get("if_many_isolated_objects", "relation_completion"), "reasons": reasons}
    if tenant_metrics.get("object_with_property_ratio", 1.0) < float(targets.get("object_with_property_ratio", 0.8)):
        return {**verdict, "next_focus": policy.get("if_many_objects_without_properties", "property_completion"), "reasons": reasons}
    return verdict


def build_repair_plan(
    tenant_id: str,
    elements: list[dict[str, Any]],
    *,
    next_focus: str | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _deep_merge(json.loads(json.dumps(DEFAULT_LOOP_CONFIG)), config or {})
    if next_focus != "ontology_shape_repair":
        if next_focus == "class_assignment":
            return _build_class_assignment_repair_plan(tenant_id, elements, config=config)
        if next_focus == "concrete_object_quality_repair":
            return _build_concrete_object_quality_repair_plan(tenant_id, elements, config=config)
        if next_focus == "relation_completion":
            return _build_relation_completion_plan(tenant_id, elements, config=config)
        if next_focus == "property_completion":
            return _build_property_completion_plan(tenant_id, elements, config=config)
        return {"focus": next_focus, "actionable": False, "items": [], "item_count": 0}
    policy = ((config.get("repair_policy") or {}).get("ontology_shape_repair") or {})
    max_items = max(1, int(policy.get("max_items") or 200))
    action = str(policy.get("default_action") or "needs_more_evidence").strip()
    object_class_labels = {
        _normalize_label(_object_record(item)["class_label"])
        for item in elements
        if str(item.get("status") or "").lower() == "approved" and _is_concrete_object(item)
    }
    items = []
    for item in elements:
        if str(item.get("status") or "").lower() != "approved" or not _is_class(item):
            continue
        class_label = _class_label(item)
        normalized_class = _normalize_label(class_label)
        if not normalized_class or normalized_class in object_class_labels:
            continue
        items.append(
            {
                "element_key": item.get("element_key"),
                "name": item.get("name"),
                "label": class_label,
                "normalized_label": normalized_class,
                "current_status": item.get("status"),
                "recommended_action": action,
                "reason": policy.get("reason") or "Approved ontology class has no approved concrete object instance.",
                "source_url": item.get("source_url"),
                "confidence": item.get("confidence"),
            }
        )
        if len(items) >= max_items:
            break
    return {
        "focus": "ontology_shape_repair",
        "actionable": bool(items),
        "recommended_action": action,
        "item_count": len(items),
        "items": items,
    }


def _build_class_assignment_repair_plan(
    tenant_id: str,
    elements: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = ((config.get("repair_policy") or {}).get("class_assignment") or {})
    max_items = max(1, int(policy.get("max_items") or 200))
    min_score = float(policy.get("min_score") or 4.0)
    auto_apply_min_score = float(policy.get("auto_apply_min_score") or 8.0)
    class_candidates = []
    seen_classes = set()
    for item in elements:
        if str(item.get("status") or "").lower() not in {"approved", "needs_more_evidence"} or not _is_class(item):
            continue
        label = _class_label(item)
        normalized = _normalize_label(label)
        if not normalized or normalized in seen_classes:
            continue
        seen_classes.add(normalized)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        class_candidates.append(
            {
                "label": label,
                "normalized_label": normalized,
                "element_key": item.get("element_key"),
                "status": item.get("status"),
                "tokens": _semantic_tokens(label, payload.get("description"), payload.get("evidence_quote")),
            }
        )
    items = []
    for item in elements:
        if str(item.get("status") or "").lower() != "approved" or not _is_concrete_object(item):
            continue
        record = _object_record(item)
        if record.get("class_label") and _normalize_label(record.get("class_label")) != "unclassifiedontologyobject":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        description = str(payload.get("description") or "")
        object_tokens = _semantic_tokens(
            record.get("label"),
            description,
            payload.get("evidence_quote"),
        )
        best = None
        for candidate in class_candidates:
            if not _object_text_supports_class_label(record.get("label"), description, candidate["label"]):
                continue
            score = _class_assignment_score(object_tokens, candidate["tokens"], candidate["label"], object_label=record.get("label"))
            if score < min_score:
                continue
            if not best or score > best["score"]:
                best = {**candidate, "score": round(score, 4)}
        if not best:
            continue
        verification = {
            "method": "generic_text_evidence_overlap",
            "class_status": best.get("status"),
            "score": best["score"],
            "min_score": min_score,
            "auto_apply_min_score": auto_apply_min_score,
            "text_supports_class_label": True,
            "auto_apply_eligible": best["score"] >= auto_apply_min_score,
        }
        items.append(
            {
                "element_key": item.get("element_key"),
                "name": item.get("name"),
                "label": record.get("label"),
                "current_status": item.get("status"),
                "recommended_action": "assign_class" if verification["auto_apply_eligible"] else "needs_review",
                "assigned_class": best["label"],
                "assigned_class_key": best.get("element_key"),
                "assigned_class_status": best.get("status"),
                "score": best["score"],
                "verification": verification,
                "reason": policy.get("reason") or "Approved concrete object had no stable class and matched class evidence text.",
                "source_url": item.get("source_url"),
                "confidence": item.get("confidence"),
            }
        )
        if len(items) >= max_items:
            break
    return {
        "focus": "class_assignment",
        "actionable": any(item.get("recommended_action") == "assign_class" for item in items),
        "recommended_action": "assign_class",
        "item_count": len(items),
        "auto_assign_count": len([item for item in items if item.get("recommended_action") == "assign_class"]),
        "needs_review_count": len([item for item in items if item.get("recommended_action") == "needs_review"]),
        "items": items,
    }


def _build_relation_completion_plan(
    tenant_id: str,
    elements: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = ((config.get("repair_policy") or {}).get("relation_completion") or {})
    max_items = max(1, int(policy.get("max_items") or 50))
    approved_objects = [
        item for item in elements
        if str(item.get("status") or "").lower() == "approved" and _is_concrete_object(item)
    ]
    approved_relations = [
        _relation_record(item)
        for item in elements
        if str(item.get("status") or "").lower() == "approved" and _is_relation(item)
    ]
    endpoint_labels = {
        _normalize_label(value)
        for relation in approved_relations
        for value in [relation.get("source_label"), relation.get("target_label")]
        if value
    }
    items = []
    neutral_fallback_items = []
    for item in approved_objects:
        record = _object_record(item)
        if _object_matches(record, endpoint_labels):
            continue
        label = record.get("label")
        if not label:
            continue
        quality = _concrete_object_quality(record, item)
        if quality["issues"]:
            continue
        object_type = record.get("class_label")
        if not object_type:
            continue
        relevance = _repair_domain_relevance(record, item, policy)
        priority = _relation_completion_priority(label, object_type, item)
        priority["score"] = round(priority["score"] + relevance["score"], 3)
        priority["reasons"].extend(relevance["reasons"])
        plan_item = _completion_frontier_plan_item(
            item,
            label=label,
            object_type=object_type,
            recommended_action="enrich_relations",
            source_kind="loop_harness_relation_completion",
            reason="Approved ontology object has no approved relation endpoint.",
            objective=(
                "Find evidence-backed relations and properties for this approved ontology object. "
                f"Anchor extraction on the object label {label!r}: every proposed relation must include this object "
                "as source_label or target_label, unless the sentence only supports a background finding."
            ),
            priority=priority,
            relevance=relevance,
        )
        if relevance["eligible"]:
            items.append(plan_item)
        elif _allow_neutral_relevance_fallback(relevance, policy):
            plan_item["domain_relevance"]["fallback_used"] = True
            plan_item["priority_reasons"].append({"feature": "neutral_relevance_fallback", "value": True})
            neutral_fallback_items.append(plan_item)
    if not items and neutral_fallback_items:
        items = neutral_fallback_items
    items.sort(key=lambda item: (-float(item.get("priority_score") or 0.0), str(item.get("label") or "")))
    items = items[:max_items]
    return {
        "focus": "relation_completion",
        "actionable": bool(items),
        "recommended_action": "enqueue_frontier",
        "item_count": len(items),
        "items": items,
    }


def _build_property_completion_plan(
    tenant_id: str,
    elements: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = ((config.get("repair_policy") or {}).get("property_completion") or {})
    max_items = max(1, int(policy.get("max_items") or 50))
    approved_objects = [
        item for item in elements
        if str(item.get("status") or "").lower() == "approved" and _is_concrete_object(item)
    ]
    approved_properties = [
        item for item in elements
        if str(item.get("status") or "").lower() == "approved" and _ontology_part(item) == "property"
    ]
    property_targets = {
        _normalize_label(value)
        for item in approved_properties
        for value in _property_target_labels(item)
        if value
    }
    items = []
    neutral_fallback_items = []
    for item in approved_objects:
        record = _object_record(item)
        if _object_matches(record, property_targets):
            continue
        label = record.get("label")
        if not label:
            continue
        quality = _concrete_object_quality(record, item)
        if quality["issues"]:
            continue
        object_type = record.get("class_label")
        if not object_type:
            continue
        relevance = _repair_domain_relevance(record, item, policy)
        priority = _relation_completion_priority(label, object_type, item)
        priority["score"] = round(priority["score"] + relevance["score"], 3)
        priority["reasons"].extend(relevance["reasons"])
        plan_item = _completion_frontier_plan_item(
            item,
            label=label,
            object_type=object_type,
            recommended_action="enrich_properties",
            source_kind="loop_harness_property_completion",
            reason="Approved ontology object has no approved property coverage.",
            objective=(
                "Find evidence-backed properties, measurements, identifiers, and state observations for this approved ontology object. "
                f"Anchor extraction on the object label {label!r}: every proposed property must describe this object "
                "or a directly evidenced state of this object."
            ),
            priority=priority,
            relevance=relevance,
        )
        if relevance["eligible"]:
            items.append(plan_item)
        elif _allow_neutral_relevance_fallback(relevance, policy):
            plan_item["domain_relevance"]["fallback_used"] = True
            plan_item["priority_reasons"].append({"feature": "neutral_relevance_fallback", "value": True})
            neutral_fallback_items.append(plan_item)
    if not items and neutral_fallback_items:
        items = neutral_fallback_items
    items.sort(key=lambda item: (-float(item.get("priority_score") or 0.0), str(item.get("label") or "")))
    items = items[:max_items]
    return {
        "focus": "property_completion",
        "actionable": bool(items),
        "recommended_action": "enqueue_frontier",
        "item_count": len(items),
        "items": items,
    }


def _completion_frontier_plan_item(
    item: dict[str, Any],
    *,
    label: Any,
    object_type: Any,
    recommended_action: str,
    source_kind: str,
    reason: str,
    objective: str,
    priority: dict[str, Any],
    relevance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "element_key": item.get("element_key"),
        "label": label,
        "object_type": object_type,
        "recommended_action": recommended_action,
        "frontier_item": {
            "key": item.get("element_key"),
            "name": label,
            "artifact_type": "ontology_concrete_object",
            "object_type": object_type,
            "source": source_kind,
            "source_kind": source_kind,
            "priority": priority["score"],
            "reason": reason,
            "objective": objective,
        },
        "reason": reason,
        "source_url": item.get("source_url"),
        "confidence": item.get("confidence"),
        "priority_score": priority["score"],
        "priority_reasons": priority["reasons"],
        "domain_relevance": relevance,
    }


def _allow_neutral_relevance_fallback(relevance: dict[str, Any], policy: dict[str, Any]) -> bool:
    relevance_policy = policy.get("domain_relevance") if isinstance(policy.get("domain_relevance"), dict) else {}
    if not relevance_policy.get("allow_neutral_fallback_when_starved"):
        return False
    if relevance.get("eligible"):
        return False
    if relevance.get("negative_hits"):
        return False
    try:
        return float(relevance.get("score") or 0.0) >= 0.0
    except (TypeError, ValueError):
        return False


def _build_concrete_object_quality_repair_plan(
    tenant_id: str,
    elements: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = ((config.get("repair_policy") or {}).get("concrete_object_quality_repair") or {})
    max_items = max(1, int(policy.get("max_items") or 200))
    action = str(policy.get("default_action") or "needs_more_evidence").strip()
    approved_relations = [
        _relation_record(item)
        for item in elements
        if str(item.get("status") or "").lower() == "approved" and _is_relation(item)
    ]
    endpoint_labels = {
        _normalize_label(value)
        for relation in approved_relations
        for value in [relation.get("source_label"), relation.get("target_label")]
        if value
    }
    items = []
    for item in elements:
        if str(item.get("status") or "").lower() != "approved" or not _is_concrete_object(item):
            continue
        record = _object_record(item)
        if _object_matches(record, endpoint_labels):
            continue
        quality = _concrete_object_quality(record, item)
        if not quality["issues"]:
            continue
        items.append(
            {
                "element_key": item.get("element_key"),
                "name": item.get("name"),
                "label": record.get("label"),
                "object_type": record.get("class_label"),
                "current_status": item.get("status"),
                "recommended_action": action,
                "reason": policy.get("reason") or "Approved concrete object failed generic object quality checks.",
                "quality_score": quality["score"],
                "quality_issues": quality["issues"],
                "source_url": item.get("source_url"),
                "confidence": item.get("confidence"),
            }
        )
    items.sort(key=lambda item: (float(item.get("quality_score") or 0.0), str(item.get("label") or "")))
    items = items[:max_items]
    return {
        "focus": "concrete_object_quality_repair",
        "actionable": bool(items),
        "recommended_action": action,
        "item_count": len(items),
        "items": items,
    }


def _relation_completion_priority(label: Any, object_type: Any, item: dict[str, Any]) -> dict[str, Any]:
    text = str(label or "").strip()
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", text)
    lower_tokens = [token.lower() for token in tokens]
    reasons = []
    score = 0.0
    try:
        confidence = float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    score += min(max(confidence, 0.0), 1.0) * 10
    reasons.append({"feature": "confidence", "value": round(min(max(confidence, 0.0), 1.0) * 10, 3)})
    if str(object_type or "").strip() and _normalize_label(object_type) != "unclassifiedontologyobject":
        score += 8
        reasons.append({"feature": "stable_class", "value": 8})
        normalized_label = _normalize_label(text)
        normalized_type = _normalize_label(object_type)
        type_token_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(object_type or ""))))
        if normalized_type and (
            normalized_type == normalized_label
            or (type_token_count > 1 and normalized_label.endswith(normalized_type))
        ):
            score -= 10
            reasons.append({"feature": "self_typed_label", "value": -10})
    unique_token_count = len(set(lower_tokens))
    token_score = min(unique_token_count, 4) * 1.5
    score += token_score
    reasons.append({"feature": "label_token_count", "value": round(token_score, 3)})
    proper_phrases = re.findall(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){1,4}\b", text)
    phrase_score = min(len(proper_phrases), 2) * 3
    score += phrase_score
    if phrase_score:
        reasons.append({"feature": "proper_phrase", "value": phrase_score})
    if item.get("source_url"):
        score += 2
        reasons.append({"feature": "source_available", "value": 2})
    if len(tokens) <= 2 and lower_tokens[:1] in (["the"], ["a"], ["an"]):
        score -= 8
        reasons.append({"feature": "short_determiner_phrase", "value": -8})
    if re.search(r"\b[A-Za-z0-9-]+'s\b", text):
        score -= 6
        reasons.append({"feature": "possessive_phrase", "value": -6})
    if len(tokens) <= 1 and not proper_phrases:
        score -= 2
        reasons.append({"feature": "single_token_label", "value": -2})
    if 2 <= unique_token_count <= 4:
        score += 5
        reasons.append({"feature": "concise_named_label", "value": 5})
    if unique_token_count > 4:
        penalty = min((unique_token_count - 4) * 2, 12)
        score -= penalty
        reasons.append({"feature": "long_label", "value": -penalty})
    if unique_token_count > 6:
        penalty = min((unique_token_count - 6) * 2, 10)
        score -= penalty
        reasons.append({"feature": "overlong_label", "value": -penalty})
    return {"score": round(score, 3), "reasons": reasons}


def _repair_domain_relevance(record: dict[str, Any], item: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    relevance_policy = policy.get("domain_relevance") if isinstance(policy.get("domain_relevance"), dict) else {}
    if not relevance_policy.get("enabled"):
        return {"eligible": True, "score": 0.0, "reasons": []}
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    text = " ".join(
        str(value or "")
        for value in (
            record.get("label"),
            record.get("class_label"),
            payload.get("description"),
            payload.get("evidence_quote"),
            item.get("source_url"),
        )
        if value
    )
    normalized_text = _normalize_relevance_text(text)
    positive_terms = [
        str(term or "").strip()
        for term in relevance_policy.get("positive_terms") or []
        if str(term or "").strip()
    ]
    negative_terms = [
        str(term or "").strip()
        for term in relevance_policy.get("negative_terms") or []
        if str(term or "").strip()
    ]
    negative_classes = {
        _normalize_label(term)
        for term in relevance_policy.get("negative_object_types") or []
        if str(term or "").strip()
    }
    positive_hits = [term for term in positive_terms if _relevance_phrase_present(normalized_text, term)]
    negative_hits = [term for term in negative_terms if _relevance_phrase_present(normalized_text, term)]
    object_type_norm = _normalize_label(record.get("class_label"))
    score = float(len(positive_hits) * float(relevance_policy.get("positive_weight") or 2.0))
    score -= float(len(negative_hits) * float(relevance_policy.get("negative_weight") or 3.0))
    if object_type_norm and object_type_norm in negative_classes:
        score -= float(relevance_policy.get("negative_object_type_weight") or 8.0)
        negative_hits.append(str(record.get("class_label") or "object_type"))
    min_score = float(relevance_policy.get("min_score") or 0.0)
    reasons = [
        {"feature": "domain_positive_hits", "value": positive_hits[:8]},
        {"feature": "domain_negative_hits", "value": negative_hits[:8]},
        {"feature": "domain_relevance_score", "value": round(score, 3)},
        {"feature": "domain_relevance_min_score", "value": min_score},
    ]
    return {
        "eligible": score >= min_score,
        "score": round(score, 3),
        "positive_hits": positive_hits,
        "negative_hits": negative_hits,
        "min_score": min_score,
        "reasons": reasons,
    }


def _normalize_relevance_text(value: Any) -> str:
    words = re.findall(r"[A-Za-z0-9]+", unquote(str(value or "")).lower())
    return " ".join(words)


def _relevance_phrase_present(normalized_text: str, term: Any) -> bool:
    normalized_term = _normalize_relevance_text(term)
    if not normalized_text or not normalized_term:
        return False
    return f" {normalized_term} " in f" {normalized_text} "


def apply_repair_plan(
    metadata_db_url: str,
    tenant_id: str,
    repair_plan: dict[str, Any],
    *,
    reviewer: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if not repair_plan:
        return {"applied": 0, "skipped": 0, "items": [], "reason": "missing repair plan"}
    if repair_plan.get("focus") == "class_assignment":
        return _apply_class_assignment_repair_plan(
            metadata_db_url,
            tenant_id,
            repair_plan,
            reviewer=reviewer,
            reason=reason,
        )
    if repair_plan.get("focus") == "concrete_object_quality_repair":
        return _apply_status_repair_plan(
            metadata_db_url,
            tenant_id,
            repair_plan,
            repair_focus="concrete_object_quality_repair",
            default_reason="Loop harness quality repair: approved concrete object looks like a semantic phrase, metric, claim, or class-shaped abstraction rather than a stable referential object.",
            reviewer=reviewer,
            reason=reason,
        )
    if repair_plan.get("focus") != "ontology_shape_repair":
        return {"applied": 0, "skipped": 0, "items": [], "reason": "repair plan is not actionable"}
    return _apply_status_repair_plan(
        metadata_db_url,
        tenant_id,
        repair_plan,
        repair_focus="ontology_shape_repair",
        default_reason="Loop harness shape repair: approved ontology class has no approved concrete object instance.",
        reviewer=reviewer,
        reason=reason,
    )


def _apply_status_repair_plan(
    metadata_db_url: str,
    tenant_id: str,
    repair_plan: dict[str, Any],
    *,
    repair_focus: str,
    default_reason: str,
    reviewer: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    action = str(repair_plan.get("recommended_action") or "needs_more_evidence").strip()
    if action not in {"needs_more_evidence", "rejected"}:
        raise ValueError(f"Unsupported repair action: {action}")
    after_status = "needs_more_evidence" if action == "needs_more_evidence" else "rejected"
    reviewer = reviewer or "Loop Harness"
    reason = reason or default_reason
    reviewed_at = datetime.utcnow().isoformat()
    engine = create_engine(metadata_db_url)
    applied = []
    skipped = []
    with engine.begin() as conn:
        for item in repair_plan.get("items") or []:
            element_key = str(item.get("element_key") or "").strip()
            if not element_key:
                skipped.append({"element_key": element_key, "reason": "missing element_key"})
                continue
            row = conn.execute(
                text(
                    """
                    SELECT element_key, status, payload_json
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "element_key": element_key},
            ).mappings().first()
            if not row:
                skipped.append({"element_key": element_key, "reason": "not_found"})
                continue
            if row["status"] != "approved":
                skipped.append({"element_key": element_key, "reason": f"status_is_{row['status']}"})
                continue
            payload = _json_load(row["payload_json"], {})
            payload.setdefault("review_events", []).append(
                {
                    "decision": action,
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": row["status"],
                    "after_status": after_status,
                    "created_at": reviewed_at,
                    "canonical_write": False,
                    "graph_space_write": False,
                    "formal_graph_write": False,
                    "source": "enrichment_loop_harness",
                }
            )
            payload["loop_repair"] = {
                "focus": repair_focus,
                "reason": reason,
                "reviewer": reviewer,
                "repaired_at": reviewed_at,
                "quality_issues": item.get("quality_issues"),
            }
            conn.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET status = :status, payload_json = :payload_json
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "element_key": element_key,
                    "status": after_status,
                    "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            )
            conn.execute(
                text(
                    """
                    DELETE FROM aletheia_graph_identity_index
                    WHERE project_id = :tenant_id
                      AND source_space = 'proposed_graph'
                      AND source_key = :element_key
                    """
                ),
                {"tenant_id": tenant_id, "element_key": element_key},
            )
            applied.append({"element_key": element_key, "before_status": row["status"], "after_status": after_status})
    return {"applied": len(applied), "skipped": len(skipped), "items": applied, "skipped_items": skipped}


def _apply_class_assignment_repair_plan(
    metadata_db_url: str,
    tenant_id: str,
    repair_plan: dict[str, Any],
    *,
    reviewer: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    reviewer = reviewer or "Loop Harness"
    reason = reason or "Loop harness class assignment: approved concrete object had no stable class, and the assigned class matched object evidence text."
    reviewed_at = datetime.utcnow().isoformat()
    engine = create_engine(metadata_db_url)
    applied = []
    skipped = []
    with engine.begin() as conn:
        for item in repair_plan.get("items") or []:
            element_key = str(item.get("element_key") or "").strip()
            assigned_class = str(item.get("assigned_class") or "").strip()
            if item.get("recommended_action") != "assign_class":
                skipped.append({"element_key": element_key, "reason": "needs_review"})
                continue
            if not element_key or not assigned_class:
                skipped.append({"element_key": element_key, "reason": "missing element_key_or_assigned_class"})
                continue
            row = conn.execute(
                text(
                    """
                    SELECT element_key, status, payload_json
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant_id, "element_key": element_key},
            ).mappings().first()
            if not row:
                skipped.append({"element_key": element_key, "reason": "not_found"})
                continue
            if row["status"] != "approved":
                skipped.append({"element_key": element_key, "reason": f"status_is_{row['status']}"})
                continue
            payload = _json_load(row["payload_json"], {})
            current_class = _first_text(
                payload.get("object_type"),
                payload.get("ontology_type"),
                payload.get("class_name"),
                payload.get("class"),
                payload.get("type"),
            )
            if current_class and _normalize_label(current_class) != "unclassifiedontologyobject":
                skipped.append({"element_key": element_key, "reason": "already_has_class"})
                continue
            payload["object_type"] = assigned_class
            payload["class_name"] = assigned_class
            identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
            identity["entity_type"] = assigned_class
            payload["identity"] = identity
            payload.setdefault("review_events", []).append(
                {
                    "decision": "assign_class",
                    "reviewer": reviewer,
                    "reason": reason,
                    "before_status": row["status"],
                    "after_status": row["status"],
                    "assigned_class": assigned_class,
                    "assigned_class_key": item.get("assigned_class_key"),
                    "score": item.get("score"),
                    "verification": item.get("verification"),
                    "created_at": reviewed_at,
                    "canonical_write": False,
                    "graph_space_write": True,
                    "formal_graph_write": False,
                    "source": "enrichment_loop_harness",
                }
            )
            payload["loop_repair"] = {
                "focus": "class_assignment",
                "assigned_class": assigned_class,
                "assigned_class_key": item.get("assigned_class_key"),
                "score": item.get("score"),
                "verification": item.get("verification"),
                "reason": reason,
                "reviewer": reviewer,
                "repaired_at": reviewed_at,
            }
            conn.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET payload_json = :payload_json
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "element_key": element_key,
                    "payload_json": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            )
            applied.append({"element_key": element_key, "assigned_class": assigned_class, "score": item.get("score")})
    return {"applied": len(applied), "skipped": len(skipped), "items": applied, "skipped_items": skipped}


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
    low_quality_objects = []
    for item in objects:
        record = _object_record(item)
        if _object_matches(record, relation_endpoint_labels):
            continue
        quality = _concrete_object_quality(record, item)
        if quality["issues"]:
            low_quality_objects.append(
                {
                    "element_key": item.get("element_key"),
                    "label": record.get("label"),
                    "object_type": record.get("class_label"),
                    "quality_score": quality["score"],
                    "quality_issues": quality["issues"],
                }
            )

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
        "low_quality_concrete_object_count": len(low_quality_objects),
        "low_quality_concrete_objects": low_quality_objects[:50],
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
        "identity_index_read": ("identity_index_read_start", "identity_index_read_done"),
        "identity_index_rebuild": ("identity_index_rebuild_start", "identity_index_rebuild_done"),
        "identity_index_stale_check": ("identity_index_stale_check_start", "identity_index_stale_check_done"),
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
    latency_reasons = _latency_reasons(latency, latency_targets)

    if not run or run.get("status") != "completed":
        focus = policy.get("if_run_failed", "run_health_diagnosis")
        reasons.append("latest run is missing or not completed")

    frontier_count = len((session or {}).get("frontier") or [])
    if not focus and session is not None and frontier_count == 0:
        focus = policy.get("if_no_frontier", "rebuild_frontier_from_uncovered_approved_objects")
        reasons.append("continuous enrichment frontier is empty")

    if not focus and tenant_metrics.get("unsupported_class_count", 0) > int(targets.get("unsupported_class_count_max", 0)):
        focus = policy.get("if_many_unsupported_classes", "ontology_shape_repair")
        reasons.append("approved classes exist without approved concrete objects")

    if not focus and tenant_metrics.get("low_quality_concrete_object_count", 0) > 0:
        focus = policy.get("if_low_quality_concrete_objects", "concrete_object_quality_repair")
        reasons.append("approved concrete objects include low-quality semantic phrases that should not drive relation enrichment")

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

    if not focus and latency_reasons:
        focus = policy.get("if_latency_high", "stage_bottleneck_diagnosis")
        reasons.extend(latency_reasons)

    if not focus:
        focus = policy.get("if_targets_met", "continue_frontier_enrichment")
        reasons.append("coverage targets are currently satisfied or no stronger failure signal was found")

    if focus != policy.get("if_latency_high", "stage_bottleneck_diagnosis") and latency_reasons:
        reasons.extend(f"latency warning: {reason}" for reason in latency_reasons)

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
        ("identity_index_read", "identity_index_sec_max"),
        ("semantic_extraction", "semantic_extraction_sec_max"),
        ("dedup", "dedup_sec_max"),
        ("cycle", "cycle_sec_max"),
    ]
    reasons = []
    for metric, target_key in checks:
        value = float(latency.get(metric) or (latency.get("identity_index") if metric == "identity_index_read" else 0.0) or 0.0)
        target = targets.get(target_key)
        if target is not None and value > float(target):
            label = "identity_index_read" if metric == "identity_index_read" else metric
            reasons.append(f"{label} latency {value:.3f}s exceeds target {float(target):.3f}s")
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


def _semantic_tokens(*values: Any) -> set[str]:
    stop_words = {
        "a", "an", "and", "are", "as", "by", "for", "from", "has", "in", "is", "it",
        "of", "on", "or", "that", "the", "their", "this", "to", "with", "specific",
        "named", "source", "text", "object", "entity", "class", "type",
    }
    tokens = set()
    for value in values:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", str(value or "").lower()):
            if token in stop_words or len(token) < 3:
                continue
            if len(token) > 4 and token.endswith("ies"):
                token = token[:-3] + "y"
            elif len(token) > 4 and token.endswith("s"):
                token = token[:-1]
            tokens.add(token)
    return tokens


def _class_assignment_score(object_tokens: set[str], class_tokens: set[str], class_label: str, *, object_label: Any = None) -> float:
    if not object_tokens or not class_tokens:
        return 0.0
    label_tokens = _semantic_tokens(class_label)
    label_overlap = len(object_tokens & label_tokens)
    class_overlap = len(object_tokens & class_tokens)
    coverage = class_overlap / max(len(class_tokens), 1)
    direct_label_overlap = len(_semantic_tokens(object_label) & label_tokens)
    return float(direct_label_overlap * 20 + label_overlap * 4 + class_overlap + coverage)


def _object_text_supports_class_label(object_label: Any, object_description: Any, class_label: Any) -> bool:
    class_tokens = _semantic_tokens(class_label)
    if not class_tokens:
        return False
    label_tokens = _semantic_tokens(object_label)
    if label_tokens & class_tokens:
        return True
    description = str(object_description or "").lower()
    description_tokens = _semantic_tokens(description)
    if not class_tokens.issubset(description_tokens):
        return False
    ordered_class_tokens = [token for token in _ordered_semantic_tokens(class_label) if token in class_tokens]
    if not ordered_class_tokens:
        return False
    class_pattern = r"[\w\s,-]{0,16}".join(re.escape(token) for token in ordered_class_tokens)
    patterns = [
        rf"\b(?:a|an|the)\b[\w\s,-]{{0,48}}\b{class_pattern}\b",
        rf"\b(?:specific|named|explicitly named)\b[\w\s,-]{{0,48}}\b{class_pattern}\b",
    ]
    return any(re.search(pattern, description) for pattern in patterns)


def _ordered_semantic_tokens(value: Any) -> list[str]:
    tokens = []
    seen = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", str(value or "").lower()):
        normalized = next(iter(_semantic_tokens(token)), "")
        if normalized and normalized not in seen:
            seen.add(normalized)
            tokens.append(normalized)
    return tokens


def _first_text(*values: Any) -> str:
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return ""


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(float(numerator) / float(denominator), 4)
