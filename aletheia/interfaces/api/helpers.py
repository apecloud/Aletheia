"""Module-level pure helpers and shared constants extracted from server.py
(dedup/identity/presentation-guard logic, ontology concept property specs,
DB/tenant config defaults). No behavior change from the original file."""

import hashlib
import json
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from sqlalchemy import bindparam, create_engine, text
from aletheia.enrichment.iterative_enrichment import (
    IterativeGraphEnrichmentAgent,
    _configured_api_key,
    _edge_fact_identity_compatible,
    _edge_fact_identity_parts,
)


ROOT = Path(__file__).resolve().parents[3]

# Property names shared between _materialize_ontology_concept_type's TAG/EDGE
# TYPE registration and _materialize_ontology_concept_vertex/_edge's actual
# INSERT VERTEX/EDGE -- the TAG/EDGE schema must declare exactly the
# properties the insert writes, or Nebula rejects the insert with "prop not
# found". Single source of truth so the two can't drift apart.
_ONTOLOGY_CONCEPT_VERTEX_PROPERTIES = [
    {"name": "label", "data_type": "string"},
    {"name": "description", "data_type": "string"},
    {"name": "evidence_quote", "data_type": "string"},
    {"name": "source_url", "data_type": "string"},
]
_ONTOLOGY_CONCEPT_EDGE_PROPERTIES = [
    {"name": "evidence_quote", "data_type": "string"},
    {"name": "source_url", "data_type": "string"},
]

DB_URL = os.environ.get(
    "ALETHEIA_PG_URL",
    f"postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/{os.environ.get('ALETHEIA_PG_DB', 'aletheia_ontology')}",
)
SOURCE_DB_URL = os.environ.get(
    "ALETHEIA_MYSQL_URL",
    f"mysql+pymysql://aletheia_user:aletheia_password@127.0.0.1:3306/{os.environ.get('ALETHEIA_MYSQL_DB', 'aletheia_test_data')}",
)
STATIC_ROOT = ROOT / "web" / "app"
CONTINUOUS_RUNNING_STALE_SECONDS = int(os.environ.get("ALETHEIA_CONTINUOUS_RUNNING_STALE_SECONDS", "900"))
AGENT_GATEWAY_TMP_DIR = Path(os.environ.get("ALETHEIA_AGENT_GATEWAY_TMPDIR", tempfile.gettempdir()))
AGENT_GATEWAY_TMP_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_LLM_MODEL = os.environ.get("ALETHEIA_DEFAULT_LLM_MODEL", "gemini-3.5-flash")


def _load_json(value, default):
    if not value:
        return default
    return json.loads(value)


def _first_nonempty(*values):
    for value in values:
        text_value = str(value or "").strip()
        if text_value:
            return text_value
    return ""


def _web_enrichment_query(raw_payload, target_artifact_key=None):
    raw_payload = raw_payload or {}
    candidates = []

    top_level_search = raw_payload.get("search_query")
    if isinstance(top_level_search, dict):
        candidates.extend(
            [
                top_level_search.get("query"),
                top_level_search.get("q"),
                top_level_search.get("text"),
            ]
        )
    elif isinstance(top_level_search, str):
        candidates.append(top_level_search)

    source = raw_payload.get("source") or {}
    if isinstance(source, dict):
        nested_search = source.get("search_query")
        if isinstance(nested_search, dict):
            candidates.extend(
                [
                    nested_search.get("query"),
                    nested_search.get("q"),
                    nested_search.get("text"),
                ]
            )
        elif isinstance(nested_search, str):
            candidates.append(nested_search)
        candidates.extend([source.get("query"), source.get("q"), source.get("search_text")])

    candidates.extend([raw_payload.get("query"), raw_payload.get("q"), raw_payload.get("search_text")])
    for candidate in candidates:
        if candidate:
            return str(candidate)
    if target_artifact_key:
        return f"{target_artifact_key} web enrichment evidence"
    return None


def _json_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


DEDUP_AUDIT_FIELDS = (
    "candidate_id",
    "task_id",
    "run_id",
    "frontier_id",
    "dedup_decision",
    "matched_node_key",
    "matched_edge_key",
    "matched_element_key",
    "matched_status",
    "matched_source",
    "matched_collection",
    "match_score",
    "match_evidence",
    "match_method",
    "conflict_fields",
    "decision_reason",
    "possible_duplicate",
    "possible_duplicate_candidates",
    "source_fingerprint",
    "evidence_fingerprint",
    "llm_merge_decision_allowed",
    "llm_duplicate_verdict",
    "llm_dedup_decision_override",
)


