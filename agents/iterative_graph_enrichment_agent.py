"""Iterative proposed-graph enrichment.

This agent extracts proposed graph elements from approved ontology context and
allowed crawl/search evidence. Local term dictionaries in this file are
extraction hints for legacy/demo coverage only; they must not become canonical
ontology, formal graph writes, or schema-to-graph decisions. New node/edge type
semantics belong in SchemaGraphModelingAgent output plus the review gate.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, inspect, text
from sqlalchemy.orm import sessionmaker

try:
    import pycountry
except Exception:  # pragma: no cover - optional dependency fallback
    pycountry = None

from ontology_artifacts import (
    GraphIdentityIndex,
    GraphDeepResearchBenchmarkRun,
    IterativeGraphEnrichmentRun,
    OntologyArtifact,
    ProposedGraphElement,
    _json_dump,
    ensure_artifact_schema,
)
from tenant_registry import TenantRegistry, default_source_db_url
from web_enrichment_agent import StaticSearchProvider, _clean_text, _is_crawl_allowed, _is_public_web_url


DEFAULT_DEDUP_EMBEDDING_MODEL = os.environ.get(
    "ALETHEIA_DEDUP_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
VECTOR_DUPLICATE_DISTANCE = 0.12
VECTOR_REVIEW_DISTANCE = 0.24
VECTOR_TOP_K = 5
NODE_SIMILARITY_DIRECT_DEDUP_THRESHOLD = 0.6
NODE_DIRECT_DEDUP_MIN_CONFIDENCE = 0.6
_DOTENV_CACHE: dict[str, str] | None = None

DEEP_GRAPH_REQUIRED_STEPS = ("source_entity", "relation", "target_entity", "evidence", "action")

GRAPH_EXTRACTION_PROMPT_VERSION = "graph_entity_relation_v3"

GRAPH_EXTRACTION_PROMPT = """Extract graph-ready facts from crawled evidence.
Return strict JSON with:
1. ontology_candidates: object/relation/property proposals with label, description, domain, range, and review_required=true.
2. nodes: typed real-world entities only, with label, type, stable id hint, properties, description, confidence, and evidence_quote.
3. edges: typed binary relations only, with source_label, relation, target_label, properties, description, confidence, and evidence_quote.
4. findings: candidate analytical findings only when the evidence supports a complete path.
Rules:
- Use only facts explicitly supported by the source text.
- Resolve contextual references and generic noun phrases before emitting entities. Do not emit labels such as "second strait", "another waterway", "the country", "this port", or "key route" as graph nodes; emit the canonical named entity only when the source text states enough context to resolve it.
- If a phrase refers to an entity but the canonical name is not explicit or safely resolvable from the source text, put it in rejected_or_ambiguous_candidates with reason unresolved_entity_reference instead of creating a node or edge.
- Use relation types only from approved SchemaGraphModelingAgent edge metadata.
- If evidence suggests a relation that cannot be mapped to approved edge metadata, return it as ambiguous_relation for review.
- Keep metrics, confidence, source_url, and provenance as properties, not hidden text.
- Reify a fact node only when the relation needs its own identity for metrics/provenance; otherwise keep the main graph as source --relation--> target.
- Do not write canonical ontology or formal graph. Output remains draft/proposed until review."""


@dataclass
class GraphEvidenceNodeDraft:
    type: str
    label: str
    description: str
    properties: dict[str, Any]
    evidence_quote: str
    confidence: float
    source_ref: str
    review_required: bool = True
    source_grounding: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GraphEvidenceEdgeDraft:
    source_type: str
    source_label: str
    relation: str
    target_type: str
    target_label: str
    description: str
    properties: dict[str, Any]
    evidence_quote: str
    confidence: float
    source_ref: str
    review_required: bool = True
    source_grounding: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GraphEvidenceFindingDraft:
    title: str
    conclusion: str
    evidence_chain: list[dict[str, Any]]
    confidence: float
    source_ref: str
    review_required: bool = True


@dataclass
class GraphEvidenceExtractionDraft:
    prompt_version: str
    prompt_contract: str
    extraction_source: str
    extraction_engine: str
    extraction_engine_status: str
    source: dict[str, Any]
    schema_context: dict[str, Any]
    ontology_candidates: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[GraphEvidenceNodeDraft] = field(default_factory=list)
    edges: list[GraphEvidenceEdgeDraft] = field(default_factory=list)
    findings: list[GraphEvidenceFindingDraft] = field(default_factory=list)
    rejected_or_ambiguous_candidates: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["quality"] = {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "finding_count": len(self.findings),
            "has_properties": all(bool(item.properties) for item in [*self.nodes, *self.edges]),
            "has_descriptions": all(bool(item.description) for item in [*self.nodes, *self.edges]),
            "has_evidence_quotes": all(bool(item.evidence_quote) for item in [*self.nodes, *self.edges]),
            "extraction_steps": [
                "read approved SchemaGraphModelingAgent projection metadata",
                "ask google/langextract structured evidence extraction for grounded typed mentions",
                "map relations only to approved schema graph edge types",
                "send unmapped or ambiguous relation candidates to review",
                "attach source evidence, confidence, provenance, and review boundary",
                "leave ontology/formal graph writes disabled until review",
            ],
        }
        return payload


class SmallMultilingualEmbeddingAdapter:
    def __init__(self, model_name: str = DEFAULT_DEDUP_EMBEDDING_MODEL):
        self.model_name = model_name
        self._model = None
        self._load_error: str | None = None

    def _load_model(self):
        if self._model is not None or self._load_error is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:  # pragma: no cover - depends on optional local model availability
            self._load_error = str(exc)
        return self._model

    def embed(self, text: str) -> dict[str, Any]:
        text = _clean_text(text or "", 1200)
        if not text:
            return {
                "status": "degraded",
                "reason": "empty_dedup_text",
                "model": self.model_name,
                "vector": None,
                "dim": 0,
            }
        model = self._load_model()
        if model is None:
            return {
                "status": "degraded",
                "reason": "embedding_model_unavailable",
                "detail": self._load_error,
                "model": self.model_name,
                "vector": None,
                "dim": 0,
            }
        vector = model.encode(text, normalize_embeddings=True)
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        values = [float(value) for value in values]
        return {
            "status": "ready",
            "model": self.model_name,
            "vector": values,
            "dim": len(values),
        }


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _slug(value: str, limit: int = 80) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return (text or "item")[:limit].strip("-") or "item"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _dotenv_api_values() -> dict[str, str]:
    global _DOTENV_CACHE
    if os.environ.get("ALETHEIA_DISABLE_DOTENV_API_KEYS"):
        return {}
    if _DOTENV_CACHE is not None:
        return _DOTENV_CACHE
    env_path = Path(os.environ.get("ALETHEIA_ENV_FILE") or (_project_root() / ".env"))
    values: dict[str, str] = {}
    try:
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("\"'")
                if key and value:
                    values[key] = value
    except OSError:
        values = {}
    _DOTENV_CACHE = values
    return values


def _configured_api_key(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    dotenv_values = _dotenv_api_values()
    for name in names:
        value = dotenv_values.get(name)
        if value:
            return value
    return None


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _normalize_identity_text(value: Any) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stopwords = {"the", "a", "an"}
    tokens = [token for token in text.split() if token not in stopwords]
    return " ".join(tokens)


_GENERIC_REFERENCE_MODIFIERS = {
    "this",
    "that",
    "these",
    "those",
    "another",
    "other",
    "same",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "next",
    "nearby",
    "regional",
    "global",
    "major",
    "main",
    "key",
    "critical",
    "strategic",
    "important",
    "vital",
    "new",
    "old",
}
_GENERIC_REFERENCE_HEADS = {
    "area",
    "asset",
    "canal",
    "chokepoint",
    "corridor",
    "country",
    "economy",
    "entity",
    "hub",
    "market",
    "nation",
    "node",
    "port",
    "region",
    "route",
    "state",
    "strait",
    "territory",
    "waterway",
    "zone",
}


def _is_unresolved_entity_reference(label: Any, entity_type: Any = None) -> bool:
    normalized = _normalize_identity_text(label)
    tokens = normalized.split()
    if len(tokens) < 2:
        return False
    type_tokens = set(_normalize_identity_text(entity_type).split())
    generic_heads = _GENERIC_REFERENCE_HEADS | {token for token in type_tokens if len(token) >= 4}
    has_generic_head = any(token in generic_heads for token in tokens)
    if not has_generic_head:
        return False
    return all(token in _GENERIC_REFERENCE_MODIFIERS or token in generic_heads for token in tokens)


def _identity_terms(value: Any) -> set[str]:
    return {token for token in _normalize_identity_text(value).split() if token}


def _stable_identity_values(values: Any) -> list[str]:
    if values in (None, "", []):
        return []
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized = []
    for value in values:
        if isinstance(value, dict):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            text = str(value)
        text = text.strip()
        if text:
            normalized.append(text)
    return sorted(set(normalized))


def _source_identity(value: dict[str, Any]) -> str | None:
    for key in ("canonical_id_hint", "fact_node_hint", "source_pk", "source_id", "source_ref", "metric_key", "source_url"):
        raw = value.get(key)
        if raw not in (None, "", []):
            stable_values = _stable_identity_values(raw)
            return "|".join(stable_values) if stable_values else None
    return None


def _country_from_identity_surface(value: Any):
    if pycountry is None:
        return None
    text = str(value or "").strip()
    if not text:
        return None
    if "=" in text:
        key, raw_value = text.split("=", 1)
        if key.strip().lower() in {"iso3", "alpha_3", "country_code"}:
            text = raw_value.strip()
    if text.lower().startswith("country:"):
        text = text.split(":", 1)[1].strip()
    upper = text.upper()
    if re.fullmatch(r"[A-Z]{3}", upper):
        country = pycountry.countries.get(alpha_3=upper)
        if country is not None:
            return country
    if re.fullmatch(r"[A-Z]{2}", upper):
        country = pycountry.countries.get(alpha_2=upper)
        if country is not None:
            return country
    try:
        matches = pycountry.countries.search_fuzzy(text)
    except LookupError:
        return None
    return matches[0] if matches else None


def _country_identity_normalization(
    entity_type: Any,
    label: Any,
    properties: dict[str, Any],
    payload: dict[str, Any],
    source_identity: str | None,
) -> dict[str, Any]:
    if _normalize_identity_text(entity_type) != "country":
        return {"normalized_label": _normalize_identity_text(label), "aliases": [], "source_identity": source_identity}
    surfaces = [
        label,
        properties.get("iso3"),
        properties.get("country_code"),
        properties.get("source_pk"),
        payload.get("source_pk"),
        source_identity,
    ]
    country = next((match for surface in surfaces if (match := _country_from_identity_surface(surface))), None)
    if country is None:
        return {"normalized_label": _normalize_identity_text(label), "aliases": [], "source_identity": source_identity}
    alias_values = {
        country.alpha_2,
        country.alpha_3,
        country.name,
        str(label or ""),
    }
    for attr in ("official_name", "common_name"):
        value = getattr(country, attr, None)
        if value:
            alias_values.add(value)
    return {
        "normalized_label": _normalize_identity_text(country.alpha_3),
        "aliases": sorted({_normalize_identity_text(alias) for alias in alias_values if _normalize_identity_text(alias)}),
        "source_identity": f"iso3={country.alpha_3}",
    }


def _edge_fact_identity(value: dict[str, Any]) -> str | None:
    for key in (
        "fact_identity",
        "fact_key",
        "claim_identity",
        "canonical_id_hint",
        "fact_node_hint",
        "metric_key",
        "schema_edge_key",
        "source_pk",
        "source_id",
    ):
        raw = value.get(key)
        if raw not in (None, "", []):
            stable_values = [
                item
                for item in _stable_identity_values(raw)
                if not _edge_identity_token_is_evidence_source(item)
            ]
            if stable_values:
                return "|".join(stable_values)
    return None


def _edge_identity_token_is_evidence_source(value: Any) -> bool:
    token = str(value or "").strip().lower()
    if not token:
        return True
    if "://" in token:
        return True
    if re.fullmatch(r"fact:[0-9a-f]{8,}", token):
        return True
    return token.startswith(
        (
            "source_ref:",
            "source_url:",
            "evidence_ref:",
            "url:",
            "run:",
            "frontier:",
            "iterative-graph-run:",
            "enrich-task:",
        )
    )


def _edge_fact_identity_parts(value: Any) -> set[str]:
    parts: set[str] = set()
    for raw in _stable_identity_values(value):
        for token in str(raw).split("|"):
            token = token.strip()
            if not token or _edge_identity_token_is_evidence_source(token):
                continue
            normalized = _normalize_identity_text(token)
            parts.add(normalized or token.lower())
    return parts


def _edge_fact_identity_compatible(left: Any, right: Any) -> bool:
    left_parts = _edge_fact_identity_parts(left)
    right_parts = _edge_fact_identity_parts(right)
    if not left_parts or not right_parts:
        return False
    return bool(left_parts & right_parts)


def _stable_task_id(tenant: str, objective: str, frontier: list[dict[str, Any]]) -> str:
    frontier_keys = [str(item.get("key") or item.get("name") or "") for item in frontier]
    return f"enrich-task:{tenant}:{_slug(objective, 48)}:{_digest({'tenant': tenant, 'objective': objective, 'frontier': frontier_keys}, 12)}"


def _node_identity_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    label = payload.get("label") or item.get("name")
    entity_type = payload.get("ontology_type") or payload.get("type") or item.get("element_type")
    source_identity = _source_identity(properties) or _source_identity(payload)
    normalized_label = _normalize_identity_text(label)
    type_normalization = _country_identity_normalization(entity_type, label, properties, payload, source_identity)
    normalized_label = type_normalization["normalized_label"]
    normalized_aliases = sorted(
        {
            _normalize_identity_text(alias)
            for alias in (properties.get("aliases") or payload.get("aliases") or [])
            if _normalize_identity_text(alias)
        }
        | set(type_normalization["aliases"])
    )
    source_identity = type_normalization["source_identity"]
    return {
        "kind": "node",
        "entity_type": str(entity_type or "").strip(),
        "label": str(label or "").strip(),
        "normalized_label": normalized_label,
        "aliases": normalized_aliases,
        "source_identity": source_identity,
        "source_refs": _stable_identity_values(item.get("evidence_refs") or []),
        "property_fingerprint": _digest(
            {
                "type": entity_type,
                "label": normalized_label,
                "aliases": normalized_aliases,
                "source_identity": source_identity,
            },
            16,
        ),
    }


def _edge_identity_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    metrics = payload.get("metrics") or properties.get("metrics") or []
    if not isinstance(metrics, list):
        metrics = [metrics] if metrics else []
    metric_identity = "|".join(sorted(str(metric) for metric in metrics if metric))
    source_identity = (
        _edge_fact_identity(properties)
        or _edge_fact_identity(payload)
        or payload.get("schema_edge_key")
        or properties.get("schema_edge_key")
        or metric_identity
    )
    if source_identity and metric_identity and metric_identity not in str(source_identity):
        source_identity = f"{source_identity}|{metric_identity}"
    relation = _normalize_identity_text(payload.get("relation"))
    source_node = _normalize_identity_text(payload.get("source_label"))
    target_node = _normalize_identity_text(payload.get("target_label"))
    endpoint_evidence = payload.get("endpoint_dedup_evidence") if isinstance(payload.get("endpoint_dedup_evidence"), dict) else {}

    def canonical_endpoint(role: str) -> str:
        evidence = endpoint_evidence.get(role) if isinstance(endpoint_evidence.get(role), dict) else {}
        matched_key = str(evidence.get("matched_node_key") or evidence.get("candidate_key") or "").strip()
        matched_space = str(evidence.get("matched_space") or evidence.get("matched_source") or "").strip()
        if matched_key and matched_space in {"approved_graph", "approved_graph_instance", "approved_graph_projection"}:
            return _normalize_identity_text(matched_key)
        return ""

    source_canonical_key = canonical_endpoint("source")
    target_canonical_key = canonical_endpoint("target")
    if relation and (source_canonical_key or target_canonical_key):
        source_identity = f"{relation}:{source_canonical_key or source_node}::{target_canonical_key or target_node}"
        if metric_identity:
            source_identity = f"{source_identity}|{metric_identity}"
    return {
        "kind": "edge",
        "source_type": str(payload.get("source_type") or "").strip(),
        "target_type": str(payload.get("target_type") or "").strip(),
        "source_node": source_node,
        "target_node": target_node,
        "source_canonical_key": source_canonical_key,
        "target_canonical_key": target_canonical_key,
        "relation": relation,
        "source_identity": source_identity,
        "property_fingerprint": _digest(
            {
                "source_type": payload.get("source_type"),
                "source_node": source_canonical_key or source_node,
                "relation": relation,
                "target_type": payload.get("target_type"),
                "target_node": target_canonical_key or target_node,
                "source_identity": source_identity,
            },
            16,
        ),
    }


def _finding_evidence_terms(payload: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    evidence_chain = payload.get("evidence_chain") if isinstance(payload.get("evidence_chain"), list) else []
    for entry in evidence_chain:
        if not isinstance(entry, dict):
            continue
        for value in _primitive_text_values(entry, limit=12):
            lowered = value.lower()
            if lowered.startswith(("source_ref:", "source_url:", "evidence_ref:", "url:")):
                continue
            if "://" in lowered:
                continue
            normalized = _normalize_identity_text(value)
            if normalized:
                terms.append(normalized)
    return sorted(dict.fromkeys(terms))[:32]


def _finding_identity_payload(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") or {}
    title = str(payload.get("title") or item.get("name") or "").strip()
    conclusion = str(payload.get("conclusion") or payload.get("description") or "").strip()
    finding_type = str(payload.get("finding_type") or "candidate_finding").strip()
    evidence_terms = _finding_evidence_terms(payload)
    normalized_title = _normalize_identity_text(title)
    normalized_conclusion = _normalize_identity_text(conclusion)
    source_identity = (
        payload.get("finding_identity")
        or payload.get("fact_identity")
        or payload.get("claim_identity")
        or None
    )
    return {
        "kind": "finding",
        "finding_type": finding_type,
        "label": title,
        "normalized_label": normalized_title,
        "conclusion": normalized_conclusion,
        "evidence_terms": evidence_terms,
        "source_identity": source_identity,
        "property_fingerprint": _digest(
            {
                "finding_type": finding_type,
                "title": normalized_title,
                "conclusion": normalized_conclusion,
                "evidence_terms": evidence_terms,
            },
            16,
        ),
    }


def _candidate_identity_payload(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("element_type") == "edge":
        return _edge_identity_payload(item)
    if item.get("element_type") == "node":
        return _node_identity_payload(item)
    if item.get("element_type") == "finding":
        return _finding_identity_payload(item)
    if item.get("element_type") == "ontology_relation":
        payload = item.get("payload") or {}
        relation_label = str(payload.get("relation_label") or item.get("name") or "").strip()
        domain = str(payload.get("domain") or payload.get("source_type") or "").strip()
        range_type = str(payload.get("range") or payload.get("target_type") or "").strip()
        source_identity = payload.get("source_identity") or _digest(
            {
                "relation_label": _normalize_identity_text(relation_label),
                "domain": _normalize_identity_text(domain),
                "range": _normalize_identity_text(range_type),
            },
            16,
        )
        return {
            "kind": "ontology_relation",
            "label": relation_label,
            "normalized_label": _normalize_identity_text(relation_label),
            "domain": domain,
            "range": range_type,
            "source_identity": source_identity,
            "property_fingerprint": source_identity,
        }
    return {
        "kind": item.get("element_type"),
        "label": str(item.get("name") or "").strip(),
        "normalized_label": _normalize_identity_text(item.get("name")),
        "source_identity": None,
        "property_fingerprint": _digest({"type": item.get("element_type"), "name": item.get("name")}, 16),
    }


def _identity_key(tenant: str, identity: dict[str, Any]) -> str:
    if identity.get("kind") == "edge":
        return "edge:{tenant}:{source}:{relation}:{target}:{source_identity}".format(
            tenant=tenant,
            source=identity.get("source_canonical_key") or identity.get("source_node") or "unknown-source",
            relation=identity.get("relation") or "related",
            target=identity.get("target_canonical_key") or identity.get("target_node") or "unknown-target",
            source_identity=identity.get("source_identity") or identity.get("property_fingerprint"),
        )
    if identity.get("kind") == "finding":
        return "finding:{tenant}:{finding_type}:{label}:{source_identity}".format(
            tenant=tenant,
            finding_type=_normalize_identity_text(identity.get("finding_type")) or "candidate-finding",
            label=identity.get("normalized_label") or "unknown",
            source_identity=identity.get("source_identity") or identity.get("property_fingerprint"),
        )
    if identity.get("kind") == "ontology_relation":
        return "ontology-relation:{tenant}:{domain}:{relation}:{range}:{source_identity}".format(
            tenant=tenant,
            domain=_normalize_identity_text(identity.get("domain")) or "unknown-domain",
            relation=identity.get("normalized_label") or "unknown-relation",
            range=_normalize_identity_text(identity.get("range")) or "unknown-range",
            source_identity=identity.get("source_identity") or identity.get("property_fingerprint"),
        )
    return "node:{tenant}:{entity_type}:{label}:{source_identity}".format(
        tenant=tenant,
        entity_type=_normalize_identity_text(identity.get("entity_type")) or "entity",
        label=identity.get("normalized_label") or "unknown",
        source_identity=identity.get("source_identity") or identity.get("property_fingerprint"),
    )


def _candidate_id_for_identity(tenant: str, identity: dict[str, Any]) -> str:
    return f"candidate:{tenant}:{_digest(_identity_key(tenant, identity), 20)}"


def _primitive_text_values(value: Any, *, limit: int = 16) -> list[str]:
    values: list[str] = []
    if value in (None, "", [], {}):
        return values
    if isinstance(value, dict):
        for key in sorted(value):
            if len(values) >= limit:
                break
            child_values = _primitive_text_values(value[key], limit=limit - len(values))
            for child in child_values:
                values.append(f"{key}: {child}")
                if len(values) >= limit:
                    break
        return values
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if len(values) >= limit:
                break
            values.extend(_primitive_text_values(item, limit=limit - len(values)))
        return values
    text = str(value).strip()
    if text:
        values.append(text)
    return values


def _language_hint(text: str) -> str:
    has_cjk = bool(re.search(r"[\u3400-\u9fff]", text or ""))
    has_latin = bool(re.search(r"[A-Za-z]", text or ""))
    if has_cjk and has_latin:
        return "mixed_cjk_latin"
    if has_cjk:
        return "cjk"
    if has_latin:
        return "latin"
    return "unknown"


def _dedup_text_for_identity(identity: dict[str, Any], payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    properties = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    parts: list[str] = [str(identity.get("kind") or "entity")]
    if identity.get("kind") == "edge":
        parts.extend(
            [
                str(identity.get("source_type") or payload.get("source_type") or ""),
                str(identity.get("source_node") or payload.get("source_label") or ""),
                str(identity.get("relation") or payload.get("relation") or ""),
                str(identity.get("target_type") or payload.get("target_type") or ""),
                str(identity.get("target_node") or payload.get("target_label") or ""),
            ]
        )
    elif identity.get("kind") == "finding":
        parts.extend(
            [
                str(identity.get("finding_type") or payload.get("finding_type") or ""),
                str(identity.get("label") or payload.get("title") or payload.get("name") or ""),
                str(identity.get("normalized_label") or ""),
                str(payload.get("conclusion") or ""),
                " ".join(str(term) for term in identity.get("evidence_terms") or []),
            ]
        )
    else:
        entity_type = str(identity.get("entity_type") or payload.get("ontology_type") or payload.get("type") or "")
        label = str(identity.get("label") or payload.get("label") or payload.get("name") or "")
        aliases = [str(alias) for alias in identity.get("aliases") or []]
        class_object_surfaces = [f"{entity_type} {label}", f"{label} {entity_type}"]
        class_object_surfaces.extend(f"{entity_type} {alias}" for alias in aliases[:8])
        parts.extend(
            [
                " ".join(surface for surface in class_object_surfaces if surface.strip()),
                entity_type,
                label,
                str(identity.get("normalized_label") or ""),
                " ".join(aliases),
            ]
        )
    parts.extend(
        [
            str(identity.get("source_identity") or ""),
            str(payload.get("description") or ""),
            str(payload.get("evidence_quote") or ""),
        ]
    )
    parts.extend(_primitive_text_values(properties, limit=12))
    return _clean_text(" | ".join(part for part in parts if part), 1200)


def _normalize_vector(vector: list[float] | None) -> list[float] | None:
    if not vector:
        return None
    norm = math.sqrt(sum(float(value) * float(value) for value in vector))
    if norm <= 0:
        return None
    return [float(value) / norm for value in vector]


def _cosine_distance(left: list[float] | None, right: list[float] | None) -> float | None:
    left_norm = _normalize_vector(left)
    right_norm = _normalize_vector(right)
    if not left_norm or not right_norm or len(left_norm) != len(right_norm):
        return None
    similarity = sum(a * b for a, b in zip(left_norm, right_norm))
    similarity = max(min(similarity, 1.0), -1.0)
    return round(1.0 - similarity, 6)


def _vector_score(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - distance)), 4)


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _structure_compatibility(candidate: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    conflict_fields: list[str] = []
    evidence: list[str] = []
    if candidate.get("kind") != existing.get("kind"):
        return {"compatible": False, "conflict_fields": ["kind"], "evidence": evidence}
    if candidate.get("kind") == "edge":
        required_fields = ("source_type", "target_type", "source_node", "target_node", "relation", "source_identity")
        for field in required_fields:
            if field == "source_node":
                candidate_value = candidate.get("source_canonical_key") or candidate.get("source_node")
                existing_value = existing.get("source_canonical_key") or existing.get("source_node")
                evidence_field = "source_endpoint"
            elif field == "target_node":
                candidate_value = candidate.get("target_canonical_key") or candidate.get("target_node")
                existing_value = existing.get("target_canonical_key") or existing.get("target_node")
                evidence_field = "target_endpoint"
            else:
                candidate_value = candidate.get(field)
                existing_value = existing.get(field)
                evidence_field = field
            if candidate_value and existing_value:
                if candidate_value == existing_value:
                    evidence.append(f"same {evidence_field}")
                elif field == "source_identity" and _edge_fact_identity_compatible(candidate_value, existing_value):
                    evidence.append("compatible source/metric identity")
                else:
                    conflict_fields.append(field)
            elif field in {"source_node", "target_node", "relation"}:
                conflict_fields.append(field)
        return {
            "compatible": not conflict_fields,
            "conflict_fields": conflict_fields,
            "evidence": evidence,
        }
    if candidate.get("kind") == "finding":
        candidate_type = _normalize_identity_text(candidate.get("finding_type"))
        existing_type = _normalize_identity_text(existing.get("finding_type"))
        if candidate_type and existing_type and candidate_type != existing_type:
            conflict_fields.append("finding_type")
        elif candidate_type and existing_type:
            evidence.append("same finding_type")
        return {
            "compatible": not conflict_fields,
            "conflict_fields": conflict_fields,
            "evidence": evidence,
        }
    candidate_type = _normalize_identity_text(candidate.get("entity_type"))
    existing_type = _normalize_identity_text(existing.get("entity_type"))
    if candidate_type and existing_type and candidate_type != existing_type:
        conflict_fields.append("entity_type")
    elif candidate_type and existing_type:
        evidence.append("same entity_type")
    return {
        "compatible": not conflict_fields,
        "conflict_fields": conflict_fields,
        "evidence": evidence,
    }


def _identity_match(candidate: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    evidence: list[str] = []
    conflict_fields: list[str] = []
    if candidate.get("kind") != existing.get("kind"):
        return {"score": 0.0, "evidence": evidence, "conflict_fields": ["kind"]}

    if candidate.get("kind") == "edge":
        fields = ("source_node", "target_node", "relation")
        exact_fields = sum(1 for field in fields if candidate.get(field) and candidate.get(field) == existing.get(field))
        source_match = bool(candidate.get("source_identity") and candidate.get("source_identity") == existing.get("source_identity"))
        if exact_fields == 3 and source_match:
            return {
                "score": 1.0,
                "evidence": ["same source node", "same target node", "same relation", "same source/metric identity"],
                "conflict_fields": [],
            }
        for field in fields:
            if candidate.get(field) == existing.get(field):
                evidence.append(f"same {field}")
            elif candidate.get(field) and existing.get(field):
                conflict_fields.append(field)
        if source_match:
            evidence.append("same source/metric identity")
        score = (exact_fields / 3.0) * 0.8 + (0.2 if source_match else 0.0)
        return {"score": round(score, 4), "evidence": evidence, "conflict_fields": conflict_fields}

    if candidate.get("kind") == "finding":
        type_match = bool(candidate.get("finding_type") and candidate.get("finding_type") == existing.get("finding_type"))
        source_match = bool(candidate.get("source_identity") and candidate.get("source_identity") == existing.get("source_identity"))
        title_score = SequenceMatcher(None, candidate.get("normalized_label") or "", existing.get("normalized_label") or "").ratio()
        candidate_terms = set(candidate.get("evidence_terms") or [])
        existing_terms = set(existing.get("evidence_terms") or [])
        overlap_score = len(candidate_terms & existing_terms) / max(len(candidate_terms | existing_terms), 1)
        if type_match:
            evidence.append("same finding type")
        elif candidate.get("finding_type") and existing.get("finding_type"):
            conflict_fields.append("finding_type")
        if source_match:
            evidence.append("same stable finding identity")
        if title_score >= 0.9:
            evidence.append("high normalized-title similarity")
        elif title_score >= 0.75:
            evidence.append("medium normalized-title similarity")
        if overlap_score >= 0.6:
            evidence.append("evidence-chain token overlap")
        score = 0.35 * (1.0 if type_match else 0.0) + 0.35 * title_score + 0.2 * overlap_score + 0.1 * (1.0 if source_match else 0.0)
        return {"score": round(score, 4), "evidence": evidence, "conflict_fields": conflict_fields}

    type_match = bool(candidate.get("entity_type") and candidate.get("entity_type") == existing.get("entity_type"))
    source_match = bool(candidate.get("source_identity") and candidate.get("source_identity") == existing.get("source_identity"))
    name_score = SequenceMatcher(None, candidate.get("normalized_label") or "", existing.get("normalized_label") or "").ratio()
    alias_hit = bool(set(candidate.get("aliases") or []) & set(existing.get("aliases") or []))
    candidate_terms = _identity_terms(candidate.get("normalized_label"))
    existing_terms = _identity_terms(existing.get("normalized_label"))
    overlap_score = len(candidate_terms & existing_terms) / max(len(candidate_terms | existing_terms), 1)

    if type_match:
        evidence.append("same entity type")
    elif candidate.get("entity_type") and existing.get("entity_type"):
        conflict_fields.append("entity_type")
    if source_match:
        evidence.append("same source identity")
    if alias_hit:
        evidence.append("alias overlap")
    if name_score >= 0.9:
        evidence.append("high normalized-name similarity")
    elif name_score >= 0.75:
        evidence.append("medium normalized-name similarity")
    if overlap_score >= 0.6:
        evidence.append("token overlap")

    if type_match and source_match:
        score = 1.0
    else:
        score = 0.35 * (1.0 if type_match else 0.0) + 0.35 * name_score + 0.15 * overlap_score + 0.1 * (1.0 if alias_hit else 0.0) + 0.05 * (1.0 if source_match else 0.0)
    return {"score": round(score, 4), "evidence": evidence, "conflict_fields": conflict_fields}


def _alias_surface_tokens(identity: dict[str, Any], dedup_text: str | None = None) -> set[str]:
    surfaces: list[Any] = [
        identity.get("label"),
        identity.get("normalized_label"),
        identity.get("source_identity"),
        dedup_text,
    ]
    surfaces.extend(identity.get("aliases") or [])
    tokens: set[str] = set()
    for surface in surfaces:
        normalized = _normalize_identity_text(surface)
        if not normalized:
            continue
        tokens.add(normalized)
        parts = [part for part in normalized.split() if part]
        tokens.update(parts)
        long_parts = [part for part in parts if len(part) > 4]
        if len(long_parts) >= 2:
            tokens.add("".join(part[0] for part in long_parts))
    return tokens


def _short_alias_possible_duplicates(
    candidate: dict[str, Any],
    index: list[dict[str, Any]],
    *,
    candidate_dedup_text: str,
    limit: int = VECTOR_TOP_K,
) -> list[dict[str, Any]]:
    if candidate.get("kind") != "node":
        return []
    candidate_type = _normalize_identity_text(candidate.get("entity_type"))
    candidate_label = candidate.get("normalized_label") or _normalize_identity_text(candidate.get("label"))
    candidate_terms = _identity_terms(candidate_label)
    if not candidate_type or len(candidate_terms) != 1:
        return []
    candidate_token = next(iter(candidate_terms))
    if not (2 <= len(candidate_token) <= 4):
        return []

    candidates: list[dict[str, Any]] = []
    for entry in index:
        existing = entry.get("identity") or {}
        if existing.get("kind") != "node":
            continue
        existing_type = _normalize_identity_text(existing.get("entity_type"))
        if existing_type and existing_type != candidate_type:
            continue
        tokens = _alias_surface_tokens(existing, entry.get("dedup_text"))
        evidence: list[str] = []
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
                "node_key": entry.get("node_key"),
                "status": entry.get("status"),
                "source": entry.get("source"),
                "identity_key": entry.get("identity_key"),
                "score": round(score, 4),
                "text_similarity": round(
                    SequenceMatcher(
                        None,
                        candidate_dedup_text.lower(),
                        (entry.get("dedup_text") or "").lower(),
                    ).ratio(),
                    4,
                )
                if entry.get("dedup_text")
                else 0.0,
                "language_hint": _language_hint(candidate_dedup_text),
                "match_method": "embedding_degraded_alias_scan",
                "evidence": evidence,
            }
        )
    return sorted(candidates, key=lambda item: (-float(item.get("score") or 0.0), item.get("node_key") or ""))[:limit]


def _dedup_decision(match: dict[str, Any] | None) -> str:
    if not match:
        return "new_proposal"
    if match.get("match_method") in {"embedding_degraded_alias_scan", "short_alias_review_gate"}:
        return "needs_review"
    if match.get("match_method") == "embedding_degraded":
        return "new_proposal"
    if match.get("match_method") == "vector_embedding":
        if match.get("structure_compatible") is False:
            distance = match.get("vector_distance")
            if distance is not None and float(distance) <= VECTOR_REVIEW_DISTANCE:
                return "needs_review"
            return "new_proposal"
        distance = match.get("vector_distance")
        if distance is None:
            return "new_proposal"
        if match.get("candidate_kind") == "node":
            candidate_confidence = _float_or_none(match.get("candidate_confidence")) or 0.0
            similarity_score = float(match.get("score") or _vector_score(distance))
            threshold = float(match.get("node_similarity_dedup_threshold") or NODE_SIMILARITY_DIRECT_DEDUP_THRESHOLD)
            if candidate_confidence > NODE_DIRECT_DEDUP_MIN_CONFIDENCE and similarity_score >= threshold:
                if match.get("matched_source") == "current_run_candidate":
                    return "duplicate_current_run"
                if match.get("matched_status") == "proposed":
                    return "duplicate_existing_proposal"
                return "merge_existing"
        if float(distance) <= VECTOR_DUPLICATE_DISTANCE:
            if match.get("matched_source") == "current_run_candidate":
                return "duplicate_current_run"
            if match.get("matched_status") == "proposed":
                return "duplicate_existing_proposal"
            return "merge_existing"
        if float(distance) <= VECTOR_REVIEW_DISTANCE:
            return "needs_review"
        return "new_proposal"
    score = float(match.get("score") or 0.0)
    if score >= 0.92:
        if match.get("matched_source") == "current_run_candidate":
            return "duplicate_current_run"
        if match.get("matched_status") == "proposed":
            return "duplicate_existing_proposal"
        return "merge_existing"
    if score >= 0.75:
        return "needs_review"
    return "new_proposal"


def _extract_terms(text: str) -> dict[str, list[str]]:
    """Extract low-risk query terms without tenant/domain dictionaries."""
    country_terms: list[str] = []
    metric_terms: list[str] = []
    entity_terms: list[str] = []
    for term in re.findall(r"\b[A-Z]{3}\b", text or ""):
        if _country_from_identity_surface(term):
            _append_unique(country_terms, term)
    for term in re.findall(r"\b[A-Za-z][A-Za-z0-9_]{3,}\b", text or ""):
        if "_" in term:
            _append_unique(metric_terms, term)
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,4}\b", text or ""):
        phrase = match.group(0).strip()
        if phrase and phrase not in country_terms:
            _append_unique(entity_terms, phrase)
    return {
        "entities": entity_terms[:8],
        "countries": country_terms[:8],
        "metrics": metric_terms[:8],
    }


def _evidence_excerpt(text: str, terms: list[str], limit: int = 280) -> str:
    normalized = _clean_text(text, 1200)
    if not normalized:
        return ""
    lowered = normalized.lower()
    positions = [lowered.find(term.lower()) for term in terms if term and lowered.find(term.lower()) >= 0]
    if not positions:
        return normalized[:limit].rstrip()
    start = max(min(positions) - 80, 0)
    end = min(max(positions) + 200, len(normalized))
    return normalized[start:end].strip()[:limit].rstrip()


def _entity_key(entity_type: str, label: str) -> str:
    return f"{entity_type}:{label}"


def _entity_description(entity_type: str, label: str, source_title: str) -> str:
    return f"{entity_type} extracted from {source_title or 'crawled evidence'}: {label}."


def _ontology_candidate(artifact_type: str, label: str, description: str, **extra: Any) -> dict[str, Any]:
    candidate = {
        "artifact_type": artifact_type,
        "label": label,
        "description": description,
        "review_required": True,
        "source": "graph_extraction",
    }
    candidate.update({key: value for key, value in extra.items() if value not in (None, "", [])})
    return candidate


def _heuristic_graph_semantics_fallback(frontier_item: dict[str, Any], result, summary: str) -> dict[str, Any]:
    """Legacy/dev smoke extractor with no tenant/domain term dictionaries."""
    source_text = _clean_text(" ".join([result.title or "", result.snippet or "", summary or ""]), 1600)
    terms = _extract_terms(source_text)
    source_ref = result.url
    source_title = result.title or source_ref
    ontology_candidates: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Dedupe ontology candidate labels while keeping the richer first version.
    deduped_candidates = []
    seen_candidates = set()
    for candidate in ontology_candidates:
        key = (candidate.get("artifact_type"), candidate.get("label"))
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        deduped_candidates.append(candidate)

    return {
        "prompt_version": GRAPH_EXTRACTION_PROMPT_VERSION,
        "prompt_contract": GRAPH_EXTRACTION_PROMPT,
        "extraction_source": "heuristic_fallback",
        "source": {"url": source_ref, "title": source_title},
        "schema_context": {"projection_source": "heuristic_fallback", "node_types": [], "edge_types": []},
        "terms": terms,
        "ontology_candidates": deduped_candidates,
        "nodes": nodes,
        "edges": edges,
        "quality": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "has_properties": all(bool(item.get("properties")) for item in [*nodes, *edges]),
            "has_descriptions": all(bool(item.get("description")) for item in [*nodes, *edges]),
            "has_evidence_quotes": all(bool(item.get("evidence_quote")) for item in [*nodes, *edges]),
            "extraction_steps": [
                "identify ontology candidate types and relation schemas",
                "extract typed nodes with descriptions and properties",
                "extract typed binary edges with relation semantics and edge properties",
                "attach evidence quote, source_url, confidence, and review boundary",
                "leave ontology/formal graph writes disabled until review",
            ],
        },
    }


def _append_unique(items: list[str], value: Any, *, excluded: list[dict[str, str]] | None = None, reason: str = "low_signal") -> None:
    if value is None:
        return
    text = str(value).strip()
    if not text:
        return
    low_signal = {"proposed", "graph", "node", "edge", "finding", "frontier", "object", "link", "artifact", "source"}
    normalized = text.lower().replace("_", " ").replace("-", " ")
    if normalized in low_signal or normalized.startswith("proposed graph"):
        if excluded is not None:
            excluded.append({"term": text, "reason": reason})
        return
    if text not in items:
        items.append(text)


def _query_from_term_groups(groups: list[list[str]], *, excluded: list[dict[str, str]] | None = None, limit: int = 18) -> str:
    terms: list[str] = []
    for group in groups:
        for term in group:
            _append_unique(terms, term, excluded=excluded)
    return " ".join(terms[:limit]).strip()


_SEARCH_INSTRUCTION_TERMS = {
    "find",
    "recent",
    "public",
    "evidence",
    "analyze",
    "investigate",
    "about",
    "affecting",
    "related",
    "current",
    "latest",
}
_SEARCH_INTERNAL_TERMS = {
    "proposed_node",
    "proposed_edge",
    "proposed graph",
    "schema",
    "frontier",
    "evidenceentity",
    "has_systemic_risk",
    "depends_on",
    "many_to_many",
}
_RELATION_SEARCH_HINTS = {
    "has_systemic_risk": ["risk", "disruption", "security"],
    "depends_on": ["dependency", "shipping"],
    "many_to_many": [],
}


def _search_surface_term(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text.replace("_", " ").replace("-", " ")
    compact = normalized.lower().strip()
    if compact in _SEARCH_INSTRUCTION_TERMS or compact in _SEARCH_INTERNAL_TERMS:
        return ""
    if compact.startswith("proposed "):
        return ""
    return normalized


def _append_search_term(items: list[str], value: Any, *, excluded: list[dict[str, str]] | None = None, reason: str = "search_noise") -> None:
    text = _search_surface_term(value)
    if not text:
        raw = str(value or "").strip()
        if raw and excluded is not None:
            excluded.append({"term": raw, "reason": reason})
        return
    if text not in items:
        items.append(text)


def _objective_search_hints(objective: str) -> list[str]:
    hints: list[str] = []
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,3}\b", objective or ""):
        _append_search_term(hints, match.group(0))
    for term in re.split(r"[^A-Za-z0-9_/-]+", objective or ""):
        if len(term) < 5:
            continue
        _append_search_term(hints, term)
    return hints[:5]


def _query_from_search_terms(groups: list[list[str]], *, excluded: list[dict[str, str]] | None = None, limit: int = 8) -> str:
    terms: list[str] = []
    for group in groups:
        for term in group:
            _append_search_term(terms, term, excluded=excluded)
    return " ".join(terms[:limit]).strip()


def _graph_context_query_plan(item: dict[str, Any], objective: str, tenant: str) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    context_text = " ".join(
        str(value)
        for value in [
            item.get("key"),
            item.get("name"),
            item.get("artifact_type"),
            item.get("ontology_type"),
            item.get("path"),
            payload.get("label"),
            payload.get("source_label"),
            payload.get("target_label"),
            payload.get("relation"),
            " ".join(map(str, payload.get("metrics") or [])) if isinstance(payload.get("metrics"), list) else payload.get("metrics"),
            objective,
        ]
        if value
    )
    extracted = _extract_terms(context_text)
    excluded_terms: list[dict[str, str]] = []
    country_terms: list[str] = []
    node_terms: list[str] = []
    relation_terms: list[str] = []
    metric_terms: list[str] = []
    domain_terms: list[str] = []

    for term in extracted["countries"]:
        _append_unique(country_terms, term)
        country = _country_from_identity_surface(term)
        if country is not None:
            _append_unique(country_terms, getattr(country, "name", None))
    for term in extracted["entities"]:
        _append_unique(node_terms, term)
    for raw in [item.get("name"), payload.get("source_label"), payload.get("target_label"), payload.get("label")]:
        if raw:
            _append_unique(node_terms, raw, excluded=excluded_terms)
    relation = str(payload.get("relation") or item.get("relation") or "").strip()
    _append_unique(relation_terms, relation, excluded=excluded_terms)
    relation_search_hints = _RELATION_SEARCH_HINTS.get(relation.lower(), [])
    raw_metrics = payload.get("metrics") or extracted["metrics"]
    if not isinstance(raw_metrics, list):
        raw_metrics = [raw_metrics] if raw_metrics else []
    for metric in raw_metrics:
        _append_unique(metric_terms, metric)
    objective_terms = _objective_search_hints(objective)

    query_terms = {
        "countries": country_terms[:4],
        "nodes": node_terms[:6],
        "relations": relation_terms[:5],
        "metrics": metric_terms[:6],
        "domain": domain_terms[:4],
        "objective": objective_terms[:6],
    }
    flat_terms: list[str] = []
    for group in ("countries", "nodes", "relations", "metrics", "domain", "objective"):
        for term in query_terms[group]:
            _append_unique(flat_terms, term, excluded=excluded_terms)
    query = " ".join(flat_terms[:18]).strip() or f"{item.get('name') or item.get('key')} {objective} graph evidence"
    derived_path_label = item.get("path") or payload.get("path_label")
    if not derived_path_label and payload.get("source_label") and payload.get("target_label"):
        metric_suffix = f" -> {', '.join(map(str, raw_metrics))}" if raw_metrics else ""
        derived_path_label = f"{payload.get('source_label')} -> {relation or 'related_to'} -> {payload.get('target_label')}{metric_suffix}"
    has_path_context = bool(derived_path_label)
    has_structured_context = bool(
        payload.get("source_label")
        or payload.get("target_label")
        or relation
        or raw_metrics
    )
    fallback_reason = None if has_path_context or has_structured_context else "node_or_type_only_no_path_context"
    source_label = payload.get("source_label")
    target_label = payload.get("target_label")
    frontier_label = item.get("name") or payload.get("label")
    source_type = payload.get("source_type") or payload.get("ontology_type")
    target_type = payload.get("target_type")
    expected_node_types = [value for value in [source_type, target_type, item.get("ontology_type") or payload.get("ontology_type")] if value]
    expected_relation_types = [relation] if relation else []
    expected_metric_keys = [str(metric) for metric in raw_metrics if metric]
    anchor_terms = [value for value in [source_label, target_label, item.get("name"), item.get("key")] if value]
    schema_terms = [str(value) for value in [*expected_node_types, *expected_relation_types, *expected_metric_keys] if value]
    relevance_gate = {
        "graph_anchor": {
            "required": bool(anchor_terms),
            "terms": anchor_terms[:6],
            "rule": "source should mention at least one frontier endpoint or path label before extraction",
        },
        "objective": {
            "terms": query_terms["objective"][:6],
            "rule": "source should remain aligned with the current enrichment objective",
        },
        "schema": {
            "terms": schema_terms[:8],
            "rule": "structured extraction must map to approved SchemaGraph metadata or review gate",
        },
        "novelty": {
            "rule": "source should add grounded evidence, a new neighbor, a new relation candidate, or contradiction evidence",
        },
        "source_trust": {
            "rule": "source must pass public URL, allowlist/source trust, and private/sensitive URL checks",
        },
    }
    expansion_policy = {
        "default_max_radius": 1,
        "query_ladder": [
            "L0_path_exact",
            "L1_single_endpoint",
            "L2_loose_pair",
            "L3_schema_broad",
        ],
        "coarsen_on": ["no_search_results", "no_trusted_sources", "duplicate_only", "no_new_proposals"],
        "reset_or_hold_on": ["new_reviewable_candidate", "new_frontier_added"],
        "cooldown_signal": "advance to a coarser plan when recent runs have high duplicate rate and low novelty",
    }

    def source_terms(groups: list[tuple[str, list[str]]]) -> list[dict[str, str]]:
        terms: list[dict[str, str]] = []
        seen: set[str] = set()
        for source, values in groups:
            for value in values:
                text = str(value or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                terms.append({"term": text, "source": source})
        return terms

    path_query = _query_from_search_terms(
        [
            [value for value in [source_label, target_label] if value],
            [frontier_label] if frontier_label and not (source_label or target_label) else [],
            relation_search_hints[:2],
            query_terms["objective"][:2],
        ],
        excluded=excluded_terms,
        limit=8,
    ) or query
    source_endpoint_query = ""
    if source_label:
        source_endpoint_query = _query_from_search_terms(
            [
                [source_label],
                relation_search_hints[:2],
                query_terms["objective"][:2],
            ],
            excluded=excluded_terms,
            limit=7,
        )
    target_endpoint_query = ""
    if target_label:
        target_endpoint_query = _query_from_search_terms(
            [
                [target_label],
                relation_search_hints[:2],
                query_terms["objective"][:2],
            ],
            excluded=excluded_terms,
            limit=7,
        )
    single_endpoint_query = target_endpoint_query or source_endpoint_query or _query_from_search_terms(
        [
            [item.get("name")] if item.get("name") else [],
            query_terms["objective"][:2],
        ],
        excluded=excluded_terms,
        limit=7,
    ) or path_query
    loose_pair_query = _query_from_search_terms(
        [
            [value for value in [source_label, target_label] if value],
            [frontier_label] if frontier_label and not (source_label or target_label) else [],
            query_terms["objective"][:2],
        ],
        excluded=excluded_terms,
        limit=7,
    ) or single_endpoint_query
    schema_broad_query = _query_from_search_terms(
        [
            [frontier_label] if frontier_label and not (source_label or target_label) else [],
            [target_label or source_label] if (target_label or source_label) else [],
            relation_search_hints[:2],
            query_terms["objective"][:2],
        ],
        excluded=excluded_terms,
        limit=7,
    ) or loose_pair_query
    common_plan = {
        "expected_node_types": expected_node_types[:4],
        "expected_relation_types": expected_relation_types[:4],
        "expected_metric_keys": expected_metric_keys[:6],
        "relevance_gate": relevance_gate,
    }
    plans = [
        {
            **common_plan,
            "intent": "path_evidence",
            "granularity": "L0_path_exact",
            "degree": 0,
            "coarse_level": 0,
            "query": path_query,
            "radius": 0,
            "priority": 100,
            "source_terms": source_terms(
                [
                    ("frontier.source_label", [source_label] if source_label else []),
                    ("frontier.target_label", [target_label] if target_label else []),
                    ("frontier.label", [frontier_label] if frontier_label and not (source_label or target_label) else []),
                    ("approved_schema.relation", query_terms["relations"]),
                    ("approved_schema.metric", query_terms["metrics"]),
                    ("objective", query_terms["objective"][:4]),
                ]
            ),
            "acceptance": "source must ground the current frontier path, endpoints, relation, or metric before candidate extraction",
        },
        {
            **common_plan,
            "intent": "single_endpoint_expansion",
            "granularity": "L1_single_endpoint",
            "degree": 1,
            "coarse_level": 1,
            "query": single_endpoint_query,
            "radius": 1,
            "priority": 80,
            "source_terms": source_terms(
                [
                    ("frontier.endpoint", [source_label or target_label] if (source_label or target_label) else []),
                    ("approved_schema.node_type", [source_type or target_type] if (source_type or target_type) else []),
                    ("approved_schema.relation", query_terms["relations"][:2]),
                    ("approved_schema.metric", query_terms["metrics"][:2]),
                    ("objective", query_terms["objective"][:4]),
                ]
            ),
            "acceptance": "source should add grounded evidence or a one-hop neighbor around one frontier endpoint",
        },
        {
            **common_plan,
            "intent": "loose_pair_discovery",
            "granularity": "L2_loose_pair",
            "degree": 1,
            "coarse_level": 2,
            "query": loose_pair_query,
            "radius": 1,
            "priority": 60,
            "source_terms": source_terms(
                [
                    ("frontier.source_label", [source_label] if source_label else []),
                    ("frontier.target_label", [target_label] if target_label else []),
                    ("objective", query_terms["objective"][:4]),
                ]
            ),
            "acceptance": "source may connect two runtime frontier nodes even when the exact relation is unknown; extracted relation still needs SchemaGraph mapping or review",
        },
        {
            **common_plan,
            "intent": "schema_broad_discovery",
            "granularity": "L3_schema_broad",
            "degree": 1,
            "coarse_level": 3,
            "query": schema_broad_query,
            "radius": 1,
            "priority": 40,
            "source_terms": source_terms(
                [
                    ("approved_schema.node_type", expected_node_types[:4]),
                    ("approved_schema.relation", expected_relation_types[:4]),
                    ("approved_schema.metric", expected_metric_keys[:4]),
                    ("objective", query_terms["objective"][:4]),
                ]
            ),
            "acceptance": "source should introduce schema-compatible evidence; unmapped relation or type candidates enter review",
        },
    ]
    return {
        "query": path_query,
        "plans": plans,
        "query_queue": [
            {
                "intent": plan["intent"],
                "granularity": plan["granularity"],
                "coarse_level": plan["coarse_level"],
                "query": plan["query"],
            }
            for plan in plans
        ],
        "selected_plan": plans[0],
        "expansion_policy": expansion_policy,
        "relevance_gate": relevance_gate,
        "query_terms": query_terms,
        "graph_context_used": {
            "frontier_key": item.get("key"),
            "frontier_name": item.get("name"),
            "frontier_type": item.get("artifact_type") or item.get("kind"),
            "ontology_type": item.get("ontology_type") or payload.get("ontology_type") or payload.get("source_type"),
            "neighbor_nodes": [value for value in [payload.get("source_label"), payload.get("target_label")] if value],
            "relation": relation or None,
            "metrics": raw_metrics,
            "fallback_reason": fallback_reason,
        },
        "path_context_used": {
            "path_label": derived_path_label,
            "source_label": payload.get("source_label"),
            "target_label": payload.get("target_label"),
            "relation": relation or None,
            "metrics": raw_metrics,
            "fallback_reason": fallback_reason,
        },
        "excluded_terms": excluded_terms,
    }


def deep_graph_profile(evidence_chain: list[dict[str, Any]]) -> dict[str, Any]:
    def step_for(item: dict[str, Any]) -> str | None:
        kind = str(item.get("kind") or "").lower()
        metric = str(item.get("metric") or "").lower()
        if "action" in kind:
            return "action"
        if "relation" in kind or "edge" in kind or item.get("source_label") or item.get("target_label"):
            return "relation"
        if metric or isinstance(item.get("value"), (int, float)):
            return "evidence"
        if item.get("source_ref"):
            return "source_entity"
        return None

    def label_for(item: dict[str, Any]) -> str:
        value = item.get("value")
        if isinstance(value, list):
            return ", ".join(str(v.get("id") or v.get("key") or v.get("label") or v.get("name") or v) for v in value[:5])
        if isinstance(value, dict):
            return str(value.get("label") or value.get("name") or value.get("id") or value.get("key") or item.get("metric") or item.get("kind"))
        return str(value) if value not in (None, "") else str(item.get("metric") or item.get("kind"))

    step_order: list[str] = []
    path = []
    for index, item in enumerate(evidence_chain):
        step = step_for(item)
        if index == 0 and step == "evidence":
            step = "source_entity"
        if index >= 1 and step == "evidence" and "relation" not in step_order:
            step = "relation"
        if index >= 2 and step == "evidence" and "target_entity" not in step_order:
            step = "target_entity"
        if not step:
            continue
        if step not in step_order:
            step_order.append(step)
        path.append(
            {
                "step": step,
                "kind": item.get("kind"),
                "metric": item.get("metric"),
                "source_ref": item.get("source_ref"),
                "label": label_for(item),
            }
        )
    missing_steps = [step for step in DEEP_GRAPH_REQUIRED_STEPS if step not in step_order]
    hop_count = max(len(step_order) - 1, 0)
    return {
        "reasoning_type": "graph_multi_hop" if hop_count >= 3 and not missing_steps else "evidence_chain",
        "finding_emphasis": "deep_graph_finding" if hop_count >= 3 and not missing_steps else "candidate_finding",
        "required_steps": list(DEEP_GRAPH_REQUIRED_STEPS),
        "observed_steps": step_order,
        "missing_steps": missing_steps,
        "hop_count": hop_count,
        "multi_hop": hop_count >= 3 and not missing_steps,
        "path": path,
        "path_label": " -> ".join(item["label"] for item in path if item.get("label")),
    }


class IterativeGraphEnrichmentAgent:
    def __init__(
        self,
        metadata_db_url: str,
        tenant: str = "default",
        *,
        search_results_json: str | None = None,
        seed_urls: list[str] | None = None,
        allowed_domains: list[str] | None = None,
        allow_discovered_domains: bool = False,
        max_iterations: int = 2,
        max_frontier: int = 5,
        max_results_per_query: int = 3,
        langextract_runner=None,
        embedding_adapter=None,
        source_db_url: str | None = None,
        node_similarity_dedup_threshold: float = NODE_SIMILARITY_DIRECT_DEDUP_THRESHOLD,
    ):
        self.engine = create_engine(metadata_db_url)
        ensure_artifact_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.tenant = tenant
        self.source_db_url = source_db_url or self._source_db_url_for_tenant(tenant)
        self.source_engine = create_engine(self.source_db_url)
        self.provider = StaticSearchProvider(search_results_json, seed_urls or [])
        self.allowed_domains = {d.lower().strip() for d in (allowed_domains or []) if d.strip()}
        self.allow_discovered_domains = allow_discovered_domains
        self.max_iterations = max_iterations
        self.max_frontier = max_frontier
        self.max_results_per_query = max_results_per_query
        self.langextract_runner = langextract_runner
        self.embedding_adapter = embedding_adapter or SmallMultilingualEmbeddingAdapter()
        self.node_similarity_dedup_threshold = max(0.0, min(1.0, float(node_similarity_dedup_threshold)))

    def _source_db_url_for_tenant(self, tenant_id: str) -> str:
        try:
            return TenantRegistry.load().get(tenant_id).source_db_url
        except Exception:
            return default_source_db_url()

    def _frontier_from_artifacts(self, session, artifact_keys: list[str] | None) -> list[dict[str, Any]]:
        query = (
            session.query(OntologyArtifact)
            .filter(OntologyArtifact.project_id == self.tenant)
            .filter(OntologyArtifact.artifact_type.notin_(["WebEnrichment"]))
            .order_by(OntologyArtifact.updated_at.desc(), OntologyArtifact.canonical_key.asc())
        )
        if artifact_keys:
            query = query.filter(OntologyArtifact.canonical_key.in_(artifact_keys))
        frontier = []
        for artifact in query.limit(self.max_frontier).all():
            frontier.append(
                {
                    "key": artifact.canonical_key,
                    "name": artifact.name,
                    "artifact_type": artifact.artifact_type,
                    "source": "ontology_artifact",
                    "depth": 0,
                }
            )
        return frontier

    def _query_for_frontier(self, item: dict[str, Any], objective: str) -> str:
        return self._query_plan_for_frontier(item, objective)["query"]

    def _query_plan_for_frontier(self, item: dict[str, Any], objective: str) -> dict[str, Any]:
        return _graph_context_query_plan(item, objective, self.tenant)

    def _frontier_allows_ontology_expansion(self, frontier_item: dict[str, Any]) -> bool:
        payload = frontier_item.get("payload") if isinstance(frontier_item.get("payload"), dict) else {}
        return (
            frontier_item.get("source_kind") == "research_topic"
            or frontier_item.get("kind") == "research_topic"
            or payload.get("research_mode") == "deep_research"
        )

    def _schema_graph_projection_context(self, session) -> dict[str, Any]:
        rows = (
            session.query(OntologyArtifact)
            .filter(OntologyArtifact.project_id == self.tenant)
            .filter(OntologyArtifact.status == "approved")
            .filter(OntologyArtifact.source_agent == "SchemaGraphModelingAgent")
            .order_by(OntologyArtifact.artifact_type.asc(), OntologyArtifact.canonical_key.asc())
            .all()
        )
        node_types: dict[str, dict[str, Any]] = {}
        edge_types: list[dict[str, Any]] = []
        for artifact in rows:
            payload = _json_load(artifact.payload_json, {})
            if payload.get("prompt_version") != "schema_graph_modeling_v1" or payload.get("llm_inferred") is not True:
                continue
            natural_key = artifact.canonical_key.split(":", 1)[1] if ":" in artifact.canonical_key else artifact.canonical_key
            if artifact.artifact_type == "object":
                node_key = str(payload.get("graph_node_key") or payload.get("node_key") or natural_key)
                node_types[node_key] = {
                    "key": node_key,
                    "canonical_key": artifact.canonical_key,
                    "name": payload.get("object_name") or artifact.name or node_key,
                    "description": artifact.description or payload.get("description") or "",
                    "properties": list(payload.get("properties") or []),
                    "mapped_tables": list(payload.get("mapped_table_names") or payload.get("mapped_tables") or []),
                    "source_refs": _json_load(artifact.source_refs_json, []),
                    "confidence": artifact.confidence,
                }
            elif artifact.artifact_type == "link":
                relation = payload.get("relation") or payload.get("graph_edge_name") or payload.get("edge_type")
                if not relation:
                    relation = natural_key or _slug(artifact.name, 64).replace("-", "_")
                edge_types.append(
                    {
                        "key": natural_key,
                        "canonical_key": artifact.canonical_key,
                        "name": artifact.name,
                        "description": artifact.description or payload.get("description") or "",
                        "relation": str(relation),
                        "relation_cardinality": str(payload.get("link_type") or ""),
                        "aliases": list(payload.get("relation_aliases") or payload.get("aliases") or []),
                        "source_node_key": str(payload.get("source_object_key") or payload.get("source_node_key") or ""),
                        "target_node_key": str(payload.get("target_object_key") or payload.get("target_node_key") or ""),
                        "properties": list(payload.get("properties") or payload.get("edge_properties") or []),
                        "source_refs": _json_load(artifact.source_refs_json, []),
                        "confidence": artifact.confidence,
                    }
                )
        return {
            "projection_source": "SchemaGraphModelingAgent" if node_types or edge_types else "none",
            "prompt_version": "schema_graph_modeling_v1",
            "node_types": node_types,
            "edge_types": edge_types,
        }

    def _extract_capitalized_entities(self, text: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z-]*|[A-Z0-9]{2,}", text or "")
        entities: list[str] = []
        current: list[str] = []
        connectors = {"of", "and", "the", "el", "al", "de", "la", "del"}

        def flush() -> None:
            nonlocal current
            if not current:
                return
            if len(current) == 1 and re.fullmatch(r"[A-Z0-9]{2,5}", current[0]):
                _append_unique(entities, current[0])
            elif len(current) >= 2 or any("-" in token for token in current):
                _append_unique(entities, " ".join(current))
            current = []

        for token in tokens:
            if re.fullmatch(r"[A-Z0-9]{2,5}", token):
                flush()
                _append_unique(entities, token)
                continue
            if token[:1].isupper():
                current.append(token)
                continue
            if current and (token.lower() in connectors or "-" in token):
                current.append(token)
                continue
            flush()
        flush()
        return entities

    def _langextract_prompt(self, schema_context: dict[str, Any]) -> str:
        node_types = [
            {"key": key, "name": value.get("name"), "properties": value.get("properties") or []}
            for key, value in (schema_context.get("node_types") or {}).items()
        ]
        edge_types = [
            {
                "key": item.get("key"),
                "name": item.get("name"),
                "relation": item.get("relation"),
                "aliases": item.get("aliases") or [],
                "source_node_key": item.get("source_node_key"),
                "target_node_key": item.get("target_node_key"),
                "properties": item.get("properties") or [],
            }
            for item in (schema_context.get("edge_types") or [])
        ]
        return (
            "Extract graph evidence from the text using only exact source spans. "
            "Return graph_node extractions for entities that match the approved node types, "
            "graph_relation extractions for explicit relation phrases between extracted nodes, "
            "and graph_metric extractions for numeric or named measures that support a relation. "
            "Do not invent node types or relation types. If relation evidence cannot map to an approved edge type, "
            "still extract the relation phrase so the review gate can inspect it. "
            f"Approved node types: {json.dumps(node_types, ensure_ascii=False, sort_keys=True)}. "
            f"Approved edge types: {json.dumps(edge_types, ensure_ascii=False, sort_keys=True)}."
        )

    def _langextract_examples(self, schema_context: dict[str, Any]) -> list[Any]:
        node_types = list((schema_context.get("node_types") or {}).items())
        if len(node_types) < 2:
            return []
        first_key, first_node = node_types[0]
        second_key, second_node = node_types[1]
        relation = "related_to"
        for edge_type in schema_context.get("edge_types") or []:
            if edge_type.get("source_node_key") == first_key and edge_type.get("target_node_key") == second_key:
                relation = self._schema_edge_relation_label(edge_type)
                break
        try:
            import langextract as lx
        except Exception:
            return []
        return [
            lx.data.ExampleData(
                text="Alpha Entity has example relation with Beta Entity supported by value_metric.",
                extractions=[
                    lx.data.Extraction(
                        extraction_class="graph_node",
                        extraction_text="Alpha Entity",
                        attributes={"schema_node_key": first_key, "node_type": str(first_node.get("name") or first_key)},
                    ),
                    lx.data.Extraction(
                        extraction_class="graph_node",
                        extraction_text="Beta Entity",
                        attributes={"schema_node_key": second_key, "node_type": str(second_node.get("name") or second_key)},
                    ),
                    lx.data.Extraction(
                        extraction_class="graph_relation",
                        extraction_text="example relation with",
                        attributes={
                            "source_label": "Alpha Entity",
                            "target_label": "Beta Entity",
                            "relation_label": relation,
                            "source_node_key": first_key,
                            "target_node_key": second_key,
                        },
                    ),
                    lx.data.Extraction(
                        extraction_class="graph_metric",
                        extraction_text="value_metric",
                        attributes={"metric_key": "value_metric", "source_label": "Alpha Entity", "target_label": "Beta Entity"},
                    ),
                ],
            )
        ]

    def _normalize_langextract_documents(self, result: Any) -> dict[str, list[dict[str, Any]]]:
        documents = result if isinstance(result, list) else [result]
        normalized = {"nodes": [], "relations": [], "metrics": []}
        for document in documents:
            for extraction in getattr(document, "extractions", None) or []:
                attributes = dict(getattr(extraction, "attributes", None) or {})
                item = {
                    "class": getattr(extraction, "extraction_class", None),
                    "text": getattr(extraction, "extraction_text", None),
                    "attributes": attributes,
                    "char_interval": None,
                    "confidence": attributes.get("confidence") or getattr(extraction, "confidence", None),
                }
                char_interval = getattr(extraction, "char_interval", None)
                if char_interval is not None:
                    item["char_interval"] = {
                        "start_pos": getattr(char_interval, "start_pos", None),
                        "end_pos": getattr(char_interval, "end_pos", None),
                    }
                if item["class"] == "graph_node":
                    normalized["nodes"].append(item)
                elif item["class"] == "graph_relation":
                    normalized["relations"].append(item)
                elif item["class"] == "graph_metric":
                    normalized["metrics"].append(item)
        return normalized

    def _grounding_from_langextract_item(self, item: dict[str, Any], source_ref: str) -> dict[str, Any]:
        return {
            "extraction_engine": "google/langextract",
            "extraction_text": item.get("text"),
            "char_interval": item.get("char_interval"),
            "attributes": item.get("attributes") or {},
            "confidence": item.get("confidence"),
            "source_ref": source_ref,
        }

    def _run_langextract(self, source_text: str, schema_context: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], str]:
        if self.langextract_runner is not None:
            return self._normalize_langextract_documents(self.langextract_runner(source_text, schema_context)), "runner"
        try:
            import langextract as lx
        except Exception:
            return {"nodes": [], "relations": [], "metrics": []}, "unavailable"
        api_key = _configured_api_key("LANGEXTRACT_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            return {"nodes": [], "relations": [], "metrics": []}, "api_key_missing"
        result = lx.extract(
            text_or_documents=source_text,
            prompt_description=self._langextract_prompt(schema_context),
            examples=self._langextract_examples(schema_context),
            model_id=os.environ.get("LANGEXTRACT_MODEL", "gemini-3.5-flash"),
            api_key=api_key,
            temperature=0.0,
            extraction_passes=max(1, min(int(os.environ.get("LANGEXTRACT_EXTRACTION_PASSES", "1") or "1"), 3)),
            show_progress=False,
        )
        return self._normalize_langextract_documents(result), "ok"

    def _node_labels_for_schema_type(
        self,
        node_type: dict[str, Any],
        *,
        source_text: str,
        frontier_item: dict[str, Any],
        entities: list[str],
    ) -> list[str]:
        key = str(node_type.get("key") or "").lower()
        name = str(node_type.get("name") or "").lower()
        properties = " ".join(str(item).lower() for item in (node_type.get("properties") or []))
        schema_text = " ".join([key, name, properties])
        frontier_text = " ".join(
            str(value or "").lower()
            for value in (frontier_item.get("key"), frontier_item.get("name"), frontier_item.get("artifact_type"))
        )
        labels: list[str] = []
        looks_like_code_type = any(token in schema_text for token in ("iso", "code", "country", "economy"))
        if looks_like_code_type:
            for entity in entities:
                if re.fullmatch(r"[A-Z0-9]{2,5}", entity):
                    _append_unique(labels, entity)
            return labels[:5]

        type_tokens = {token for token in re.split(r"[^a-z0-9]+", schema_text) if len(token) >= 4}
        frontier_mentions_type = bool(type_tokens and any(token in frontier_text for token in type_tokens))
        if not frontier_mentions_type:
            return []
        for entity in entities:
            if re.fullmatch(r"[A-Z0-9]{2,5}", entity):
                continue
            if entity.lower() in {"schema", "graph", "modeling", "agent"}:
                continue
            if entity.lower() in source_text.lower():
                _append_unique(labels, entity)
        return labels[:4]

    def _relation_phrase_between(self, source_text: str, source_label: str, target_label: str) -> str | None:
        text = _clean_text(source_text, 2000)
        if not source_label or not target_label:
            return None
        escaped_source = re.escape(source_label)
        escaped_target = re.escape(target_label)
        patterns = (
            rf"\b{escaped_source}\b(?P<relation>.{{1,120}}?)\b{escaped_target}\b",
            rf"\b{escaped_target}\b(?P<relation>.{{1,120}}?)\b{escaped_source}\b",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            relation = _clean_text(match.group("relation"), 120)
            relation = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", relation).strip()
            if not relation:
                continue
            if len(re.findall(r"[A-Za-z0-9_]+", relation)) > 12:
                continue
            return relation[:100]
        return None

    def _semantic_phrase_key(self, value: Any) -> str:
        text = str(value or "").lower().strip()
        text = text.replace("_", " ").replace("-", " ")
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(token for token in text.split() if token)

    def _schema_edge_relation_label(self, edge_type: dict[str, Any]) -> str:
        relation = str(edge_type.get("relation") or "").strip()
        structural = {"many_to_many", "one_to_many", "many_to_one", "one_to_one"}
        if relation and _slug(relation, 64).replace("-", "_") not in structural:
            return relation
        key = str(edge_type.get("key") or "").strip()
        if key:
            return key
        name = str(edge_type.get("name") or "").strip()
        if name:
            return _slug(name, 64).replace("-", "_")
        return relation or "related_to"

    def _edge_relation_aliases(self, edge_type: dict[str, Any]) -> set[str]:
        aliases: set[str] = set()
        for value in (
            self._schema_edge_relation_label(edge_type),
            edge_type.get("relation"),
            edge_type.get("key"),
            edge_type.get("name"),
        ):
            normalized = self._semantic_phrase_key(value)
            if normalized:
                aliases.add(normalized)
        for value in edge_type.get("aliases") or []:
            normalized = self._semantic_phrase_key(value)
            if normalized:
                aliases.add(normalized)
        return aliases

    def _relation_candidate_labels(self, relation_item: dict[str, Any] | None) -> list[str]:
        if not relation_item:
            return []
        attrs = relation_item.get("attributes") or {}
        values = [
            attrs.get("schema_edge_key"),
            attrs.get("edge_type"),
            attrs.get("relation_key"),
            attrs.get("relation_label"),
            attrs.get("relation"),
            relation_item.get("text"),
        ]
        labels: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in labels:
                labels.append(text)
        return labels

    def _relation_matches_approved_edge(self, relation_item: dict[str, Any] | None, edge_type: dict[str, Any]) -> bool:
        aliases = self._edge_relation_aliases(edge_type)
        if not aliases:
            return False
        for label in self._relation_candidate_labels(relation_item):
            normalized = self._semantic_phrase_key(label)
            if not normalized:
                continue
            if normalized in aliases:
                return True
            if any(SequenceMatcher(None, normalized, alias).ratio() >= 0.88 for alias in aliases):
                return True
        return False

    def _relation_item_for_edge_nodes(
        self,
        relation_items: list[dict[str, Any]],
        source_node: GraphEvidenceNodeDraft,
        target_node: GraphEvidenceNodeDraft,
    ) -> tuple[dict[str, Any] | None, str | None]:
        for relation_item in relation_items:
            attrs = relation_item.get("attributes") or {}
            relation_source = str(attrs.get("source_label") or "").strip()
            relation_target = str(attrs.get("target_label") or "").strip()
            if relation_source == source_node.label and relation_target == target_node.label:
                return relation_item, "forward"
            if relation_source == target_node.label and relation_target == source_node.label:
                return relation_item, "reversed"
        return None, None

    def _approved_metric_terms_for_pair(
        self,
        edge_type: dict[str, Any],
        source_text: str,
        extracted_metrics: list[str],
    ) -> list[str]:
        approved_properties = [str(prop) for prop in edge_type.get("properties") or [] if prop]
        approved_by_key = {self._semantic_phrase_key(prop): prop for prop in approved_properties}
        metric_terms: list[str] = []
        for prop in approved_properties:
            if prop.lower() in source_text.lower():
                _append_unique(metric_terms, prop)
        for metric in extracted_metrics:
            normalized = self._semantic_phrase_key(metric)
            if normalized in approved_by_key:
                _append_unique(metric_terms, approved_by_key[normalized])
                continue
            for approved_key, approved_prop in approved_by_key.items():
                if normalized and SequenceMatcher(None, normalized, approved_key).ratio() >= 0.9:
                    _append_unique(metric_terms, approved_prop)
                    break
        return metric_terms[:8]

    def _extract_graph_evidence_contract(
        self,
        session,
        frontier_item: dict[str, Any],
        result,
        summary: str,
    ) -> dict[str, Any]:
        source_text = _clean_text(" ".join([result.title or "", result.snippet or "", summary or ""]), 2000)
        source_ref = result.url
        source_title = result.title or source_ref
        schema_context = self._schema_graph_projection_context(session)
        if schema_context["projection_source"] != "SchemaGraphModelingAgent":
            return GraphEvidenceExtractionDraft(
                prompt_version=GRAPH_EXTRACTION_PROMPT_VERSION,
                prompt_contract=GRAPH_EXTRACTION_PROMPT,
                extraction_source="structured_llm_contract",
                extraction_engine="google/langextract",
                extraction_engine_status="skipped_no_schema_projection",
                source={"url": source_ref, "title": source_title},
                schema_context=schema_context,
                rejected_or_ambiguous_candidates=[
                    {
                        "reason": "no_approved_schema_graph_projection",
                        "review_status": "needs_review",
                        "review_required": True,
                    }
                ],
                assumptions=["No approved SchemaGraphModelingAgent projection is available for this tenant."],
            ).to_payload()

        langextract_candidates, langextract_status = self._run_langextract(source_text, schema_context)
        if langextract_status not in {"ok", "runner"}:
            return GraphEvidenceExtractionDraft(
                prompt_version=GRAPH_EXTRACTION_PROMPT_VERSION,
                prompt_contract=GRAPH_EXTRACTION_PROMPT,
                extraction_source="structured_llm_contract",
                extraction_engine="google/langextract",
                extraction_engine_status=langextract_status,
                source={"url": source_ref, "title": source_title},
                schema_context=schema_context,
                rejected_or_ambiguous_candidates=[
                    {
                        "reason": f"langextract_{langextract_status}",
                        "review_status": "blocked",
                        "review_required": True,
                        "source_ref": source_ref,
                    }
                ],
                assumptions=[
                    "LangExtract structured extraction was unavailable; production graph extraction does not fall back to heuristic entity or relation rules.",
                ],
            ).to_payload()
        entities = self._extract_capitalized_entities(source_text)
        nodes_by_type: dict[str, list[GraphEvidenceNodeDraft]] = {}
        ontology_candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for node_key, node_type in schema_context["node_types"].items():
            labels: list[str] = []
            grounding_by_label: dict[str, list[dict[str, Any]]] = {}
            for item in langextract_candidates["nodes"]:
                attrs = item.get("attributes") or {}
                attr_key = str(attrs.get("schema_node_key") or attrs.get("node_key") or "").strip()
                attr_type = str(attrs.get("node_type") or attrs.get("type") or "").strip().lower()
                if attr_key == node_key or attr_type == str(node_type.get("name") or "").lower():
                    label = str(item.get("text") or "").strip()
                    _append_unique(labels, label)
                    if label:
                        grounding_by_label.setdefault(label, []).append(self._grounding_from_langextract_item(item, source_ref))
            if not labels:
                continue
            for label in labels:
                evidence_quote = _evidence_excerpt(source_text, [label])
                source_grounding = grounding_by_label.get(label, [])
                if _is_unresolved_entity_reference(label, node_type.get("name")):
                    rejected.append(
                        {
                            "reason": "unresolved_entity_reference",
                            "review_status": "blocked",
                            "review_required": False,
                            "entity_type": node_type.get("name"),
                            "label": label,
                            "evidence_quote": evidence_quote,
                            "source_ref": source_ref,
                            "schema_projection_source": "SchemaGraphModelingAgent",
                            "source_grounding": source_grounding,
                            "resolution_policy": "generic or contextual entity references must resolve to a canonical named entity before graph proposal",
                        }
                    )
                    continue
                properties = {
                    "canonical_id_hint": f"{node_type['name']}:{label}",
                    "source_title": source_title,
                    "source_url": source_ref,
                    "extracted_from_frontier": frontier_item.get("key"),
                    "schema_node_key": node_key,
                    "schema_artifact_key": node_type.get("canonical_key"),
                    "schema_projection_source": "SchemaGraphModelingAgent",
                    "source_grounding": source_grounding,
                }
                node = GraphEvidenceNodeDraft(
                    type=str(node_type["name"]),
                    label=label,
                    description=f"{node_type['name']} supported by crawled evidence and approved schema projection: {label}.",
                    properties=properties,
                    evidence_quote=evidence_quote,
                    confidence=min(float(node_type.get("confidence") or 0.72), 0.82),
                    source_ref=source_ref,
                    source_grounding=source_grounding,
                )
                nodes_by_type.setdefault(node_key, []).append(node)
            ontology_candidates.append(
                _ontology_candidate(
                    "object",
                    str(node_type["name"]),
                    node_type.get("description") or f"Approved schema graph node type {node_type['name']}.",
                    review_required=False,
                    schema_artifact_key=node_type.get("canonical_key"),
                    schema_projection_source="SchemaGraphModelingAgent",
                )
            )

        edges: list[GraphEvidenceEdgeDraft] = []
        mapped_node_pairs: set[tuple[str, str]] = set()
        metrics_by_pair: dict[frozenset[str], list[str]] = {}
        metric_grounding_by_pair: dict[frozenset[str], list[dict[str, Any]]] = {}
        for metric in langextract_candidates["metrics"]:
            attrs = metric.get("attributes") or {}
            source_label = str(attrs.get("source_label") or "").strip()
            target_label = str(attrs.get("target_label") or "").strip()
            if source_label and target_label:
                pair = frozenset({source_label, target_label})
                metrics_by_pair.setdefault(pair, []).append(str(metric.get("text") or attrs.get("metric_key") or ""))
                metric_grounding_by_pair.setdefault(pair, []).append(self._grounding_from_langextract_item(metric, source_ref))
        relation_items = list(langextract_candidates["relations"])
        for edge_type in schema_context["edge_types"]:
            edge_relation = self._schema_edge_relation_label(edge_type)
            source_nodes = nodes_by_type.get(edge_type.get("source_node_key"), [])
            target_nodes = nodes_by_type.get(edge_type.get("target_node_key"), [])
            if not source_nodes or not target_nodes:
                if source_nodes or target_nodes:
                    rejected.append(
                        {
                            "reason": "ambiguous_relation_endpoint",
                            "review_status": "needs_review",
                            "review_required": True,
                            "schema_edge_key": edge_type.get("canonical_key"),
                            "relation": edge_relation,
                            "source_node_count": len(source_nodes),
                            "target_node_count": len(target_nodes),
                        }
                    )
                continue
            ontology_candidates.append(
                _ontology_candidate(
                    "link",
                    edge_relation,
                    edge_type.get("description") or f"Approved schema graph relation {edge_relation}.",
                    domain=(schema_context["node_types"].get(edge_type.get("source_node_key")) or {}).get("name"),
                    range=(schema_context["node_types"].get(edge_type.get("target_node_key")) or {}).get("name"),
                    review_required=False,
                    schema_artifact_key=edge_type.get("canonical_key"),
                    schema_projection_source="SchemaGraphModelingAgent",
                )
            )
            for source_node in source_nodes[:4]:
                for target_node in target_nodes[:2]:
                    pair_key = frozenset({source_node.label, target_node.label})
                    relation_evidence, relation_direction = self._relation_item_for_edge_nodes(
                        relation_items,
                        source_node,
                        target_node,
                    )
                    if not self._relation_matches_approved_edge(relation_evidence, edge_type):
                        if relation_evidence:
                            attrs = relation_evidence.get("attributes") or {}
                            rejected.append(
                                {
                                    "reason": "ambiguous_relation",
                                    "review_status": "needs_review",
                                    "review_required": True,
                                    "source_type": source_node.type,
                                    "source_label": source_node.label,
                                    "target_type": target_node.type,
                                    "target_label": target_node.label,
                                    "relation_label": str(attrs.get("relation_label") or relation_evidence.get("text") or "").strip(),
                                    "evidence_quote": _evidence_excerpt(source_text, [source_node.label, target_node.label]),
                                    "source_ref": source_ref,
                                    "schema_projection_source": "SchemaGraphModelingAgent",
                                    "schema_edge_key": edge_type.get("canonical_key"),
                                    "source_grounding": [
                                        *source_node.source_grounding,
                                        *target_node.source_grounding,
                                        self._grounding_from_langextract_item(relation_evidence, source_ref),
                                    ],
                                }
                            )
                        continue
                    metric_terms = self._approved_metric_terms_for_pair(
                        edge_type,
                        source_text,
                        metrics_by_pair.get(pair_key, []),
                    )
                    evidence_quote = _evidence_excerpt(
                        source_text,
                        [
                            source_node.label,
                            target_node.label,
                            str((relation_evidence or {}).get("text") or ""),
                            *metric_terms,
                        ],
                    )
                    relation_grounding = (
                        [self._grounding_from_langextract_item(relation_evidence, source_ref)]
                        if relation_evidence
                        else []
                    )
                    source_grounding = [
                        *source_node.source_grounding,
                        *target_node.source_grounding,
                        *relation_grounding,
                        *metric_grounding_by_pair.get(pair_key, []),
                    ]
                    edges.append(
                        GraphEvidenceEdgeDraft(
                            source_type=source_node.type,
                            source_label=source_node.label,
                            relation=edge_relation,
                            target_type=target_node.type,
                            target_label=target_node.label,
                            description=f"{source_node.label} is connected to {target_node.label} by approved schema relation {edge_relation}.",
                            properties={
                                "metrics": metric_terms,
                                "source_url": source_ref,
                                "source_title": source_title,
                                "evidence_quote": evidence_quote,
                                "extracted_from_frontier": frontier_item.get("key"),
                                "schema_edge_key": edge_type.get("canonical_key"),
                                "schema_projection_source": "SchemaGraphModelingAgent",
                                "relation_cardinality": edge_type.get("relation_cardinality") or None,
                                "fact_node_hint": f"{edge_relation}:{source_node.label}::{target_node.label}",
                                "source_grounding": source_grounding,
                                "relation_direction": relation_direction,
                            },
                            evidence_quote=evidence_quote,
                            confidence=0.78 if metric_terms else 0.7,
                            source_ref=source_ref,
                            source_grounding=source_grounding,
                        )
                    )
                    mapped_node_pairs.add((source_node.type, target_node.type))

        observed_nodes = [node for values in nodes_by_type.values() for node in values]
        relation_items = list(langextract_candidates["relations"])
        if len(observed_nodes) >= 2:
            for idx, source_node in enumerate(observed_nodes[:6]):
                for target_node in observed_nodes[idx + 1 : 7]:
                    if source_node.type == target_node.type:
                        continue
                    if (source_node.type, target_node.type) in mapped_node_pairs or (target_node.type, source_node.type) in mapped_node_pairs:
                        continue
                    relation_label = None
                    relation_grounding: list[dict[str, Any]] = []
                    for relation_item in relation_items:
                        attrs = relation_item.get("attributes") or {}
                        relation_source = str(attrs.get("source_label") or "").strip()
                        relation_target = str(attrs.get("target_label") or "").strip()
                        if {relation_source, relation_target} == {source_node.label, target_node.label}:
                            relation_label = str(attrs.get("relation_label") or relation_item.get("text") or "").strip()
                            relation_grounding = [self._grounding_from_langextract_item(relation_item, source_ref)]
                            break
                    relation_label = relation_label or self._relation_phrase_between(source_text, source_node.label, target_node.label)
                    if not relation_label:
                        continue
                    rejected.append(
                        {
                            "reason": "unmapped_relation" if not schema_context["edge_types"] else "ambiguous_relation",
                            "review_status": "needs_review",
                            "review_required": True,
                            "source_type": source_node.type,
                            "source_label": source_node.label,
                            "target_type": target_node.type,
                            "target_label": target_node.label,
                            "relation_label": relation_label,
                            "evidence_quote": _evidence_excerpt(source_text, [source_node.label, target_node.label]),
                            "source_ref": source_ref,
                            "schema_projection_source": "SchemaGraphModelingAgent",
                            "source_grounding": [
                                *source_node.source_grounding,
                                *target_node.source_grounding,
                                *relation_grounding,
                            ],
                        }
                    )
                    break
                if rejected and rejected[-1].get("reason") in {"unmapped_relation", "ambiguous_relation"}:
                    break

        observed_labels = {node.label for node in observed_nodes}
        rejected_relation_keys = {
            (
                str(item.get("source_label") or ""),
                str(item.get("relation_label") or item.get("relation") or ""),
                str(item.get("target_label") or ""),
            )
            for item in rejected
        }
        for relation_item in relation_items[:8]:
            attrs = relation_item.get("attributes") or {}
            source_label = str(attrs.get("source_label") or "").strip()
            target_label = str(attrs.get("target_label") or "").strip()
            relation_label = str(attrs.get("relation_label") or relation_item.get("text") or "").strip()
            if not relation_label or not (source_label or target_label):
                continue
            relation_key = (source_label, relation_label, target_label)
            if relation_key in rejected_relation_keys:
                continue
            if source_label in observed_labels and target_label in observed_labels:
                continue
            rejected.append(
                {
                    "reason": "ambiguous_relation_endpoint",
                    "review_status": "needs_review",
                    "review_required": True,
                    "source_type": str(attrs.get("source_type") or attrs.get("source_node_type") or "").strip(),
                    "source_label": source_label,
                    "target_type": str(attrs.get("target_type") or attrs.get("target_node_type") or "").strip(),
                    "target_label": target_label,
                    "relation_label": relation_label,
                    "evidence_quote": _evidence_excerpt(
                        source_text,
                        [source_label, target_label, relation_label, str(relation_item.get("text") or "")],
                    ),
                    "source_ref": source_ref,
                    "schema_projection_source": "SchemaGraphModelingAgent",
                    "source_grounding": [self._grounding_from_langextract_item(relation_item, source_ref)],
                }
            )
            rejected_relation_keys.add(relation_key)

        findings: list[GraphEvidenceFindingDraft] = []
        if edges:
            edge = edges[0]
            metrics = list(edge.properties.get("metrics") or [])
            risk_indicators = re.findall(r"\b(?:likelihood|severity)_[A-Za-z0-9_]+\b", source_text)
            evidence_chain = []
            if risk_indicators:
                evidence_chain.append(
                    {"kind": "risk_indicator", "metric": risk_indicators[0], "value": risk_indicators[0], "source_ref": source_ref}
                )
            evidence_chain.extend(
                [
                    {"kind": "dependent_countries", "metric": "iso3", "value": [{"iso3": edge.source_label}], "source_ref": source_ref},
                    {"kind": edge.target_type, "metric": edge.target_type, "value": edge.target_label, "source_ref": source_ref},
                    {"kind": "relation", "metric": edge.relation, "value": edge.relation, "source_ref": source_ref},
                ]
            )
            if metrics:
                evidence_chain.append({"kind": "risk_metric", "metric": metrics[0], "value": metrics[0], "source_ref": source_ref})
            evidence_chain.append(
                {
                    "kind": "recommended_action",
                    "value": {"label": "Review the proposed graph path and supporting source evidence."},
                    "source_ref": "Aletheia proposed graph review gate",
                }
            )
            findings.append(
                GraphEvidenceFindingDraft(
                    title=f"{edge.source_label} {edge.relation.replace('_', ' ')} {edge.target_label}",
                    conclusion=summary,
                    evidence_chain=evidence_chain,
                    confidence=0.72,
                    source_ref=source_ref,
                )
            )

        return GraphEvidenceExtractionDraft(
            prompt_version=GRAPH_EXTRACTION_PROMPT_VERSION,
            prompt_contract=GRAPH_EXTRACTION_PROMPT,
            extraction_source="structured_llm_contract",
            extraction_engine="google/langextract",
            extraction_engine_status=langextract_status,
            source={"url": source_ref, "title": source_title},
            schema_context=schema_context,
            ontology_candidates=ontology_candidates,
            nodes=[node for values in nodes_by_type.values() for node in values],
            edges=edges,
            findings=findings,
            rejected_or_ambiguous_candidates=rejected,
            assumptions=[
                "Schema node and edge types come from approved SchemaGraphModelingAgent projection metadata.",
                "Unmapped relations are not promoted; they remain review candidates.",
            ],
        ).to_payload()

    def _candidate_elements(self, extraction: dict[str, Any], frontier_item: dict[str, Any], result, summary: str, iteration: int) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        source_ref = result.url
        source_title = result.title or source_ref
        emitted_node_keys: set[tuple[str, str]] = set()
        emitted_edge_keys: set[tuple[str, str, str]] = set()
        for node in extraction["nodes"]:
            emitted_node_keys.add((str(node["type"]), str(node["label"])))
            elements.append(
                {
                    "element_type": "node",
                    "name": node["label"],
                    "payload": {
                        "ontology_type": node["type"],
                        "label": node["label"],
                        "description": node["description"],
                        "properties": node["properties"],
                        "evidence_quote": node["evidence_quote"],
                        "source_grounding": node.get("source_grounding") or node["properties"].get("source_grounding") or [],
                        "ontology_candidate": next(
                            (
                                candidate
                                for candidate in extraction["ontology_candidates"]
                                if candidate.get("artifact_type") == "object" and candidate.get("label") == node["type"]
                            ),
                            None,
                        ),
                        "extraction": {
                            "prompt_version": extraction["prompt_version"],
                            "extraction_source": extraction.get("extraction_source"),
                            "extraction_engine": extraction.get("extraction_engine"),
                            "extraction_engine_status": extraction.get("extraction_engine_status"),
                            "schema_context": extraction.get("schema_context"),
                            "steps": extraction["quality"]["extraction_steps"],
                            "review_boundary": "proposed_graph_space",
                            "canonical_ontology_write": False,
                            "formal_graph_write": False,
                        },
                        "discovered_from": frontier_item.get("key"),
                    },
                    "evidence_refs": [source_ref],
                    "source_url": source_ref,
                    "confidence": node["confidence"],
                    "iteration": iteration,
                }
            )
        for edge in extraction["edges"]:
            edge_schema_key = (edge.get("properties") or {}).get("schema_edge_key")
            relation_ontology = next(
                (
                    candidate
                    for candidate in extraction["ontology_candidates"]
                    if candidate.get("artifact_type") == "link"
                    and (
                        (edge_schema_key and candidate.get("schema_artifact_key") == edge_schema_key)
                        or candidate.get("label") == edge["relation"]
                    )
                ),
                None,
            )
            elements.append(
                {
                    "element_type": "edge",
                    "name": f"{edge['source_label']} {edge['relation'].replace('_', ' ')} {edge['target_label']}",
                    "payload": {
                        "source_type": edge["source_type"],
                        "target_type": edge["target_type"],
                        "relation": edge["relation"],
                        "source_label": edge["source_label"],
                        "target_label": edge["target_label"],
                        "description": edge["description"],
                        "properties": edge["properties"],
                        "metrics": edge["properties"].get("metrics") or [],
                        "evidence_quote": edge["evidence_quote"],
                        "source_grounding": edge.get("source_grounding") or edge["properties"].get("source_grounding") or [],
                        "relation_ontology_candidate": relation_ontology,
                        "extraction": {
                            "prompt_version": extraction["prompt_version"],
                            "extraction_source": extraction.get("extraction_source"),
                            "extraction_engine": extraction.get("extraction_engine"),
                            "extraction_engine_status": extraction.get("extraction_engine_status"),
                            "schema_context": extraction.get("schema_context"),
                            "steps": extraction["quality"]["extraction_steps"],
                            "review_boundary": "proposed_graph_space",
                            "canonical_ontology_write": False,
                            "formal_graph_write": False,
                        },
                    },
                    "evidence_refs": [source_ref],
                    "source_url": source_ref,
                    "confidence": edge["confidence"],
                    "iteration": iteration,
                }
            )
            emitted_edge_keys.add((str(edge["source_label"]), str(edge["relation"]), str(edge["target_label"])))
        fact_reasons = {"ambiguous_relation", "unmapped_relation", "ambiguous_relation_endpoint"}
        allow_ontology_expansion = self._frontier_allows_ontology_expansion(frontier_item)
        for candidate in extraction.get("rejected_or_ambiguous_candidates") or []:
            reason = str(candidate.get("reason") or "").strip()
            if reason not in fact_reasons or not candidate.get("review_required"):
                continue
            candidate["proposal_suppressed"] = True
            candidate["proposal_suppressed_reason"] = (
                "ambiguous_or_unmapped_relation_requires_grounded_entities_and_relation"
            )
            candidate["review_surface"] = "live_trace"
            candidate["canonical_ontology_write"] = False
            candidate["formal_graph_write"] = False
            relation_label = str(candidate.get("relation_label") or candidate.get("relation") or "").strip()
            if allow_ontology_expansion and relation_label and reason in {"ambiguous_relation", "unmapped_relation"}:
                source_type = str(candidate.get("source_type") or "").strip()
                target_type = str(candidate.get("target_type") or "").strip()
                ontology_candidate = _ontology_candidate(
                    "link",
                    relation_label,
                    (
                        f"Candidate relation discovered during deep research: {relation_label}. "
                        "Requires human ontology review before becoming an approved SchemaGraph relation."
                    ),
                    domain=source_type,
                    range=target_type,
                    review_required=True,
                    schema_projection_source="DeepResearchOntologyExpansion",
                    source_ref=candidate.get("source_ref") or source_ref,
                )
                elements.append(
                    {
                        "element_type": "ontology_relation",
                        "name": relation_label,
                        "payload": {
                            "relation_label": relation_label,
                            "domain": source_type,
                            "range": target_type,
                            "source_type": source_type,
                            "target_type": target_type,
                            "source_label": candidate.get("source_label"),
                            "target_label": candidate.get("target_label"),
                            "description": ontology_candidate["description"],
                            "ontology_candidate": ontology_candidate,
                            "evidence_quote": candidate.get("evidence_quote"),
                            "source_grounding": candidate.get("source_grounding") or [],
                            "source_url": candidate.get("source_ref") or source_ref,
                            "source_title": source_title,
                            "discovered_from": frontier_item.get("key"),
                            "research_mode": "deep_research",
                            "proposal_scope": "ontology_expansion",
                            "review_status": "needs_review",
                            "review_required": True,
                            "rejected_candidate_reason": reason,
                            "extraction": {
                                "prompt_version": extraction["prompt_version"],
                                "extraction_source": extraction.get("extraction_source"),
                                "schema_context": extraction.get("schema_context"),
                                "quality": extraction["quality"],
                                "review_boundary": "ontology_relation_review",
                                "canonical_ontology_write": False,
                                "formal_graph_write": False,
                            },
                            "recommended_action": "Review whether this relation type should be added to the ontology/schema graph.",
                            "writes_canonical": False,
                        },
                        "evidence_refs": [candidate.get("source_ref") or source_ref],
                        "source_url": candidate.get("source_ref") or source_ref,
                        "confidence": 0.58,
                        "status": "needs_more_evidence",
                        "iteration": iteration,
                    }
                )
        for finding in extraction.get("findings") or []:
            evidence_chain = finding.get("evidence_chain") or []
            elements.append(
                {
                    "element_type": "finding",
                    "name": finding.get("title") or "Candidate graph finding",
                    "payload": {
                        "finding_type": "deep_graph_finding",
                        "title": finding.get("title") or "Candidate graph finding",
                        "conclusion": finding.get("conclusion") or summary,
                        "evidence_chain": evidence_chain,
                        "deep_graph_profile": deep_graph_profile(evidence_chain),
                        "extraction": {
                            "prompt_version": extraction["prompt_version"],
                            "extraction_source": extraction.get("extraction_source"),
                            "schema_context": extraction.get("schema_context"),
                            "ontology_candidates": extraction["ontology_candidates"],
                            "quality": extraction["quality"],
                            "review_boundary": "candidate_finding_review",
                            "canonical_ontology_write": False,
                            "formal_graph_write": False,
                        },
                        "recommended_action": "Review the proposed graph path and supporting source evidence.",
                        "writes_canonical": False,
                    },
                    "evidence_refs": [source_ref],
                    "source_url": source_ref,
                    "confidence": finding.get("confidence") or 0.72,
                    "iteration": iteration,
                }
            )
        return elements

    def _approved_identity_index(self, session) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        approved_artifacts = (
            session.query(OntologyArtifact)
            .filter(OntologyArtifact.project_id == self.tenant)
            .filter(OntologyArtifact.status == "approved")
            .all()
        )
        for artifact in approved_artifacts:
            payload = _json_load(artifact.payload_json, {})
            if artifact.artifact_type in {"object", "node", "graph_node"}:
                item = {
                    "element_type": "node",
                    "name": artifact.name,
                    "payload": {
                        "ontology_type": payload.get("graph_label") or payload.get("name") or artifact.name,
                        "label": payload.get("label") or payload.get("name") or artifact.name,
                        "properties": payload,
                    },
                    "evidence_refs": _json_load(artifact.source_refs_json, []),
                }
            elif artifact.artifact_type in {"link", "edge", "graph_edge"}:
                item = {
                    "element_type": "edge",
                    "name": artifact.name,
                    "payload": {
                        "source_type": payload.get("source_node_key") or payload.get("source_object_key") or payload.get("domain"),
                        "target_type": payload.get("target_node_key") or payload.get("target_object_key") or payload.get("range"),
                        "source_label": payload.get("source_label") or payload.get("source_node_key") or payload.get("source_object_key"),
                        "target_label": payload.get("target_label") or payload.get("target_node_key") or payload.get("target_object_key"),
                        "relation": payload.get("graph_edge_name") or payload.get("relation") or payload.get("link_type") or artifact.name,
                        "properties": payload,
                    },
                    "evidence_refs": _json_load(artifact.source_refs_json, []),
                }
            else:
                continue
            identity = _candidate_identity_payload(item)
            index.append(
                {
                    "node_key": artifact.canonical_key,
                    "status": "approved",
                    "source": "approved_ontology_artifact",
                    "identity": identity,
                    "identity_key": _identity_key(self.tenant, identity),
                }
            )
        return index

    def _schema_graph_node_type(self, artifact: OntologyArtifact) -> str:
        return re.sub(r"[^0-9A-Za-z]", "", artifact.name or "") or artifact.canonical_key.split(":", 1)[-1]

    def _source_tables(self) -> set[str]:
        try:
            return set(inspect(self.source_engine).get_table_names())
        except Exception:
            return set()

    def _source_columns(self, table: str) -> set[str]:
        try:
            inspector = inspect(self.source_engine)
            if table not in inspector.get_table_names():
                return set()
            return {column["name"] for column in inspector.get_columns(table)}
        except Exception:
            return set()

    def _safe_source_identifier(self, value: Any) -> str | None:
        value = str(value or "")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            return value
        return None

    def _schema_graph_safe_join_condition(self, join_condition: Any) -> str | None:
        match = re.fullmatch(
            r"\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*",
            str(join_condition or ""),
        )
        if not match:
            return None
        left_table, left_col, right_table, right_col = match.groups()
        source_tables = self._source_tables()
        if left_table not in source_tables or right_table not in source_tables:
            return None
        if left_col not in self._source_columns(left_table) or right_col not in self._source_columns(right_table):
            return None
        return f"{left_table}.{left_col} = {right_table}.{right_col}"

    def _approved_schema_graph_projection_artifacts(self, session) -> tuple[dict[str, OntologyArtifact], list[OntologyArtifact]]:
        rows = (
            session.query(OntologyArtifact)
            .filter(OntologyArtifact.project_id == self.tenant)
            .filter(OntologyArtifact.status == "approved")
            .filter(OntologyArtifact.source_agent == "SchemaGraphModelingAgent")
            .order_by(OntologyArtifact.artifact_type.asc(), OntologyArtifact.canonical_key.asc())
            .all()
        )
        objects: dict[str, OntologyArtifact] = {}
        links: list[OntologyArtifact] = []
        for artifact in rows:
            payload = _json_load(artifact.payload_json, {})
            if payload.get("prompt_version") != "schema_graph_modeling_v1" or payload.get("llm_inferred") is not True:
                continue
            natural_key = artifact.canonical_key.split(":", 1)[1] if ":" in artifact.canonical_key else artifact.canonical_key
            if artifact.artifact_type == "object":
                objects[natural_key] = artifact
            elif artifact.artifact_type == "link":
                links.append(artifact)
        return objects, links

    def _schema_graph_table_and_pk(self, artifact: OntologyArtifact) -> tuple[str | None, str | None]:
        payload = _json_load(artifact.payload_json, {})
        source_tables = self._source_tables()
        table = next((item for item in payload.get("mapped_table_names") or [] if item in source_tables), None)
        pk = payload.get("primary_key")
        table = self._safe_source_identifier(table)
        pk = self._safe_source_identifier(pk)
        if not table or not pk or pk not in self._source_columns(table):
            return None, None
        return table, pk

    def _approved_graph_instance_identity_index(self, session) -> list[dict[str, Any]]:
        objects, links = self._approved_schema_graph_projection_artifacts(session)
        if not objects:
            return []
        limit = max(1, int(os.environ.get("ALETHEIA_APPROVED_IDENTITY_INDEX_LIMIT", "500") or "500"))
        index: list[dict[str, Any]] = []
        object_meta: dict[str, dict[str, Any]] = {}
        try:
            conn_ctx = self.source_engine.connect()
        except Exception:
            return []
        with conn_ctx as conn:
            for natural_key, artifact in objects.items():
                table, pk = self._schema_graph_table_and_pk(artifact)
                if not table or not pk:
                    continue
                type_name = self._schema_graph_node_type(artifact)
                object_meta[natural_key] = {"artifact": artifact, "table": table, "pk": pk, "type": type_name}
                rows = conn.execute(
                    text(
                        f"SELECT DISTINCT {table}.{pk} AS node_pk "
                        f"FROM {table} WHERE {table}.{pk} IS NOT NULL "
                        f"ORDER BY {table}.{pk} LIMIT :limit"
                    ),
                    {"limit": limit},
                ).mappings().all()
                for row in rows:
                    label = str(row["node_pk"])
                    payload = {
                        "ontology_type": type_name,
                        "label": label,
                        "properties": {
                            "source_pk": f"{pk}={label}",
                            "source_table": table,
                            "ontology_artifact": artifact.canonical_key,
                            "projection_source": "SchemaGraphModelingAgent",
                        },
                    }
                    identity = _candidate_identity_payload(
                        {
                            "element_type": "node",
                            "name": label,
                            "payload": payload,
                            "evidence_refs": [f"{table}.{pk}={label}"],
                        }
                    )
                    index.append(
                        {
                            "node_key": f"{type_name}:{label}",
                            "status": "approved",
                            "source": "approved_graph_instance",
                            "identity": identity,
                            "identity_key": _identity_key(self.tenant, identity),
                            "payload": payload,
                        }
                    )

            for link in links:
                payload = _json_load(link.payload_json, {})
                source_meta = object_meta.get(payload.get("source_object_key"))
                target_meta = object_meta.get(payload.get("target_object_key"))
                source_table = self._safe_source_identifier(payload.get("source_table"))
                target_table = self._safe_source_identifier(payload.get("target_table"))
                join_condition = self._schema_graph_safe_join_condition(payload.get("join_condition"))
                if not source_meta or not target_meta or not source_table or not target_table or not join_condition:
                    continue
                source_pk = source_meta["pk"]
                target_pk = target_meta["pk"]
                if source_pk not in self._source_columns(source_table) or target_pk not in self._source_columns(target_table):
                    continue
                rows = conn.execute(
                    text(
                        f"SELECT DISTINCT {source_table}.{source_pk} AS source_pk, "
                        f"{target_table}.{target_pk} AS target_pk "
                        f"FROM {source_table} JOIN {target_table} ON {join_condition} "
                        f"WHERE {source_table}.{source_pk} IS NOT NULL "
                        f"AND {target_table}.{target_pk} IS NOT NULL "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                ).mappings().all()
                relation = payload.get("graph_edge_name") or payload.get("relation") or payload.get("link_type") or link.name
                for row in rows:
                    source_label = str(row["source_pk"])
                    target_label = str(row["target_pk"])
                    edge_payload = {
                        "source_type": source_meta["type"],
                        "source_label": source_label,
                        "target_type": target_meta["type"],
                        "target_label": target_label,
                        "relation": relation,
                        "properties": {
                            "schema_edge_key": link.canonical_key,
                            "source_pk": f"{source_pk}={source_label}",
                            "target_pk": f"{target_pk}={target_label}",
                            "source_table": source_table,
                            "target_table": target_table,
                            "projection_source": "SchemaGraphModelingAgent",
                        },
                    }
                    identity = _candidate_identity_payload(
                        {
                            "element_type": "edge",
                            "name": f"{source_label} {relation} {target_label}",
                            "payload": edge_payload,
                            "evidence_refs": [
                                f"{source_table}.{source_pk}={source_label}",
                                f"{target_table}.{target_pk}={target_label}",
                            ],
                        }
                    )
                    index.append(
                        {
                            "node_key": f"{source_meta['type']}:{source_label}->{target_meta['type']}:{target_label}:{link.canonical_key}",
                            "status": "approved",
                            "source": "approved_graph_instance",
                            "identity": identity,
                            "identity_key": _identity_key(self.tenant, identity),
                            "payload": edge_payload,
                        }
                    )
        return index

    def _proposed_identity_index(self, session) -> list[dict[str, Any]]:
        index: list[dict[str, Any]] = []
        rows = (
            session.query(ProposedGraphElement)
            .filter(ProposedGraphElement.project_id == self.tenant)
            .filter(ProposedGraphElement.element_type.in_(["node", "edge", "finding"]))
            .filter(ProposedGraphElement.status.in_(["draft", "needs_review", "needs_more_evidence", "approved"]))
            .all()
        )
        for row in rows:
            payload = _json_load(row.payload_json, {})
            identity = payload.get("identity")
            if row.element_type in {"node", "edge", "finding"} or not isinstance(identity, dict):
                identity = _candidate_identity_payload(
                    {
                        "element_type": row.element_type,
                        "name": row.name,
                        "payload": payload,
                        "evidence_refs": _json_load(row.evidence_refs_json, []),
                        "source_url": row.source_url,
                    }
                )
            node_key = row.element_key
            status = "approved" if row.status == "approved" else "proposed"
            source = "proposed_graph"
            if (
                row.element_type == "node"
                and payload.get("dedup_decision") == "merge_existing"
                and payload.get("matched_node_key")
                and (
                    payload.get("matched_status") == "approved"
                    or payload.get("matched_source") in {"approved_graph_instance", "approved_graph", "approved_graph_projection"}
                )
            ):
                node_key = payload.get("matched_node_key")
                status = "approved"
                source = payload.get("matched_source") or "approved_graph_instance"
            index.append(
                {
                    "node_key": node_key,
                    "status": status,
                    "source": source,
                    "identity": identity,
                    "identity_key": _identity_key(self.tenant, identity),
                    "payload": payload,
                }
            )
        return index

    def _embedding_for_dedup_text(self, dedup_text: str) -> dict[str, Any]:
        result = self.embedding_adapter.embed(dedup_text)
        vector = result.get("vector")
        normalized = _normalize_vector(vector) if isinstance(vector, list) else None
        status = result.get("status") or ("ready" if normalized else "degraded")
        return {
            "status": status,
            "reason": result.get("reason"),
            "detail": result.get("detail"),
            "model": result.get("model") or getattr(self.embedding_adapter, "model_name", DEFAULT_DEDUP_EMBEDDING_MODEL),
            "vector": normalized,
            "dim": len(normalized or []),
            "fingerprint": _digest(
                {
                    "model": result.get("model") or getattr(self.embedding_adapter, "model_name", DEFAULT_DEDUP_EMBEDDING_MODEL),
                    "dedup_text": dedup_text,
                    "dim": len(normalized or []),
                    "vector": [round(value, 6) for value in (normalized or [])[:8]],
                },
                32,
            )
            if normalized
            else None,
        }

    def _identity_entry_from_index_row(self, row: GraphIdentityIndex) -> dict[str, Any]:
        identity = _json_load(row.identity_json, {})
        return {
            "node_key": row.source_key,
            "status": "approved" if row.source_status == "approved" else "proposed",
            "source": row.source_space,
            "identity": identity,
            "identity_key": row.identity_key,
            "candidate_id": row.candidate_id,
            "payload_fingerprint": row.payload_fingerprint,
            "dedup_text": row.dedup_text,
            "embedding_model": row.embedding_model,
            "embedding_dim": row.embedding_dim,
            "embedding": _json_load(row.embedding_json, None),
            "vector_fingerprint": row.vector_fingerprint,
        }

    def _persistent_identity_index(self, session) -> list[dict[str, Any]]:
        rows = (
            session.query(GraphIdentityIndex)
            .filter(GraphIdentityIndex.project_id == self.tenant)
            .filter(GraphIdentityIndex.element_kind.in_(["node", "edge", "finding"]))
            .order_by(GraphIdentityIndex.source_space.asc(), GraphIdentityIndex.source_key.asc())
            .all()
        )
        return [self._identity_entry_from_index_row(row) for row in rows]

    def _persistent_index_missing_proposed_graph_sources(self, session, index: list[dict[str, Any]]) -> bool:
        indexed_source_keys = {
            entry.get("node_key")
            for entry in index
            if entry.get("source") == "proposed_graph" and entry.get("node_key")
        }
        rows = (
            session.query(ProposedGraphElement.element_key)
            .filter(ProposedGraphElement.project_id == self.tenant)
            .filter(ProposedGraphElement.element_type.in_(["node", "edge", "finding"]))
            .filter(ProposedGraphElement.status.in_(["draft", "needs_review", "needs_more_evidence", "approved"]))
            .all()
        )
        return any(row.element_key not in indexed_source_keys for row in rows)

    def _upsert_identity_index_row(
        self,
        session,
        *,
        identity: dict[str, Any],
        identity_key: str | None,
        source_space: str,
        source_key: str,
        source_status: str,
        evidence_refs: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> GraphIdentityIndex:
        identity_key = identity_key or _identity_key(self.tenant, identity)
        dedup_text = _dedup_text_for_identity(identity, payload)
        embedding = self._embedding_for_dedup_text(dedup_text)
        row = (
            session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant, source_space=source_space, identity_key=identity_key)
            .first()
        )
        if not row:
            row = (
                session.query(GraphIdentityIndex)
                .filter_by(project_id=self.tenant, source_space=source_space, source_key=source_key)
                .first()
            )
        if not row:
            row = GraphIdentityIndex(project_id=self.tenant, source_space=source_space, source_key=source_key)
            session.add(row)
        row.identity_key = identity_key
        row.element_kind = str(identity.get("kind") or "unknown")
        row.candidate_id = _candidate_id_for_identity(self.tenant, identity)
        row.source_space = source_space
        row.source_key = source_key
        row.source_status = source_status
        row.match_label = identity.get("normalized_label") or identity.get("source_node")
        row.match_relation = identity.get("relation")
        row.identity_json = _json_dump(identity)
        row.evidence_refs_json = _json_dump(evidence_refs or [])
        row.dedup_text = dedup_text
        row.embedding_model = embedding.get("model")
        row.embedding_dim = embedding.get("dim") or 0
        row.embedding_json = _json_dump(embedding.get("vector")) if embedding.get("vector") else None
        row.vector_fingerprint = embedding.get("fingerprint")
        row.payload_fingerprint = _digest(
            {
                "identity_key": identity_key,
                "source_space": source_space,
                "source_key": source_key,
                "source_status": source_status,
                "evidence_refs": evidence_refs or [],
                "dedup_text": dedup_text,
                "vector_fingerprint": row.vector_fingerprint,
            },
            32,
        )
        row.updated_at = datetime.utcnow()
        return row

    def _rebuild_persistent_identity_index(self, session) -> list[dict[str, Any]]:
        session.query(GraphIdentityIndex).filter(GraphIdentityIndex.project_id == self.tenant).delete(
            synchronize_session=False
        )
        source_entries = (
            self._approved_identity_index(session)
            + self._approved_graph_instance_identity_index(session)
            + self._proposed_identity_index(session)
        )
        for entry in source_entries:
            identity = entry.get("identity")
            if not isinstance(identity, dict):
                continue
            self._upsert_identity_index_row(
                session,
                identity=identity,
                identity_key=entry.get("identity_key"),
                source_space=entry.get("source") or "unknown",
                source_key=entry.get("node_key") or entry.get("identity_key") or "unknown",
                source_status=entry.get("status") or "draft",
                evidence_refs=[],
                payload=entry.get("payload") if isinstance(entry.get("payload"), dict) else None,
            )
        session.flush()
        return self._persistent_identity_index(session)

    def _identity_index(self, session) -> list[dict[str, Any]]:
        index = self._persistent_identity_index(session)
        if index:
            approved_objects, _approved_links = self._approved_schema_graph_projection_artifacts(session)
            if approved_objects and not any(entry.get("source") == "approved_graph_instance" for entry in index):
                if self._approved_graph_instance_identity_index(session):
                    return self._rebuild_persistent_identity_index(session)
            if any(
                entry.get("identity", {}).get("kind") == "edge"
                and "://" in str(entry.get("identity_key") or "")
                for entry in index
            ):
                return self._rebuild_persistent_identity_index(session)
            if self._persistent_index_missing_proposed_graph_sources(session, index):
                return self._rebuild_persistent_identity_index(session)
            return index
        return self._rebuild_persistent_identity_index(session)

    def rebuild_identity_index(self) -> dict[str, Any]:
        session = self.Session()
        try:
            index = self._rebuild_persistent_identity_index(session)
            session.commit()
            return {
                "tenant": self.tenant,
                "identity_index_count": len(index),
                "identity_index": index,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _approved_proposed_node_aliases(self, session) -> dict[str, str]:
        aliases: dict[str, str] = {}
        rows = (
            session.query(ProposedGraphElement)
            .filter(ProposedGraphElement.project_id == self.tenant)
            .filter(ProposedGraphElement.element_type == "node")
            .all()
        )
        for row in rows:
            payload = _json_load(row.payload_json, {})
            matched_key = str(payload.get("matched_node_key") or "").strip()
            if not matched_key:
                continue
            if not (
                payload.get("matched_status") == "approved"
                or payload.get("matched_source") in {"approved_graph_instance", "approved_graph", "approved_graph_projection"}
            ):
                continue
            aliases[row.element_key] = matched_key
            identity_key = str(payload.get("identity_key") or "").strip()
            if identity_key:
                aliases[identity_key] = matched_key
        return aliases

    def _canonical_endpoint_for_cleanup(
        self,
        payload: dict[str, Any],
        role: str,
        aliases: dict[str, str],
    ) -> tuple[str, str]:
        evidence = payload.get("endpoint_dedup_evidence") if isinstance(payload.get("endpoint_dedup_evidence"), dict) else {}
        endpoint = evidence.get(role) if isinstance(evidence.get(role), dict) else {}
        matched_key = str(endpoint.get("matched_node_key") or endpoint.get("candidate_key") or "").strip()
        matched_space = str(endpoint.get("matched_space") or endpoint.get("matched_source") or "").strip()
        if matched_key in aliases:
            return _normalize_identity_text(aliases[matched_key]), "approved_alias"
        if matched_space in {"approved_graph", "approved_graph_instance", "approved_graph_projection"} and matched_key:
            return _normalize_identity_text(matched_key), "approved_direct"
        identity_key = str(endpoint.get("identity_key") or "").strip()
        if identity_key in aliases:
            return _normalize_identity_text(aliases[identity_key]), "approved_alias"
        label_key = "source_label" if role == "source" else "target_label"
        return _normalize_identity_text(payload.get(label_key)), "label"

    def _canonical_edge_cleanup_key(
        self,
        payload: dict[str, Any],
        aliases: dict[str, str],
    ) -> tuple[tuple[str, str, str, str, str], dict[str, Any]] | tuple[None, dict[str, Any]]:
        source, source_basis = self._canonical_endpoint_for_cleanup(payload, "source", aliases)
        target, target_basis = self._canonical_endpoint_for_cleanup(payload, "target", aliases)
        relation = _normalize_identity_text(payload.get("relation") or payload.get("relation_label") or payload.get("graph_edge_name"))
        source_type = _normalize_identity_text(payload.get("source_type"))
        target_type = _normalize_identity_text(payload.get("target_type"))
        trace = {
            "source": source,
            "target": target,
            "relation": relation,
            "source_type": source_type,
            "target_type": target_type,
            "source_basis": source_basis,
            "target_basis": target_basis,
        }
        if not all([source, target, relation, source_type, target_type]):
            return None, trace
        if source_basis not in {"approved_direct", "approved_alias"} or target_basis not in {"approved_direct", "approved_alias"}:
            return None, trace
        return (source_type, source, relation, target_type, target), trace

    def _duplicate_edge_rank(self, row: ProposedGraphElement, payload: dict[str, Any], trace: dict[str, Any]) -> tuple[int, int, float, int]:
        direct_endpoint_count = sum(
            1 for basis in (trace.get("source_basis"), trace.get("target_basis")) if basis == "approved_direct"
        )
        status_rank = {"approved": 4, "draft": 3, "needs_review": 2, "needs_more_evidence": 1}.get(row.status, 0)
        confidence = float(row.confidence or payload.get("candidate_confidence") or 0.0)
        return (direct_endpoint_count, status_rank, confidence, -int(row.id or 0))

    def _cleanup_duplicate_proposed_edges(self, session, *, reviewer: str = "Continuous Enrichment Agent") -> dict[str, Any]:
        aliases = self._approved_proposed_node_aliases(session)
        pending_statuses = {"draft", "needs_review", "needs_more_evidence"}
        rows = (
            session.query(ProposedGraphElement)
            .filter(ProposedGraphElement.project_id == self.tenant)
            .filter(ProposedGraphElement.element_type == "edge")
            .filter(ProposedGraphElement.status.in_(sorted(pending_statuses)))
            .all()
        )
        groups: dict[tuple[str, str, str, str, str], list[tuple[ProposedGraphElement, dict[str, Any], dict[str, Any]]]] = {}
        skipped = []
        for row in rows:
            payload = _json_load(row.payload_json, {})
            group_key, trace = self._canonical_edge_cleanup_key(payload, aliases)
            if not group_key:
                skipped.append({"element_key": row.element_key, "reason": "no_approved_canonical_edge_key", "trace": trace})
                continue
            groups.setdefault(group_key, []).append((row, payload, trace))

        reviewed = []
        for group_key, members in groups.items():
            if len(members) < 2:
                continue
            winner, winner_payload, winner_trace = max(
                members,
                key=lambda item: self._duplicate_edge_rank(item[0], item[1], item[2]),
            )
            winner_payload = {**winner_payload}
            winner_payload["canonical_edge_cleanup"] = {
                "group_key": list(group_key),
                "retained": True,
                "duplicate_count": len(members) - 1,
                "trace": winner_trace,
            }
            winner.payload_json = _json_dump(winner_payload)
            for row, payload, trace in members:
                if row.element_key == winner.element_key:
                    continue
                payload = {**payload}
                payload["dedup_decision"] = "duplicate_existing_proposal"
                payload["matched_node_key"] = winner.element_key
                payload["matched_edge_key"] = winner.element_key
                payload["matched_status"] = "proposed"
                payload["matched_source"] = "proposed_graph"
                payload["review_required"] = False
                payload["canonical_edge_cleanup"] = {
                    "group_key": list(group_key),
                    "retained": False,
                    "duplicate_of": winner.element_key,
                    "reviewer": reviewer,
                    "reason": "same approved-canonical source, relation, and target edge fact",
                    "trace": trace,
                }
                row.payload_json = _json_dump(payload)
                row.status = "rejected"
                reviewed.append(
                    {
                        "element_key": row.element_key,
                        "duplicate_of": winner.element_key,
                        "group_key": list(group_key),
                    }
                )
        session.flush()
        return {"tenant": self.tenant, "reviewed": reviewed, "skipped": skipped, "group_count": len(groups)}

    def cleanup_duplicate_proposed_edges(self) -> dict[str, Any]:
        session = self.Session()
        try:
            result = self._cleanup_duplicate_proposed_edges(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def identity_index_snapshot(self, limit: int = 200) -> dict[str, Any]:
        session = self.Session()
        try:
            total = session.query(GraphIdentityIndex).filter(GraphIdentityIndex.project_id == self.tenant).count()
            rows = (
                session.query(GraphIdentityIndex)
                .filter(GraphIdentityIndex.project_id == self.tenant)
                .order_by(GraphIdentityIndex.source_space.asc(), GraphIdentityIndex.source_key.asc())
                .limit(limit)
                .all()
            )
            return {
                "tenant": self.tenant,
                "identity_index_count": total,
                "identity_index": [
                    {
                        "identity_key": row.identity_key,
                        "element_kind": row.element_kind,
                        "candidate_id": row.candidate_id,
                        "source_space": row.source_space,
                        "source_key": row.source_key,
                        "source_status": row.source_status,
                        "match_label": row.match_label,
                        "match_relation": row.match_relation,
                        "dedup_text": row.dedup_text,
                        "embedding_model": row.embedding_model,
                        "embedding_dim": row.embedding_dim,
                        "vector_fingerprint": row.vector_fingerprint,
                        "identity": _json_load(row.identity_json, {}),
                    }
                    for row in rows
                ],
            }
        finally:
            session.close()

    def _best_identity_match(
        self,
        identity: dict[str, Any],
        index: list[dict[str, Any]],
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        candidate_identity_key = _identity_key(self.tenant, identity)
        for entry in index:
            if entry.get("identity_key") == candidate_identity_key:
                return {
                    "score": 1.0,
                    "evidence": ["same stable identity key"],
                    "conflict_fields": [],
                    "matched_node_key": entry["node_key"],
                    "matched_status": entry["status"],
                    "matched_source": entry["source"],
                    "identity_key": entry["identity_key"],
                    "match_method": "stable_identity_key",
                    "decision_reason": "exact stable graph identity match",
                    "vector_top_k": [],
                    "vector_distance": 0.0,
                }

        dedup_text = _dedup_text_for_identity(identity, payload)
        candidate_confidence = _float_or_none((payload or {}).get("_candidate_confidence") or (payload or {}).get("confidence"))
        candidate_embedding = self._embedding_for_dedup_text(dedup_text)
        if not candidate_embedding.get("vector"):
            alias_candidates = _short_alias_possible_duplicates(
                identity,
                index,
                candidate_dedup_text=dedup_text,
            )
            if alias_candidates:
                best_alias = alias_candidates[0]
                evidence = [
                    "embedding unavailable; exact identity missed",
                    "short label/alias conflict found in existing identity index",
                ]
                evidence.extend(best_alias.get("evidence") or [])
                return {
                    "score": best_alias.get("score") or 0.0,
                    "evidence": evidence,
                    "conflict_fields": [],
                    "matched_node_key": best_alias.get("node_key"),
                    "matched_status": best_alias.get("status"),
                    "matched_source": best_alias.get("source"),
                    "identity_key": best_alias.get("identity_key"),
                    "match_method": "embedding_degraded_alias_scan",
                    "decision_reason": "possible_duplicate_alias_conflict_embedding_degraded",
                    "embedding_model": candidate_embedding.get("model"),
                    "embedding_status": candidate_embedding.get("status"),
                    "embedding_degraded": True,
                    "embedding_degraded_reason": candidate_embedding.get("reason"),
                    "language_hint": _language_hint(dedup_text),
                    "dedup_text": dedup_text,
                    "text_similarity": best_alias.get("text_similarity") or 0.0,
                    "possible_duplicate": True,
                    "possible_duplicate_candidates": alias_candidates,
                    "vector_top_k": alias_candidates,
                }
            return {
                "score": 0.0,
                "evidence": ["embedding unavailable; no rule/lexical fallback used"],
                "conflict_fields": [],
                "matched_node_key": None,
                "matched_status": None,
                "matched_source": None,
                "identity_key": None,
                "match_method": "embedding_degraded",
                "decision_reason": "embedding model unavailable, so non-exact dedup is degraded",
                "embedding_model": candidate_embedding.get("model"),
                "embedding_status": candidate_embedding.get("status"),
                "embedding_degraded": True,
                "embedding_degraded_reason": candidate_embedding.get("reason"),
                "language_hint": _language_hint(dedup_text),
                "dedup_text": dedup_text,
                "vector_top_k": [],
            }

        candidates: list[dict[str, Any]] = []
        candidate_vector = candidate_embedding.get("vector")
        candidate_language = _language_hint(dedup_text)
        for entry in index:
            if identity.get("kind") != (entry.get("identity") or {}).get("kind"):
                continue
            if identity.get("kind") == "node" and entry.get("source") == "approved_ontology_artifact":
                continue
            existing_vector = entry.get("embedding")
            distance = _cosine_distance(candidate_vector, existing_vector if isinstance(existing_vector, list) else None)
            if distance is None:
                continue
            structure = _structure_compatibility(identity, entry.get("identity") or {})
            text_similarity = 0.0
            existing_text = entry.get("dedup_text") or ""
            existing_language = _language_hint(existing_text)
            if candidate_language == existing_language and candidate_language not in {"unknown", "mixed_cjk_latin"}:
                text_similarity = round(SequenceMatcher(None, dedup_text.lower(), existing_text.lower()).ratio(), 4)
            candidates.append(
                {
                    "node_key": entry["node_key"],
                    "status": entry["status"],
                    "source": entry["source"],
                    "identity_key": entry["identity_key"],
                    "distance": distance,
                    "score": _vector_score(distance),
                    "text_similarity": text_similarity,
                    "language_hint": candidate_language,
                    "embedding_model": candidate_embedding.get("model"),
                    "structure_compatible": structure["compatible"],
                    "conflict_fields": structure["conflict_fields"],
                    "structure_evidence": structure["evidence"],
                }
            )
        source_priority = {
            "approved_graph_instance": 0,
            "proposed_graph": 1,
            "approved_ontology_artifact": 2,
        }
        top_k = sorted(
            candidates,
            key=lambda item: (
                item["distance"],
                source_priority.get(str(item.get("source") or ""), 9),
                -item["text_similarity"],
            ),
        )[:VECTOR_TOP_K]
        if not top_k:
            return {
                "score": 0.0,
                "evidence": ["no comparable embedding vectors in identity index"],
                "conflict_fields": [],
                "match_method": "vector_embedding",
                "decision_reason": "no vector neighbors available",
                "embedding_model": candidate_embedding.get("model"),
                "embedding_status": candidate_embedding.get("status"),
                "language_hint": candidate_language,
                "dedup_text": dedup_text,
                "vector_top_k": [],
            }

        best = top_k[0]
        if _dedup_decision(
            {
                "match_method": "vector_embedding",
                "structure_compatible": best.get("structure_compatible", False),
                "vector_distance": best["distance"],
                "score": best["score"],
                "candidate_kind": identity.get("kind"),
                "candidate_confidence": candidate_confidence,
                "node_similarity_dedup_threshold": self.node_similarity_dedup_threshold,
                "matched_source": best["source"],
                "matched_status": best["status"],
            }
        ) == "new_proposal":
            alias_candidates = _short_alias_possible_duplicates(
                identity,
                index,
                candidate_dedup_text=dedup_text,
            )
            if alias_candidates:
                best_alias = alias_candidates[0]
                evidence = [
                    "vector nearest-neighbor outside dedup threshold",
                    "short label/alias conflict found in existing identity index",
                ]
                evidence.extend(best_alias.get("evidence") or [])
                return {
                    "score": best_alias.get("score") or 0.0,
                    "evidence": evidence,
                    "conflict_fields": [],
                    "matched_node_key": best_alias.get("node_key"),
                    "matched_status": best_alias.get("status"),
                    "matched_source": best_alias.get("source"),
                    "identity_key": best_alias.get("identity_key"),
                    "match_method": "short_alias_review_gate",
                    "decision_reason": "possible_duplicate_alias_conflict",
                    "embedding_model": candidate_embedding.get("model"),
                    "embedding_status": candidate_embedding.get("status"),
                    "embedding_degraded": False,
                    "structure_compatible": True,
                    "language_hint": candidate_language,
                    "dedup_text": dedup_text,
                    "text_similarity": best_alias.get("text_similarity") or 0.0,
                    "possible_duplicate": True,
                    "possible_duplicate_candidates": alias_candidates,
                    "vector_distance": best["distance"],
                    "vector_top_k": top_k,
                }
        evidence = [
            "vector nearest-neighbor search",
            f"cosine distance {best['distance']:.4f}",
        ]
        evidence.extend(best.get("structure_evidence") or [])
        if best.get("text_similarity"):
            evidence.append(f"same-language text similarity {best['text_similarity']:.4f}")
        structural_conflict = not best.get("structure_compatible", False)
        node_direct_dedup = (
            identity.get("kind") == "node"
            and not structural_conflict
            and (candidate_confidence or 0.0) > NODE_DIRECT_DEDUP_MIN_CONFIDENCE
            and float(best.get("score") or 0.0) >= self.node_similarity_dedup_threshold
        )
        if node_direct_dedup:
            evidence.append(
                f"node similarity {best['score']:.4f} >= threshold {self.node_similarity_dedup_threshold:.4f} with confidence {candidate_confidence:.4f}"
            )
        return {
            "score": best["score"],
            "evidence": evidence,
            "conflict_fields": best.get("conflict_fields") or [],
            "matched_node_key": best["node_key"],
            "matched_status": best["status"],
            "matched_source": best["source"],
            "identity_key": best["identity_key"],
            "match_method": "vector_embedding",
            "decision_reason": "structural_conflict"
            if structural_conflict
            else "node_similarity_confidence_threshold_met"
            if node_direct_dedup
            else "vector distance within review window"
            if best["distance"] <= VECTOR_REVIEW_DISTANCE
            else "nearest vector outside dedup threshold",
            "embedding_model": candidate_embedding.get("model"),
            "embedding_status": candidate_embedding.get("status"),
            "embedding_degraded": False,
            "structure_compatible": best.get("structure_compatible", False),
            "language_hint": candidate_language,
            "dedup_text": dedup_text,
            "vector_distance": best["distance"],
            "text_similarity": best.get("text_similarity") or 0.0,
            "vector_top_k": top_k,
            "candidate_kind": identity.get("kind"),
            "candidate_confidence": candidate_confidence,
            "node_similarity_dedup_threshold": self.node_similarity_dedup_threshold,
        }

    def _annotate_candidate_identity(
        self,
        item: dict[str, Any],
        *,
        task_id: str,
        run_id: str,
        frontier_id: str,
        candidate_seq: int,
        identity_index: list[dict[str, Any]],
    ) -> dict[str, Any]:
        identity = _candidate_identity_payload(item)
        candidate_id = _candidate_id_for_identity(self.tenant, identity)
        item_payload = item.get("payload") or {}
        match_payload = {**item_payload, "_candidate_confidence": item.get("confidence")}
        best = self._best_identity_match(identity, identity_index, payload=match_payload)
        decision = _dedup_decision(best)
        match_score = float(best.get("score") or 0.0) if best else 0.0
        existing_review_required = bool(item_payload.get("review_required") or item_payload.get("review_status") == "needs_review")
        existing_review_required = existing_review_required or item.get("status") == "needs_more_evidence"
        review_required = existing_review_required or decision == "needs_review"
        merge_decision_source = (
            "stable_identity_key"
            if best and best.get("match_method") == "stable_identity_key"
            else "vector_embedding_distance"
            if best and best.get("match_method") == "vector_embedding"
            else "embedding_unavailable_degraded"
            if best and best.get("match_method") in {"embedding_degraded", "embedding_degraded_alias_scan"}
            else "no_identity_match"
        )
        dedup = {
            "task_id": task_id,
            "run_id": run_id,
            "frontier_id": frontier_id,
            "candidate_id": candidate_id,
            "candidate_seq": candidate_seq,
            "source_fingerprint": _digest({"source_url": item.get("source_url"), "evidence_refs": item.get("evidence_refs") or []}, 16),
            "evidence_fingerprint": _digest(
                {
                    "evidence_refs": item.get("evidence_refs") or [],
                    "evidence_quote": (item.get("payload") or {}).get("evidence_quote"),
                },
                16,
            ),
            "dedup_decision": decision,
            "matched_node_key": best.get("matched_node_key") if best else None,
            "matched_status": best.get("matched_status") if best else None,
            "matched_source": best.get("matched_source") if best else None,
            "match_score": match_score,
            "match_evidence": best.get("evidence", []) if best else [],
            "conflict_fields": best.get("conflict_fields", []) if best else [],
            "match_method": best.get("match_method") if best else None,
            "embedding_model": best.get("embedding_model") if best else None,
            "embedding_status": best.get("embedding_status") if best else None,
            "embedding_degraded": bool(best.get("embedding_degraded")) if best else False,
            "embedding_degraded_reason": best.get("embedding_degraded_reason") if best else None,
            "structure_compatible": best.get("structure_compatible") if best else None,
            "possible_duplicate": bool(best.get("possible_duplicate")) if best else False,
            "possible_duplicate_candidates": best.get("possible_duplicate_candidates", []) if best else [],
            "vector_distance": best.get("vector_distance") if best else None,
            "vector_top_k": best.get("vector_top_k", []) if best else [],
            "text_similarity": best.get("text_similarity") if best else None,
            "language_hint": best.get("language_hint") if best else _language_hint(_dedup_text_for_identity(identity, item_payload)),
            "decision_reason": best.get("decision_reason") if best else "no exact or vector identity match",
            "vector_duplicate_distance_threshold": VECTOR_DUPLICATE_DISTANCE,
            "vector_review_distance_threshold": VECTOR_REVIEW_DISTANCE,
            "node_similarity_dedup_threshold": best.get("node_similarity_dedup_threshold") if best else self.node_similarity_dedup_threshold,
            "node_direct_dedup_min_confidence": NODE_DIRECT_DEDUP_MIN_CONFIDENCE,
            "candidate_confidence": best.get("candidate_confidence") if best else item.get("confidence"),
            "review_required": review_required,
            "merge_decision_source": merge_decision_source,
            "llm_merge_decision_allowed": False,
            "audit_context": {
                "task_id": task_id,
                "run_id": run_id,
                "frontier_id": frontier_id,
                "source_url": item.get("source_url"),
                "evidence_refs": item.get("evidence_refs") or [],
                "description": (item.get("payload") or {}).get("description"),
                "confidence": item.get("confidence"),
            },
        }
        item = {**item}
        item["candidate_id"] = candidate_id
        item["identity_key"] = _identity_key(self.tenant, identity)
        item["dedup_decision"] = decision
        item["status"] = "needs_more_evidence" if review_required else "draft"
        payload = {**(item.get("payload") or {})}
        payload.update(dedup)
        payload["identity"] = identity
        payload["identity_key"] = item["identity_key"]
        extraction = payload.get("extraction")
        if isinstance(extraction, dict):
            extraction = {**extraction}
            extraction["identity_dedup"] = {
                "approved_graph_checked": True,
                "proposed_graph_checked": True,
                "current_run_candidates_checked": True,
                "vector_identity_index_checked": True,
                "embedding_model": dedup.get("embedding_model"),
                "vector_duplicate_distance_threshold": VECTOR_DUPLICATE_DISTANCE,
                "vector_review_distance_threshold": VECTOR_REVIEW_DISTANCE,
                "auto_merge_threshold": 0.92,
                "review_threshold": 0.75,
                "canonical_ontology_write": False,
                "formal_graph_write": False,
            }
            payload["extraction"] = extraction
        item["payload"] = payload
        return item

    def _endpoint_lookup_key(self, node_type: Any, label: Any) -> str:
        return f"{_normalize_identity_text(node_type)}::{_normalize_identity_text(label)}"

    def _endpoint_evidence_from_node_candidate(self, item: dict[str, Any], *, proposed_node_created: bool) -> dict[str, Any]:
        payload = item.get("payload") or {}
        matched_key = payload.get("matched_node_key") or payload.get("matched_edge_key") or payload.get("matched_element_key")
        matched_source = payload.get("matched_source")
        matched_status = payload.get("matched_status")
        dedup_decision = payload.get("dedup_decision") or item.get("dedup_decision")
        review_required = bool(payload.get("review_required")) or item.get("status") == "needs_more_evidence"
        return {
            "label": payload.get("label") or item.get("name"),
            "type": payload.get("ontology_type") or payload.get("type"),
            "candidate_id": payload.get("candidate_id") or item.get("candidate_id"),
            "candidate_key": item.get("element_key"),
            "identity_key": payload.get("identity_key") or item.get("identity_key"),
            "dedup_decision": dedup_decision,
            "matched_node_key": matched_key,
            "matched_status": matched_status,
            "matched_source": matched_source,
            "matched_space": "approved_graph"
            if matched_status == "approved" or matched_source == "approved_ontology_artifact"
            else "proposed_graph"
            if matched_source in {"proposed_graph", "current_run_candidate"} or matched_status == "proposed"
            else None,
            "match_score": payload.get("match_score"),
            "match_method": payload.get("match_method"),
            "match_evidence": payload.get("match_evidence") or [],
            "conflict_fields": payload.get("conflict_fields") or [],
            "decision_reason": payload.get("decision_reason"),
            "review_required": review_required,
            "proposed_node_created": proposed_node_created,
        }

    def _attach_endpoint_dedup_evidence(
        self,
        edge_candidate: dict[str, Any],
        endpoint_evidence: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {**(edge_candidate.get("payload") or {})}
        endpoint_payload: dict[str, Any] = {}
        for role, type_key, label_key in (
            ("source", "source_type", "source_label"),
            ("target", "target_type", "target_label"),
        ):
            evidence = endpoint_evidence.get(self._endpoint_lookup_key(payload.get(type_key), payload.get(label_key)))
            if evidence:
                endpoint_payload[role] = {**evidence, "role": role}
        if not endpoint_payload:
            return edge_candidate

        endpoint_review_required = any(bool(evidence.get("review_required")) for evidence in endpoint_payload.values())
        payload["endpoint_dedup_evidence"] = endpoint_payload
        payload["endpoint_review_required"] = endpoint_review_required
        if endpoint_review_required:
            payload["review_required"] = True
            payload["endpoint_decision_reason"] = "endpoint_identity_needs_review"
            edge_candidate = {**edge_candidate, "status": "needs_more_evidence"}
        edge_candidate = {**edge_candidate, "payload": payload}
        return edge_candidate

    def _upsert_element(self, session, run_id: int, item: dict[str, Any]) -> ProposedGraphElement:
        payload = item.get("payload") or {}
        if (
            item.get("element_type") in {"node", "edge"}
            and payload.get("dedup_decision") == "duplicate_existing_proposal"
            and payload.get("matched_node_key")
        ):
            element_key = payload["matched_node_key"]
        else:
            element_key = f"proposed-graph:{self.tenant}:{_slug(item['element_type'])}:{_digest(item.get('identity_key') or item.get('candidate_id') or {'name': item['name'], 'payload': item['payload']})}"
        row = session.query(ProposedGraphElement).filter_by(project_id=self.tenant, element_key=element_key).first()
        if not row:
            row = ProposedGraphElement(run_id=run_id, project_id=self.tenant, element_key=element_key)
            session.add(row)
        row.run_id = run_id
        row.element_type = item["element_type"]
        row.name = item["name"]
        row.payload_json = _json_dump(item.get("payload") or {})
        row.evidence_refs_json = _json_dump(item.get("evidence_refs") or [])
        row.source_url = item.get("source_url")
        row.confidence = item.get("confidence", 0.6)
        row.status = item.get("status") or "draft"
        row.iteration = item.get("iteration", 1)
        return row

    def run(
        self,
        objective: str,
        artifact_keys: list[str] | None = None,
        frontier_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session = self.Session()
        try:
            frontier = list(frontier_items or [])[: self.max_frontier] if frontier_items else self._frontier_from_artifacts(session, artifact_keys)
        except Exception:
            session.close()
            raise
        task_id = _stable_task_id(self.tenant, objective, frontier)
        run_key = f"iterative-graph-run:{self.tenant}:{_digest(task_id, 10)}:{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}:{os.getpid()}"
        run = IterativeGraphEnrichmentRun(
            project_id=self.tenant,
            run_key=run_key,
            objective=objective,
            status="running",
            safety_profile_json=_json_dump(
                {
                    "task_id": task_id,
                    "run_id": run_key,
                    "space": "proposed_graph",
                    "canonical_writes": "disabled",
                    "graph_writes": "disabled",
                    "baseline_writes_to_graph": "disabled",
                    "identity_dedup": {
                        "approved_graph": "checked_before_write",
                        "proposed_graph": "checked_before_write",
                        "current_run_candidates": "checked_before_write",
                        "persistent_identity_index": "enabled",
                        "vector_embedding_dedup": "enabled",
                        "embedding_model": getattr(self.embedding_adapter, "model_name", DEFAULT_DEDUP_EMBEDDING_MODEL),
                        "vector_top_k": VECTOR_TOP_K,
                        "vector_duplicate_distance_threshold": VECTOR_DUPLICATE_DISTANCE,
                        "vector_review_distance_threshold": VECTOR_REVIEW_DISTANCE,
                        "node_similarity_direct_dedup_threshold": self.node_similarity_dedup_threshold,
                        "node_direct_dedup_min_confidence": NODE_DIRECT_DEDUP_MIN_CONFIDENCE,
                        "text_similarity": "same_language_audit_only",
                        "auto_merge_threshold": 0.92,
                        "review_threshold": 0.75,
                        "llm_merge_decision": "disabled",
                    },
                    "allowed_domains": sorted(self.allowed_domains),
                    "allow_discovered_domains": self.allow_discovered_domains,
                    "private_or_sensitive_url_policy": "skip_and_audit",
                }
            ),
            budget_json=_json_dump(
                {
                    "max_iterations": self.max_iterations,
                    "max_frontier": self.max_frontier,
                    "max_results_per_query": self.max_results_per_query,
                }
            ),
        )
        session.add(run)
        session.flush()
        try:
            identity_index = self._identity_index(session)
            trace = []
            skipped_sources = []
            next_frontier = list(frontier)
            proposed_count = 0
            pruned_count = 0
            finding_count = 0
            skipped_duplicates = []
            candidate_seq = 0
            for iteration in range(1, self.max_iterations + 1):
                current_frontier = next_frontier[: self.max_frontier]
                next_frontier = []
                for item in current_frontier:
                    frontier_id = str(item.get("key") or item.get("name") or f"frontier:{iteration}")
                    query_plan = self._query_plan_for_frontier(item, objective)
                    query = query_plan["query"]
                    results = self.provider.search(query, self.max_results_per_query)
                    extracted_keys = []
                    pruned = []
                    for result in results:
                        if not result.url or not _is_public_web_url(result.url):
                            reason = "blocked_non_public_or_sensitive_url"
                            skipped_sources.append({
                                "iteration": iteration,
                                "frontier_key": item.get("key"),
                                "url": result.url,
                                "reason": reason,
                                "search_query": query,
                                "query_terms": query_plan["query_terms"],
                                "selected_query_plan": query_plan.get("selected_plan"),
                            })
                            pruned.append({"url": result.url, "reason": reason})
                            pruned_count += 1
                            continue
                        allowed, blocked_reason = _is_crawl_allowed(result.url, self.allowed_domains, self.allow_discovered_domains)
                        if not allowed:
                            skipped_sources.append({
                                "iteration": iteration,
                                "frontier_key": item.get("key"),
                                "url": result.url,
                                "reason": blocked_reason,
                                "search_query": query,
                                "query_terms": query_plan["query_terms"],
                                "selected_query_plan": query_plan.get("selected_plan"),
                            })
                            pruned.append({"url": result.url, "reason": blocked_reason})
                            pruned_count += 1
                            continue
                        summary = _clean_text(result.snippet or result.title, 700)
                        extraction_profile = self._extract_graph_evidence_contract(session, item, result, summary)
                        candidates = self._candidate_elements(extraction_profile, item, result, summary, iteration)
                        if not candidates:
                            pruned.append({"url": result.url, "reason": "no_graph_candidate_extracted"})
                            pruned_count += 1
                            continue
                        annotated_candidates = []
                        endpoint_evidence: dict[str, dict[str, Any]] = {}
                        for candidate in candidates:
                            candidate_seq += 1
                            if candidate.get("element_type") == "edge":
                                candidate = {**candidate, "_candidate_seq": candidate_seq}
                                annotated_candidates.append(candidate)
                                continue

                            candidate = self._annotate_candidate_identity(
                                candidate,
                                task_id=task_id,
                                run_id=run_key,
                                frontier_id=frontier_id,
                                candidate_seq=candidate_seq,
                                identity_index=identity_index,
                            )
                            if candidate["element_type"] == "node":
                                candidate_payload = candidate.get("payload") or {}
                                endpoint_evidence[
                                    self._endpoint_lookup_key(
                                        candidate_payload.get("ontology_type") or candidate_payload.get("type"),
                                        candidate_payload.get("label") or candidate.get("name"),
                                    )
                                ] = self._endpoint_evidence_from_node_candidate(candidate, proposed_node_created=True)
                            annotated_candidates.append(candidate)

                        for candidate in annotated_candidates:
                            if candidate["element_type"] == "edge":
                                candidate = self._attach_endpoint_dedup_evidence(candidate, endpoint_evidence)
                                candidate = self._annotate_candidate_identity(
                                    candidate,
                                    task_id=task_id,
                                    run_id=run_key,
                                    frontier_id=frontier_id,
                                    candidate_seq=int(candidate.get("_candidate_seq") or 0),
                                    identity_index=identity_index,
                                )
                                candidate.pop("_candidate_seq", None)
                            if (
                                candidate["element_type"] in {"node", "edge"}
                                and candidate["dedup_decision"] in {"merge_existing", "duplicate_current_run"}
                                and (
                                    candidate["payload"].get("matched_status") == "approved"
                                    or candidate["payload"].get("matched_source") == "current_run_candidate"
                                )
                            ):
                                if candidate["element_type"] == "node":
                                    endpoint_evidence[
                                        self._endpoint_lookup_key(
                                            candidate["payload"].get("ontology_type") or candidate["payload"].get("type"),
                                            candidate["payload"].get("label") or candidate.get("name"),
                                        )
                                    ] = self._endpoint_evidence_from_node_candidate(candidate, proposed_node_created=False)
                                skipped_duplicates.append(
                                    {
                                        "frontier_id": frontier_id,
                                        "candidate_id": candidate["candidate_id"],
                                        "element_type": candidate["element_type"],
                                        "name": candidate["name"],
                                        "dedup_decision": candidate["dedup_decision"],
                                        "matched_node_key": candidate["payload"].get("matched_node_key"),
                                        "match_score": candidate["payload"].get("match_score"),
                                        "reason": candidate["dedup_decision"],
                                    }
                                )
                                continue
                            if (
                                candidate["element_type"] == "finding"
                                and candidate["dedup_decision"] in {"merge_existing", "duplicate_current_run", "duplicate_existing_proposal"}
                            ):
                                skipped_duplicates.append(
                                    {
                                        "frontier_id": frontier_id,
                                        "candidate_id": candidate["candidate_id"],
                                        "element_type": candidate["element_type"],
                                        "name": candidate["name"],
                                        "dedup_decision": candidate["dedup_decision"],
                                        "matched_node_key": candidate["payload"].get("matched_node_key"),
                                        "match_score": candidate["payload"].get("match_score"),
                                        "reason": "duplicate_finding_not_proposed",
                                    }
                                )
                                continue
                            if (
                                candidate["element_type"] == "node"
                                and candidate["dedup_decision"] == "duplicate_existing_proposal"
                                and candidate["payload"].get("matched_node_key")
                            ):
                                endpoint_evidence[
                                    self._endpoint_lookup_key(
                                        candidate["payload"].get("ontology_type") or candidate["payload"].get("type"),
                                        candidate["payload"].get("label") or candidate.get("name"),
                                    )
                                ] = self._endpoint_evidence_from_node_candidate(candidate, proposed_node_created=False)
                                skipped_duplicates.append(
                                    {
                                        "frontier_id": frontier_id,
                                        "candidate_id": candidate["candidate_id"],
                                        "element_type": candidate["element_type"],
                                        "name": candidate["name"],
                                        "dedup_decision": candidate["dedup_decision"],
                                        "matched_node_key": candidate["payload"].get("matched_node_key"),
                                        "match_score": candidate["payload"].get("match_score"),
                                        "reason": "duplicate_endpoint_node_not_proposed",
                                    }
                                )
                                continue
                            row = self._upsert_element(session, run.id, candidate)
                            candidate = {**candidate, "element_key": row.element_key}
                            if candidate["element_type"] == "node":
                                endpoint_evidence[
                                    self._endpoint_lookup_key(
                                        candidate["payload"].get("ontology_type") or candidate["payload"].get("type"),
                                        candidate["payload"].get("label") or candidate.get("name"),
                                    )
                                ] = self._endpoint_evidence_from_node_candidate(candidate, proposed_node_created=True)
                            extracted_keys.append(row.element_key)
                            proposed_count += 1
                            if candidate["element_type"] == "finding":
                                finding_count += 1
                            if candidate["element_type"] in {"node", "edge", "finding"}:
                                identity = candidate["payload"].get("identity")
                                if isinstance(identity, dict):
                                    index_row = self._upsert_identity_index_row(
                                        session,
                                        identity=identity,
                                        identity_key=candidate.get("identity_key") or _identity_key(self.tenant, identity),
                                        source_space="proposed_graph",
                                        source_key=row.element_key,
                                        source_status=row.status,
                                        evidence_refs=candidate.get("evidence_refs") or [],
                                        payload=candidate.get("payload") or {},
                                    )
                                    identity_index.append(
                                        {
                                            **self._identity_entry_from_index_row(index_row),
                                            "status": "proposed",
                                            "source": "current_run_candidate",
                                        }
                                    )
                            if candidate["element_type"] == "node":
                                next_frontier.append(
                                    {
                                        "key": row.element_key,
                                        "name": candidate["name"],
                                        "artifact_type": "proposed_node",
                                        "source": "proposed_graph",
                                        "depth": iteration,
                                    }
                                )
                    trace.append(
                        {
                            "iteration": iteration,
                            "frontier": item,
                            "query": query,
                            "query_plans": query_plan.get("plans", []),
                            "selected_query_plan": query_plan.get("selected_plan"),
                            "expansion_policy": query_plan.get("expansion_policy"),
                            "relevance_gate": query_plan.get("relevance_gate"),
                            "query_terms": query_plan["query_terms"],
                            "graph_context_used": query_plan["graph_context_used"],
                            "path_context_used": query_plan["path_context_used"],
                            "excluded_terms": query_plan["excluded_terms"],
                            "result_count": len(results),
                            "extracted_candidates": extracted_keys,
                            "skipped_duplicate_candidates": [
                                item for item in skipped_duplicates if item.get("frontier_id") == frontier_id
                            ],
                            "extraction_prompt_version": GRAPH_EXTRACTION_PROMPT_VERSION,
                            "extraction_contract": {
                                "outputs": ["ontology_candidates", "nodes", "edges", "properties", "descriptions", "findings"],
                                "rules": [
                                    "typed entities only",
                                    "typed binary relations with properties",
                                    "evidence quote and source URL required",
                                    "approved graph identity checked before proposed graph write",
                                    "pending proposed graph and current-run candidates checked before proposed graph write",
                                    "ambiguous identity matches require review",
                                    "candidate ontology remains review-gated",
                                    "formal graph writes disabled",
                                ],
                            },
                            "last_extraction_profile": extraction_profile,
                            "pruned": pruned,
                        }
                    )
            run.frontier_json = _json_dump(frontier)
            run.expansion_trace_json = _json_dump(trace)
            run.skipped_sources_json = _json_dump(
                skipped_sources
                + [
                    {
                        "iteration": None,
                        "frontier_key": item["frontier_id"],
                        "url": None,
                        "reason": item["reason"],
                        "candidate_id": item["candidate_id"],
                        "element_type": item.get("element_type"),
                        "name": item.get("name"),
                        "dedup_decision": item["dedup_decision"],
                        "matched_node_key": item["matched_node_key"],
                        "match_score": item["match_score"],
                    }
                    for item in skipped_duplicates
                ]
            )
            run.proposed_count = proposed_count
            run.pruned_count = pruned_count
            run.finding_count = finding_count
            duplicate_cleanup = self._cleanup_duplicate_proposed_edges(session)
            if duplicate_cleanup.get("reviewed"):
                safety_profile = _json_load(run.safety_profile_json, {})
                safety_profile["post_run_duplicate_edge_cleanup"] = duplicate_cleanup
                run.safety_profile_json = _json_dump(safety_profile)
            run.status = "completed"
            run.finished_at = datetime.utcnow()
            session.commit()
            return self.get_run(run_key)
        except Exception as exc:
            session.rollback()
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = datetime.utcnow()
            session.add(run)
            session.commit()
            raise
        finally:
            session.close()

    def get_run(self, run_key: str) -> dict[str, Any]:
        session = self.Session()
        try:
            run = session.query(IterativeGraphEnrichmentRun).filter_by(project_id=self.tenant, run_key=run_key).first()
            if not run:
                raise KeyError(run_key)
            elements = (
                session.query(ProposedGraphElement)
                .filter_by(project_id=self.tenant, run_id=run.id)
                .order_by(ProposedGraphElement.iteration.asc(), ProposedGraphElement.element_type.asc(), ProposedGraphElement.name.asc())
                .all()
            )
            return {
                "tenant": self.tenant,
                "run": {
                    "run_key": run.run_key,
                    "status": run.status,
                    "objective": run.objective,
                    "frontier": _json_load(run.frontier_json, []),
                    "expansion_trace": _json_load(run.expansion_trace_json, []),
                    "safety_profile": _json_load(run.safety_profile_json, {}),
                    "budget": _json_load(run.budget_json, {}),
                    "skipped_sources": _json_load(run.skipped_sources_json, []),
                    "proposed_count": run.proposed_count,
                    "pruned_count": run.pruned_count,
                    "finding_count": run.finding_count,
                },
                "proposed_graph": [self._element_to_dict(item) for item in elements],
            }
        finally:
            session.close()

    def _element_to_dict(self, row: ProposedGraphElement) -> dict[str, Any]:
        return {
            "element_key": row.element_key,
            "element_type": row.element_type,
            "name": row.name,
            "payload": _json_load(row.payload_json, {}),
            "evidence_refs": _json_load(row.evidence_refs_json, []),
            "source_url": row.source_url,
            "confidence": row.confidence,
            "status": row.status,
            "iteration": row.iteration,
        }

    def graph_fingerprint(self) -> dict[str, Any]:
        session = self.Session()
        try:
            artifacts = (
                session.query(OntologyArtifact.artifact_type, OntologyArtifact.status, func.count(OntologyArtifact.id))
                .filter(OntologyArtifact.project_id == self.tenant)
                .group_by(OntologyArtifact.artifact_type, OntologyArtifact.status)
                .order_by(OntologyArtifact.artifact_type, OntologyArtifact.status)
                .all()
            )
            proposed = session.query(ProposedGraphElement).filter_by(project_id=self.tenant).count()
            identity_index_count = session.query(GraphIdentityIndex).filter_by(project_id=self.tenant).count()
            return {
                "ontology_artifacts": [list(row) for row in artifacts],
                "proposed_graph_elements": proposed,
                "graph_identity_index": identity_index_count,
            }
        finally:
            session.close()


class GraphDeepResearchBenchmark:
    def __init__(self, metadata_db_url: str, tenant: str = "default"):
        self.engine = create_engine(metadata_db_url)
        ensure_artifact_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.tenant = tenant

    def compare(self, enrichment_run_key: str, baseline_summary: dict[str, Any], baseline_name: str = "external_deep_research_baseline") -> dict[str, Any]:
        session = self.Session()
        benchmark_key = f"graph-benchmark:{self.tenant}:{_slug(enrichment_run_key)}:{_digest(baseline_summary)}"
        try:
            run = session.query(IterativeGraphEnrichmentRun).filter_by(project_id=self.tenant, run_key=enrichment_run_key).first()
            if not run:
                raise KeyError(enrichment_run_key)
            findings = [
                {
                    "element_key": row.element_key,
                    "title": (_json_load(row.payload_json, {}).get("title") or row.name),
                    "payload": _json_load(row.payload_json, {}),
                    "evidence_refs": _json_load(row.evidence_refs_json, []),
                }
                for row in session.query(ProposedGraphElement)
                .filter_by(project_id=self.tenant, run_id=run.id, element_type="finding")
                .order_by(ProposedGraphElement.confidence.desc())
                .all()
            ]
            comparison = self._score(baseline_summary, findings)
            existing = session.query(GraphDeepResearchBenchmarkRun).filter_by(project_id=self.tenant, benchmark_key=benchmark_key).first()
            if not existing:
                existing = GraphDeepResearchBenchmarkRun(project_id=self.tenant, benchmark_key=benchmark_key)
                session.add(existing)
            existing.enrichment_run_key = enrichment_run_key
            existing.baseline_name = baseline_name
            existing.baseline_summary_json = _json_dump(baseline_summary)
            existing.graph_findings_json = _json_dump(findings)
            existing.comparison_json = _json_dump(comparison)
            existing.status = "completed"
            session.commit()
            return {
                "tenant": self.tenant,
                "benchmark_key": benchmark_key,
                "enrichment_run_key": enrichment_run_key,
                "baseline_name": baseline_name,
                "baseline_summary": baseline_summary,
                "aletheia_graph_findings": findings,
                "comparison": comparison,
                "boundary": {
                    "baseline_is_comparison_artifact_only": True,
                    "baseline_writes_to_proposed_graph": False,
                    "baseline_writes_to_canonical_graph": False,
                },
            }
        finally:
            session.close()

    def _score(self, baseline: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
        baseline_sources = baseline.get("sources") or []
        baseline_claims = baseline.get("claims") or []
        graph_paths = [f["payload"].get("deep_graph_profile", {}) for f in findings]
        complete_paths = [path for path in graph_paths if path.get("multi_hop")]
        graph_evidence_refs = sorted({ref for finding in findings for ref in finding.get("evidence_refs", [])})
        dimensions = {
            "traceability": {
                "aletheia": 1.0 if graph_evidence_refs else 0.0,
                "baseline": 1.0 if baseline_sources else 0.4,
                "difference": "Aletheia keeps field/path-level source refs; baseline depends on cited source list granularity.",
            },
            "multi_hop_path_completeness": {
                "aletheia": len(complete_paths) / max(len(findings), 1),
                "baseline": 0.6 if baseline.get("mentions_multi_hop") else 0.25,
                "difference": "Aletheia requires explicit source -> relation -> target -> evidence -> action paths.",
            },
            "coverage": {
                "aletheia": min(len(findings) / 3, 1.0),
                "baseline": min(len(baseline_claims) / 3, 1.0),
                "difference": "Baseline may summarize broader context; proposed graph covers what has evidence-linked nodes and edges.",
            },
            "hallucination_risk": {
                "aletheia": 0.2 if graph_evidence_refs and complete_paths else 0.5,
                "baseline": 0.5 if baseline_sources else 0.8,
                "lower_is_better": True,
                "difference": "Aletheia drops claims without evidence-chain structure; baseline text is not accepted as graph fact.",
            },
            "updateability": {
                "aletheia": 0.9,
                "baseline": 0.5,
                "difference": "Aletheia can rerun frontier expansion and compare proposed graph deltas by run key.",
            },
            "reviewer_actionability": {
                "aletheia": 1.0 if any((f["payload"].get("recommended_action")) for f in findings) else 0.3,
                "baseline": 0.6 if baseline.get("recommended_actions") else 0.3,
                "difference": "Aletheia attaches each action to a path and review boundary.",
            },
        }
        return {
            "dimensions": dimensions,
            "summary": {
                "aletheia_complete_deep_graph_findings": len(complete_paths),
                "baseline_claim_count": len(baseline_claims),
                "graph_finding_count": len(findings),
                "baseline_is_not_fact_source": True,
            },
            "difference_table": [
                {
                    "dimension": key,
                    "aletheia_score": value.get("aletheia"),
                    "baseline_score": value.get("baseline"),
                    "note": value.get("difference"),
                }
                for key, value in dimensions.items()
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aletheia iterative proposed-graph enrichment and benchmark")
    parser.add_argument("--target", default=os.environ.get("ALETHEIA_PG_URL", "postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology"))
    parser.add_argument("--tenant", default=os.environ.get("ALETHEIA_TENANT", "default"))
    parser.add_argument("--objective", default="Discover multi-hop graph reasoning findings")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--search-results-json")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--allowed-domain", action="append", default=[])
    parser.add_argument("--allow-discovered-domains", action="store_true")
    parser.add_argument("--max-iterations", type=int, default=2)
    parser.add_argument("--max-frontier", type=int, default=5)
    parser.add_argument("--max-results-per-query", type=int, default=3)
    parser.add_argument("--baseline-json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    agent = IterativeGraphEnrichmentAgent(
        args.target,
        tenant=args.tenant,
        search_results_json=args.search_results_json,
        seed_urls=args.seed_url,
        allowed_domains=args.allowed_domain,
        allow_discovered_domains=args.allow_discovered_domains,
        max_iterations=args.max_iterations,
        max_frontier=args.max_frontier,
        max_results_per_query=args.max_results_per_query,
    )
    result = agent.run(args.objective, artifact_keys=args.artifact)
    if args.baseline_json:
        baseline = json.loads(Path(args.baseline_json).read_text(encoding="utf-8"))
        result["benchmark"] = GraphDeepResearchBenchmark(args.target, tenant=args.tenant).compare(
            result["run"]["run_key"],
            baseline,
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"iterative_graph_enrichment status={result['run']['status']} "
            f"tenant={result['tenant']} proposed={result['run']['proposed_count']} "
            f"findings={result['run']['finding_count']} run={result['run']['run_key']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