GRAPH_DUPLICATE_DEDUP_DECISIONS = {
    "duplicate_existing_proposal",
    "duplicate_current_run",
    "merge_existing",
}


def _graph_identity_text(value):
    text_value = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text_value)).strip()


def _graph_identity_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _graph_edge_metric_identity(payload, properties):
    metrics = payload.get("metrics") or properties.get("metrics") or []
    if not isinstance(metrics, list):
        metrics = [metrics] if metrics else []
    normalized = []
    for metric in metrics:
        if isinstance(metric, dict):
            metric_key = (
                metric.get("canonical_key")
                or metric.get("key")
                or metric.get("metric_key")
                or metric.get("name")
                or metric.get("label")
            )
            metric_value = metric.get("value") or metric.get("amount") or metric.get("score")
            if metric_key:
                normalized.append(
                    json.dumps(
                        {"key": str(metric_key), "value": metric_value},
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        elif metric not in (None, ""):
            normalized.append(str(metric))
    return "|".join(sorted(set(normalized)))


def _graph_edge_fact_key(payload):
    payload = payload or {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    endpoint_evidence = payload.get("endpoint_dedup_evidence") if isinstance(payload.get("endpoint_dedup_evidence"), dict) else {}

    def endpoint_key(role, *fallbacks):
        evidence = endpoint_evidence.get(role) if isinstance(endpoint_evidence.get(role), dict) else {}
        matched_key = evidence.get("matched_node_key") or evidence.get("candidate_key")
        matched_space = evidence.get("matched_space") or evidence.get("matched_source")
        if matched_key and matched_space in {"approved_graph", "approved_graph_instance", "approved_graph_projection", "proposed_graph"}:
            return _graph_identity_text(matched_key)
        for fallback in fallbacks:
            value = _graph_identity_text(fallback)
            if value:
                return value
        return ""

    relation = _graph_identity_text(payload.get("relation") or payload.get("link_type") or payload.get("graph_edge_name"))
    source_label = endpoint_key(
        "source",
        payload.get("source_label"),
        payload.get("source_node_key"),
        payload.get("source_object_key"),
    )
    target_label = endpoint_key(
        "target",
        payload.get("target_label"),
        payload.get("target_node_key"),
        payload.get("target_object_key"),
    )
    if not relation or not source_label or not target_label:
        return None
    source_type = _graph_identity_text(payload.get("source_type"))
    target_type = _graph_identity_text(payload.get("target_type"))
    stable_source = (
        properties.get("canonical_id_hint")
        or properties.get("fact_node_hint")
        or payload.get("canonical_id_hint")
        or payload.get("fact_node_hint")
        or payload.get("schema_edge_key")
        or properties.get("schema_edge_key")
    )
    metric_identity = _graph_edge_metric_identity(payload, properties)
    return _graph_identity_digest(
        {
            "source_type": source_type,
            "source_label": source_label,
            "relation": relation,
            "target_type": target_type,
            "target_label": target_label,
            "stable_source": str(stable_source or ""),
            "metric_identity": metric_identity,
        }
    )


def _dedup_audit_from_payload(payload):
    payload = payload or {}
    audit = {}
    for field in DEDUP_AUDIT_FIELDS:
        if field not in payload:
            continue
        value = payload.get(field)
        if value in (None, "", [], {}):
            continue
        audit[field] = value
    if "llm_merge_decision_allowed" in payload:
        audit["llm_merge_decision_allowed"] = bool(payload.get("llm_merge_decision_allowed"))
    elif audit:
        audit["llm_merge_decision_allowed"] = False
    return audit


def _matched_proposed_graph_key(value):
    value = str(value or "").strip()
    return value if value.startswith("proposed-graph:") else ""


def _proposal_match_keys_from_payload(payload):
    payload = payload or {}
    keys = set()
    for field in ("matched_node_key", "matched_edge_key", "matched_element_key"):
        key = _matched_proposed_graph_key(payload.get(field))
        if key:
            keys.add(key)
    endpoint_evidence = payload.get("endpoint_dedup_evidence") if isinstance(payload.get("endpoint_dedup_evidence"), dict) else {}
    for evidence in endpoint_evidence.values():
        if not isinstance(evidence, dict):
            continue
        key = _matched_proposed_graph_key(evidence.get("matched_node_key") or evidence.get("candidate_key"))
        if key:
            keys.add(key)
    for item in payload.get("vector_top_k") if isinstance(payload.get("vector_top_k"), list) else []:
        if not isinstance(item, dict):
            continue
        key = _matched_proposed_graph_key(item.get("node_key"))
        if key:
            keys.add(key)
    return keys


def _proposal_match_summary(row):
    if not row:
        return None
    payload = _load_json(row["payload_json"], {})
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    summary = {
        "element_key": row["element_key"],
        "element_type": row["element_type"],
        "name": row["name"],
        "status": row["status"],
        "confidence": row["confidence"],
        "source_url": row["source_url"],
        "evidence_refs": _load_json(row["evidence_refs_json"], []),
        "run_key": row.get("run_key"),
        "created_at": _jsonable(row["created_at"]),
    }
    for field in (
        "source_type",
        "source_label",
        "relation",
        "relation_label",
        "target_type",
        "target_label",
        "ontology_type",
        "label",
    ):
        value = payload.get(field) or properties.get(field)
        if value not in (None, "", [], {}):
            summary[field] = value
    return summary


def _knowledge_candidate_profile(element_type, payload):
    payload = payload or {}
    raw_type = str(element_type or "").strip().lower()
    graph_space = payload.get("graph_space") if isinstance(payload.get("graph_space"), dict) else {}
    artifact_type = str(payload.get("artifact_type") or payload.get("ontology_artifact_type") or "").strip().lower()
    ontology_part = str(payload.get("ontology_part") or "").strip().lower()
    has_relation_shape = any(payload.get(field) for field in ("source_label", "target_label", "source_key", "target_key", "relation", "relation_label"))
    has_observation_shape = any(payload.get(field) for field in ("metric", "value", "unit", "change", "baseline", "time_window"))
    has_action_shape = any(payload.get(field) for field in ("action", "trigger", "preconditions", "effects", "target_object_type"))
    has_model_shape = bool(artifact_type or graph_space.get("space") == "ontology_model")
    graph_element_kind = raw_type if raw_type in {"node", "edge"} else None

    type_tokens = set(filter(None, raw_type.replace("-", "_").split("_")))
    if has_model_shape:
        knowledge_kind = "object" if artifact_type == "object" and ontology_part == "concrete_object" else "model_concept"
        product_surface = "ontology"
    elif "claim" in type_tokens:
        knowledge_kind = "claim"
        product_surface = "knowledge"
    elif "observation" in type_tokens:
        knowledge_kind = "observation"
        product_surface = "knowledge"
    elif type_tokens.intersection({"action", "recommendation"}) or has_action_shape:
        knowledge_kind = "actionable_object"
        product_surface = "ontology"
    elif raw_type == "edge" or has_relation_shape:
        knowledge_kind = "relation"
        product_surface = "knowledge"
    elif raw_type == "finding":
        knowledge_kind = "finding"
        product_surface = "reasoning"
    elif has_observation_shape:
        knowledge_kind = "observation"
        product_surface = "knowledge"
    else:
        knowledge_kind = "object"
        product_surface = "knowledge"

    if graph_space.get("space"):
        storage_projection = graph_space.get("space")
    elif raw_type == "edge" or has_relation_shape:
        storage_projection = "candidate_relation_projection"
    elif has_model_shape:
        storage_projection = "ontology_model_projection"
    else:
        storage_projection = "candidate_object_projection"

    return {
        "graph_element_kind": graph_element_kind,
        "knowledge_kind": knowledge_kind,
        "product_surface": product_surface,
        "review_domain": product_surface,
        "storage_projection": storage_projection,
        "review_surface": product_surface,
    }


def _compact_candidate_payload(payload):
    if not isinstance(payload, dict):
        return {}
    keep_keys = {
        "artifact_type",
        "label",
        "description",
        "domain",
        "range",
        "property_of",
        "source_artifact_type",
        "ontology_part",
        "trigger_event",
        "trigger_or_condition",
        "target_object_types",
        "affected_object_types",
        "expected_effects",
        "input_parameters",
        "inputs",
        "outputs",
        "guardrails",
        "applies_to",
        "evidence_quote",
        "source_url",
        "review_boundary",
        "governance",
        "instance_resolution",
        "promotion",
    }
    compact = {key: payload.get(key) for key in keep_keys if key in payload}
    for nested_key in ("ontology_candidate", "identity"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            compact[nested_key] = {
                key: nested.get(key)
                for key in ("artifact_type", "label", "normalized_label", "description", "domain", "range", "property_of")
                if key in nested
            }
    return compact


def _attach_proposal_match_summaries(payload, proposal_match_lookup):
    if not proposal_match_lookup:
        return payload
    enriched = dict(payload or {})
    for field in ("matched_node_key", "matched_edge_key", "matched_element_key"):
        match = proposal_match_lookup.get(_matched_proposed_graph_key(enriched.get(field)))
        if match:
            enriched["nearest_proposal_match"] = match
            break
    endpoint_evidence = enriched.get("endpoint_dedup_evidence")
    if isinstance(endpoint_evidence, dict):
        endpoint_copy = {}
        changed = False
        for role, evidence in endpoint_evidence.items():
            if not isinstance(evidence, dict):
                endpoint_copy[role] = evidence
                continue
            item = dict(evidence)
            match = proposal_match_lookup.get(_matched_proposed_graph_key(item.get("matched_node_key") or item.get("candidate_key")))
            if match:
                item["nearest_proposal_match"] = match
                changed = True
            endpoint_copy[role] = item
        if changed:
            enriched["endpoint_dedup_evidence"] = endpoint_copy
    return enriched


def _graph_identity_terms(value):
    return {token for token in _graph_identity_text(value).split() if token}


def _graph_node_identity_from_payload(name, payload):
    payload = payload or {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    identity = payload.get("identity")
    if isinstance(identity, dict) and (identity.get("kind") or "node") == "node":
        return {
            "kind": "node",
            "entity_type": str(identity.get("entity_type") or payload.get("ontology_type") or payload.get("type") or "").strip(),
            "label": str(identity.get("label") or payload.get("label") or name or "").strip(),
            "normalized_label": _graph_identity_text(identity.get("normalized_label") or identity.get("label") or payload.get("label") or name),
            "aliases": [
                _graph_identity_text(alias)
                for alias in (identity.get("aliases") or payload.get("aliases") or properties.get("aliases") or [])
                if _graph_identity_text(alias)
            ],
            "source_identity": identity.get("source_identity")
            or properties.get("canonical_id_hint")
            or properties.get("source_id")
            or payload.get("canonical_id_hint")
            or payload.get("source_id"),
        }
    return {
        "kind": "node",
        "entity_type": str(payload.get("ontology_type") or payload.get("type") or "").strip(),
        "label": str(payload.get("label") or name or "").strip(),
        "normalized_label": _graph_identity_text(payload.get("label") or name),
        "aliases": [
            _graph_identity_text(alias)
            for alias in (payload.get("aliases") or properties.get("aliases") or [])
            if _graph_identity_text(alias)
        ],
        "source_identity": properties.get("canonical_id_hint")
        or properties.get("source_id")
        or payload.get("canonical_id_hint")
        or payload.get("source_id"),
    }


def _graph_alias_surface_tokens(identity, dedup_text=None):
    surfaces = [
        identity.get("label"),
        identity.get("normalized_label"),
        identity.get("source_identity"),
        dedup_text,
        *(identity.get("aliases") or []),
    ]
    tokens = set()
    for surface in surfaces:
        normalized = _graph_identity_text(surface)
        if not normalized:
            continue
        tokens.add(normalized)
        parts = [part for part in normalized.split() if part]
        tokens.update(parts)
        long_parts = [part for part in parts if len(part) > 4]
        if len(long_parts) >= 2:
            tokens.add("".join(part[0] for part in long_parts))
    return tokens


def _graph_short_alias_possible_duplicates(candidate_identity, identity_rows, *, candidate_dedup_text, current_element_key=None, limit=5):
    candidate_type = _graph_identity_text(candidate_identity.get("entity_type"))
    candidate_terms = _graph_identity_terms(candidate_identity.get("normalized_label") or candidate_identity.get("label"))
    if not candidate_type or len(candidate_terms) != 1:
        return []
    candidate_token = next(iter(candidate_terms))
    if not (2 <= len(candidate_token) <= 4):
        return []
    candidates = []
    for row in identity_rows:
        if current_element_key and row.get("source_key") == current_element_key:
            continue
        identity = row.get("identity") or {}
        if identity.get("kind") != "node":
            continue
        existing_type = _graph_identity_text(identity.get("entity_type"))
        if existing_type and existing_type != candidate_type:
            continue
        tokens = _graph_alias_surface_tokens(identity, row.get("dedup_text"))
        evidence = []
        score = 0.0
        if candidate_token in tokens:
            evidence.append("same short label/alias token from identity index")
            score = 0.86
        else:
            prefixed = sorted(
                token
                for token in tokens
                if token.startswith(candidate_token)
                and len(token) > len(candidate_token)
                and len(token) <= len(candidate_token) + 2
            )
            if prefixed:
                evidence.append(f"short label prefixes existing alias token: {prefixed[0]}")
                score = 0.8
        if score <= 0:
            continue
        if existing_type:
            evidence.append("same entity type")
        candidates.append(
            {
                "node_key": row.get("source_key"),
                "status": "approved" if row.get("source_status") == "approved" else "proposed",
                "source": row.get("source_space"),
                "identity_key": row.get("identity_key"),
                "score": round(score, 4),
                "text_similarity": round(
                    SequenceMatcher(None, str(candidate_dedup_text or "").lower(), str(row.get("dedup_text") or "").lower()).ratio(),
                    4,
                )
                if row.get("dedup_text")
                else 0.0,
                "match_method": "embedding_degraded_alias_scan",
                "evidence": evidence,
            }
        )
    return sorted(candidates, key=lambda item: (-float(item.get("score") or 0.0), item.get("node_key") or ""))[:limit]


def _apply_possible_duplicate_presentation_guard(element, identity_rows):
    payload = {**(element.get("payload") or {})}
    if element.get("element_type") != "node":
        return element
    if str(element.get("status") or "").replace("-", "_").lower() not in {"draft", "needs_review", "needs_more_evidence"}:
        return element
    if str(payload.get("dedup_decision") or "").replace("-", "_").lower() != "new_proposal":
        return element
    if str(payload.get("match_method") or "").replace("-", "_").lower() != "embedding_degraded":
        return element
    candidate_identity = _graph_node_identity_from_payload(element.get("name"), payload)
    candidate_dedup_text = " | ".join(
        str(value)
        for value in [
            "node",
            candidate_identity.get("entity_type"),
            candidate_identity.get("label"),
            candidate_identity.get("normalized_label"),
            candidate_identity.get("source_identity"),
            payload.get("description"),
            payload.get("evidence_quote"),
        ]
        if value
    )
    possible = _graph_short_alias_possible_duplicates(
        candidate_identity,
        identity_rows,
        candidate_dedup_text=candidate_dedup_text,
        current_element_key=element.get("element_key"),
    )
    if not possible:
        return element
    best = possible[0]
    payload.update(
        {
            "dedup_decision": "needs_review",
            "review_required": True,
            "possible_duplicate": True,
            "possible_duplicate_candidates": possible,
            "matched_node_key": best.get("node_key"),
            "matched_status": best.get("status"),
            "matched_source": best.get("source"),
            "matched_element_key": best.get("node_key"),
            "match_score": best.get("score"),
            "match_method": "embedding_degraded_alias_scan",
            "match_evidence": [
                "presentation guard: embedding unavailable; exact identity missed",
                "short label/alias conflict found in existing identity index",
                *(best.get("evidence") or []),
            ],
            "decision_reason": "possible_duplicate_alias_conflict_embedding_degraded",
            "llm_merge_decision_allowed": False,
        }
    )
    guarded = {**element, "payload": payload, "status": "needs_more_evidence"}
    guarded["dedup_audit"] = _dedup_audit_from_payload(payload)
    guarded["presentation_guard"] = {
        "applied": True,
        "reason": "embedding_degraded_short_alias_possible_duplicate",
        "writes_persisted": False,
    }
    return guarded


def _graph_edge_source_identity_from_match(match):
    identity_key = str((match or {}).get("identity_key") or "")
    parts = identity_key.split(":")
    if len(parts) > 5 and parts[0] == "edge":
        return ":".join(parts[5:])
    return None


def _graph_edge_source_identity_conflict_is_provenance_only(candidate_source_identity, matched_source_identity):
    candidate_parts = _edge_fact_identity_parts(candidate_source_identity)
    matched_parts = _edge_fact_identity_parts(matched_source_identity)
    if candidate_parts and matched_parts:
        return _edge_fact_identity_compatible(candidate_source_identity, matched_source_identity)
    return not candidate_parts and not matched_parts


def _apply_edge_source_identity_presentation_guard(element):
    payload = {**(element.get("payload") or {})}
    if element.get("element_type") != "edge":
        return element
    if str(element.get("status") or "").replace("-", "_").lower() not in {"draft", "needs_review", "needs_more_evidence"}:
        return element
    if str(payload.get("dedup_decision") or "").replace("-", "_").lower() != "needs_review":
        return element
    if str(payload.get("decision_reason") or "").replace("-", "_").lower() != "structural_conflict":
        return element
    conflict_fields = [str(field) for field in (payload.get("conflict_fields") or [])]
    if conflict_fields != ["source_identity"]:
        return element
    if str(payload.get("match_method") or "").replace("-", "_").lower() != "vector_embedding":
        return element
    match_score = float(payload.get("match_score") or 0.0)
    vector_distance = payload.get("vector_distance")
    duplicate_distance = float(payload.get("vector_duplicate_distance_threshold") or 0.12)
    if vector_distance is not None:
        try:
            if float(vector_distance) > duplicate_distance:
                return element
        except (TypeError, ValueError):
            return element
    elif match_score < (1.0 - duplicate_distance):
        return element
    matched_key = payload.get("matched_edge_key") or payload.get("matched_node_key") or payload.get("matched_element_key")
    top_k = payload.get("vector_top_k") if isinstance(payload.get("vector_top_k"), list) else []
    matched = next((item for item in top_k if item.get("node_key") == matched_key), top_k[0] if top_k else {})
    matched_conflicts = [str(field) for field in (matched.get("conflict_fields") or [])]
    if matched_conflicts != ["source_identity"]:
        return element
    structure_evidence = {str(item) for item in (matched.get("structure_evidence") or payload.get("match_evidence") or [])}
    required_evidence = {"same source_type", "same target_type", "same source_node", "same target_node", "same relation"}
    if not required_evidence.issubset(structure_evidence):
        return element
    candidate_identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
    candidate_source_identity = candidate_identity.get("source_identity")
    matched_source_identity = _graph_edge_source_identity_from_match(matched)
    if not _graph_edge_source_identity_conflict_is_provenance_only(candidate_source_identity, matched_source_identity):
        return element

    matched_source = str(payload.get("matched_source") or matched.get("source") or "").replace("-", "_").lower()
    matched_status = str(payload.get("matched_status") or matched.get("status") or "").replace("-", "_").lower()
    if matched_source == "current_run_candidate":
        decision = "duplicate_current_run"
    elif matched_status == "proposed":
        decision = "duplicate_existing_proposal"
    else:
        decision = "merge_existing"
    match_evidence = [
        *(payload.get("match_evidence") or []),
        "presentation guard: source_identity conflict only contains provenance/occurrence tokens",
        "same stable edge fact after ignoring evidence source identity drift",
    ]
    payload.update(
        {
            "dedup_decision": decision,
            "review_required": False,
            "structure_compatible": True,
            "conflict_fields": [],
            "match_evidence": match_evidence,
            "decision_reason": "source_identity_provenance_drift_ignored",
            "llm_merge_decision_allowed": False,
        }
    )
    guarded = {**element, "payload": payload}
    guarded["dedup_audit"] = _dedup_audit_from_payload(payload)
    guarded["presentation_guard"] = {
        "applied": True,
        "reason": "edge_source_identity_provenance_drift",
        "writes_persisted": False,
    }
    return guarded


def _is_current_graph_proposal(row_status, payload):
    status = str(row_status or "").replace("-", "_").lower()
    if status not in {"draft", "needs_review", "needs_more_evidence"}:
        return False
    decision = str((payload or {}).get("dedup_decision") or "").replace("-", "_").lower()
    return decision not in GRAPH_DUPLICATE_DEDUP_DECISIONS


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "scope"


def _jsonable(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _require_reason(action, reason):
    if action in {
        "approve",
        "approved",
        "reject",
        "rejected",
        "needs_changes",
        "needs_more_evidence",
        "stale",
        "superseded",
        "reaffirmed",
        "comment",
    } and not reason.strip():
        raise ValueError(f"reason is required for {action}")


def _safe_error_message(exc):
    message = str(exc)
    for secret in ("aletheia_password", "aletheia_root"):
        message = message.replace(secret, "***")
    return message[:500]


def _artifact_to_dict(row):
    return {
        "id": row["id"],
        "tenant_id": row["project_id"],
        "project_id": row["project_id"],
        "canonical_key": row["canonical_key"],
        "artifact_type": row["artifact_type"],
        "name": row["name"],
        "description": row["description"],
        "payload": _load_json(row["payload_json"], {}),
        "confidence": row["confidence"],
        "source_refs": _load_json(row["source_refs_json"], []),
        "status": row["status"],
        "version": row["version"],
        "source_agent": row["source_agent"],
        "created_at": str(row["created_at"]) if row["created_at"] else None,
        "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
    }


def _field_by_qualified_name(fields):
    return {f"{field.get('source_table')}.{field.get('name')}": field for field in fields}


def _ontology_source_schema(artifact, table_fields=None):
    canonical_key = artifact.get("canonical_key") or ""
    payload = artifact.get("payload") or {}
    artifact_type = artifact.get("artifact_type")
    if artifact_type == "link" and payload.get("source_table") and payload.get("target_table"):
        table_fields = table_fields or {}
        source_table = payload.get("source_table")
        target_table = payload.get("target_table")
        field_map = _field_by_qualified_name(
            table_fields.get(source_table, {}).get("fields", [])
            + table_fields.get(target_table, {}).get("fields", [])
        )
        source_tables = [table_fields.get(t, {}) for t in (source_table, target_table)]
        schema = {
            "kind": "relationship_source_schema",
            "source_table": source_table,
            "target_table": target_table,
            "join_condition": payload.get("join_condition"),
            "cardinality": payload.get("cardinality"),
            "graph_edge": f"{payload.get('source_object_name') or payload.get('source_object_key')} -> {payload.get('target_object_name') or payload.get('target_object_key')}",
            "source_ref": payload.get("source_ref") or payload.get("join_condition"),
            "schema_source": (
                "live"
                if all(t.get("schema_source") == "live" for t in source_tables)
                else "degraded"
                if any(t.get("schema_source") == "degraded" for t in source_tables)
                else "artifact_payload"
            ),
            "source_object": payload.get("source_object_name") or payload.get("source_object_key"),
            "target_object": payload.get("target_object_name") or payload.get("target_object_key"),
            "link_type": payload.get("link_type") or payload.get("cardinality"),
            "modeling_source": payload.get("source_agent") or artifact.get("source_agent") or "SchemaGraphModelingAgent",
        }
        for field_name, role in (
            (payload.get("source_field"), "source_identity_field"),
            (payload.get("target_field"), "target_reference_field"),
        ):
            if not field_name:
                continue
            prop = dict(field_map.get(field_name, {}))
            if prop:
                prop["relationship_role"] = role
                schema[f"{role}_property"] = prop
        schema["field_properties"] = [
            value for key, value in schema.items() if key.endswith("_property") and isinstance(value, dict)
        ]
        return schema
    if artifact_type == "object":
        mapped_tables = payload.get("mapped_table_names") or payload.get("mapped_tables") or []
        primary_key = payload.get("primary_key")
        if mapped_tables:
            table = mapped_tables[0]
            live = (table_fields or {}).get(table)
            schema = {
                "table": table,
                "primary_key": primary_key,
                "columns": live.get("columns") if live else list(payload.get("properties") or []),
                "fields": live.get("fields", []) if live else [],
                "schema_source": live.get("schema_source") if live else "artifact_payload",
                "kind": "object_source_schema",
                "object_name": artifact.get("name"),
                "modeling_source": payload.get("source_agent") or artifact.get("source_agent") or "SchemaGraphModelingAgent",
            }
            if live and live.get("schema_source") != "live":
                schema["degraded"] = True
                schema["degraded_reason"] = live.get("degraded_reason")
                schema["connection_error"] = live.get("connection_error")
            return schema
    return {"kind": "unmapped", "source_refs": artifact.get("source_refs", [])}
