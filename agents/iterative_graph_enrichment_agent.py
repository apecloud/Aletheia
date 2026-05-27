import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from ontology_artifacts import (
    GraphDeepResearchBenchmarkRun,
    IterativeGraphEnrichmentRun,
    OntologyArtifact,
    ProposedGraphElement,
    _json_dump,
    ensure_artifact_schema,
)
from web_enrichment_agent import StaticSearchProvider, _clean_text, _is_crawl_allowed, _is_public_web_url


DEEP_GRAPH_REQUIRED_STEPS = ("hazard", "chokepoint", "dependent_country", "risk_metric", "recommended_action")

COUNTRY_ALIASES = {
    "CHN": "China",
    "IND": "India",
    "USA": "United States",
    "JPN": "Japan",
    "KOR": "South Korea",
    "GMB": "Gambia",
    "IRN": "Iran",
    "SAU": "Saudi Arabia",
    "ARE": "United Arab Emirates",
}

METRIC_TERMS = {
    "trade_at_risk_v": ["trade at risk", "trade exposure", "trade disruption"],
    "trade_impacted": ["trade impacted", "trade disruption", "supply chain impact"],
    "v_canal": ["canal volume", "shipping volume"],
    "dependency_share": ["dependency share", "trade dependency"],
    "import exposure": ["import exposure", "trade exposure"],
}

RELATION_TERMS = {
    "depends_on": ["depends on", "trade dependency", "maritime chokepoint"],
    "trade_dependency": ["depends on", "trade dependency", "maritime chokepoint", "trade route exposure"],
    "raises_risk_for": ["risk propagation", "hazard", "maritime disruption"],
    "dependency_chokepoint": ["chokepoint dependency", "trade route"],
    "risk_country": ["country exposure", "risk exposure"],
    "risk_chokepoint": ["chokepoint risk", "maritime risk"],
}

GRAPH_EXTRACTION_PROMPT_VERSION = "graph_entity_relation_v2"

GRAPH_EXTRACTION_PROMPT = """Extract graph-ready facts from crawled evidence.
Return strict JSON with:
1. ontology_candidates: object/relation/property proposals with label, description, domain, range, and review_required=true.
2. nodes: typed real-world entities only, with label, type, stable id hint, properties, description, confidence, and evidence_quote.
3. edges: typed binary relations only, with source_label, relation, target_label, properties, description, confidence, and evidence_quote.
4. findings: candidate analytical findings only when the evidence supports a complete path.
Rules:
- Use only facts explicitly supported by the source text.
- Prefer domain relations such as trade_dependency over generic related_to/depends_on.
- Keep metrics, confidence, source_url, and provenance as properties, not hidden text.
- Reify a fact node only when the relation needs its own identity for metrics/provenance; otherwise keep the main graph as source --relation--> target.
- Do not write canonical ontology or formal graph. Output remains draft/proposed until review."""


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


def _digest(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _extract_terms(text: str) -> dict[str, list[str]]:
    hazard_terms = []
    chokepoint_terms = []
    country_terms = []
    metric_terms = []
    lowered = text.lower()
    for term in (
        "likelihood_conflict",
        "severity_conflict",
        "likelihood_geopolitical",
        "missile strike",
        "shipping disruption",
        "sanctions",
        "conflict",
        "hazard",
    ):
        if term.lower() in lowered:
            hazard_terms.append(term)
    for term in (
        "Bab el-Mandeb Strait",
        "Hormuz Strait",
        "Gibraltar Strait",
        "Suez Canal",
        "Panama Canal",
        "Malacca Strait",
        "chokepoint",
    ):
        if term.lower() in lowered:
            chokepoint_terms.append(term)
    for term in ("CHN", "IND", "USA", "GMB", "JPN", "KOR", "IRN", "SAU", "ARE"):
        if re.search(rf"\b{re.escape(term)}\b", text):
            country_terms.append(term)
    for term in ("trade_at_risk_v", "trade_impacted", "v_canal", "dependency_share", "import exposure"):
        if term.lower() in lowered:
            metric_terms.append(term)
    return {
        "hazards": list(dict.fromkeys(hazard_terms)),
        "chokepoints": list(dict.fromkeys(chokepoint_terms)),
        "countries": list(dict.fromkeys(country_terms)),
        "metrics": list(dict.fromkeys(metric_terms)),
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
    if entity_type == "Country":
        iso3 = label.upper()
        return f"Country:{iso3}"
    return f"{entity_type}:{label}"


def _entity_description(entity_type: str, label: str, source_title: str) -> str:
    if entity_type == "Country":
        return f"Country or economy mentioned as exposed to maritime chokepoint trade disruption: {label}."
    if entity_type == "Chokepoint":
        return f"Maritime chokepoint or canal mentioned as a trade disruption concentration point: {label}."
    if entity_type == "Hazard":
        return f"Risk driver or disruption condition mentioned in source evidence: {label}."
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


def _extract_graph_semantics(frontier_item: dict[str, Any], result, summary: str) -> dict[str, Any]:
    source_text = _clean_text(" ".join([result.title or "", result.snippet or "", summary or ""]), 1600)
    terms = _extract_terms(source_text)
    source_ref = result.url
    source_title = result.title or source_ref
    ontology_candidates: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def add_node(entity_type: str, label: str, confidence: float) -> None:
        label = str(label).strip()
        if not label:
            return
        if any(node["type"] == entity_type and node["label"] == label for node in nodes):
            return
        evidence_quote = _evidence_excerpt(source_text, [label, COUNTRY_ALIASES.get(label, "")])
        description = _entity_description(entity_type, label, source_title)
        properties = {
            "canonical_id_hint": _entity_key(entity_type, label),
            "source_title": source_title,
            "source_url": source_ref,
            "extracted_from_frontier": frontier_item.get("key"),
        }
        if entity_type == "Country":
            properties["iso3"] = label.upper()
            properties["name"] = COUNTRY_ALIASES.get(label.upper(), label)
        if entity_type == "Chokepoint":
            properties["domain"] = "maritime chokepoint"
        if entity_type == "Hazard":
            properties["hazard_category"] = "maritime disruption risk"
        nodes.append(
            {
                "type": entity_type,
                "label": label,
                "description": description,
                "properties": properties,
                "evidence_quote": evidence_quote,
                "confidence": confidence,
            }
        )
        ontology_candidates.append(
            _ontology_candidate(
                "object",
                entity_type,
                f"{entity_type} entities extracted from crawled maritime risk evidence.",
                properties=["label", "description", "source_url", "confidence"],
            )
        )

    for hazard in terms["hazards"][:2]:
        add_node("Hazard", hazard, 0.68)
    for chokepoint in terms["chokepoints"][:2]:
        add_node("Chokepoint", chokepoint, 0.74)
    for country in terms["countries"][:4]:
        add_node("Country", country, 0.72)

    if terms["chokepoints"] and terms["countries"]:
        relation = "trade_dependency"
        ontology_candidates.append(
            _ontology_candidate(
                "link",
                relation,
                "A country has trade exposure or dependency through a maritime chokepoint.",
                domain="Country",
                range="Chokepoint",
                edge_properties=["metrics", "source_url", "confidence", "evidence_quote"],
            )
        )
        for country in terms["countries"][:4]:
            for chokepoint in terms["chokepoints"][:1]:
                metric_terms = terms["metrics"][:5]
                evidence_quote = _evidence_excerpt(source_text, [country, COUNTRY_ALIASES.get(country, ""), chokepoint] + metric_terms)
                edges.append(
                    {
                        "source_type": "Country",
                        "source_label": country,
                        "relation": relation,
                        "target_type": "Chokepoint",
                        "target_label": chokepoint,
                        "description": f"{country} has a maritime trade dependency through {chokepoint}.",
                        "properties": {
                            "metrics": metric_terms,
                            "source_url": source_ref,
                            "source_title": source_title,
                            "evidence_quote": evidence_quote,
                            "extracted_from_frontier": frontier_item.get("key"),
                            "fact_node_hint": f"TradeDependency:{country}::{chokepoint}",
                        },
                        "evidence_quote": evidence_quote,
                        "confidence": 0.76 if metric_terms else 0.7,
                    }
                )

    if terms["hazards"] and terms["chokepoints"]:
        relation = "raises_risk_for"
        ontology_candidates.append(
            _ontology_candidate(
                "link",
                relation,
                "A hazard or disruption driver raises risk for a maritime chokepoint.",
                domain="Hazard",
                range="Chokepoint",
                edge_properties=["source_url", "confidence", "evidence_quote"],
            )
        )
        hazard = terms["hazards"][0]
        chokepoint = terms["chokepoints"][0]
        evidence_quote = _evidence_excerpt(source_text, [hazard, chokepoint])
        edges.append(
            {
                "source_type": "Hazard",
                "source_label": hazard,
                "relation": relation,
                "target_type": "Chokepoint",
                "target_label": chokepoint,
                "description": f"{hazard} is stated as a risk driver for {chokepoint}.",
                "properties": {
                    "source_url": source_ref,
                    "source_title": source_title,
                    "evidence_quote": evidence_quote,
                    "extracted_from_frontier": frontier_item.get("key"),
                },
                "evidence_quote": evidence_quote,
                "confidence": 0.7,
            }
        )

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
        "source": {"url": source_ref, "title": source_title},
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
        _append_unique(country_terms, COUNTRY_ALIASES.get(term))
    for term in extracted["chokepoints"]:
        _append_unique(node_terms, term)
    for term in extracted["hazards"]:
        _append_unique(node_terms, term)
    for raw in [item.get("name"), payload.get("source_label"), payload.get("target_label"), payload.get("label")]:
        if raw:
            # Keep useful multi-word labels such as "Bab el-Mandeb Strait" and
            # edge labels such as "CHN depends on Bab el-Mandeb Strait".
            _append_unique(node_terms, raw, excluded=excluded_terms)
    relation = str(payload.get("relation") or item.get("relation") or "").strip()
    _append_unique(relation_terms, relation, excluded=excluded_terms)
    for term in RELATION_TERMS.get(relation, []):
        _append_unique(relation_terms, term)
    raw_metrics = payload.get("metrics") or extracted["metrics"]
    if not isinstance(raw_metrics, list):
        raw_metrics = [raw_metrics] if raw_metrics else []
    for metric in raw_metrics:
        _append_unique(metric_terms, metric)
        for term in METRIC_TERMS.get(str(metric), []):
            _append_unique(metric_terms, term)
    if tenant == "maritime-risk":
        for term in ["maritime chokepoint", "shipping disruption", "trade route risk"]:
            _append_unique(domain_terms, term)
    objective_terms = []
    for term in re.split(r"[^A-Za-z0-9_/-]+", objective or ""):
        if len(term) >= 4:
            _append_unique(objective_terms, term, excluded=excluded_terms)

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
    return {
        "query": query,
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
        if "hazard" in kind or kind == "risk_indicator" or metric.startswith("likelihood_") or metric.startswith("severity_"):
            return "hazard"
        if "chokepoint" in kind or metric == "canal":
            return "chokepoint"
        if kind in {"dependent_country", "dependent_countries", "country", "countries"} or metric == "iso3":
            return "dependent_country"
        if kind in {"trade_metric", "risk_metric"} or metric in {"trade_at_risk_v", "trade_impacted", "v_canal", "dependency_share"}:
            return "risk_metric"
        if "action" in kind:
            return "recommended_action"
        return None

    def label_for(item: dict[str, Any]) -> str:
        value = item.get("value")
        if isinstance(value, list):
            return ", ".join(str(v.get("iso3") or v.get("name") or v) for v in value[:5])
        if isinstance(value, dict):
            return str(value.get("label") or value.get("name") or value.get("iso3") or value.get("canal") or item.get("metric") or item.get("kind"))
        return str(value) if value not in (None, "") else str(item.get("metric") or item.get("kind"))

    step_order: list[str] = []
    path = []
    for item in evidence_chain:
        step = step_for(item)
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
    ):
        self.engine = create_engine(metadata_db_url)
        ensure_artifact_schema(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.tenant = tenant
        self.provider = StaticSearchProvider(search_results_json, seed_urls or [])
        self.allowed_domains = {d.lower().strip() for d in (allowed_domains or []) if d.strip()}
        self.allow_discovered_domains = allow_discovered_domains
        self.max_iterations = max_iterations
        self.max_frontier = max_frontier
        self.max_results_per_query = max_results_per_query

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

    def _candidate_elements(self, frontier_item: dict[str, Any], result, summary: str, iteration: int) -> list[dict[str, Any]]:
        extraction = _extract_graph_semantics(frontier_item, result, summary)
        terms = extraction["terms"]
        elements: list[dict[str, Any]] = []
        source_ref = result.url
        for node in extraction["nodes"]:
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
            relation_ontology = next(
                (
                    candidate
                    for candidate in extraction["ontology_candidates"]
                    if candidate.get("artifact_type") == "link" and candidate.get("label") == edge["relation"]
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
                        "relation_ontology_candidate": relation_ontology,
                        "extraction": {
                            "prompt_version": extraction["prompt_version"],
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
        if terms["hazards"] and terms["chokepoints"] and terms["countries"] and terms["metrics"]:
            evidence_chain = [
                {"kind": "hazard", "metric": terms["hazards"][0], "value": terms["hazards"][0], "source_ref": source_ref},
                {"kind": "chokepoint", "metric": "canal", "value": terms["chokepoints"][0], "source_ref": source_ref},
                {"kind": "dependent_countries", "metric": "iso3", "value": [{"iso3": c} for c in terms["countries"][:3]], "source_ref": source_ref},
                {"kind": "risk_metric", "metric": terms["metrics"][0], "value": terms["metrics"][0], "source_ref": source_ref},
                {"kind": "recommended_action", "value": {"label": "Run analyst review on exposed country/chokepoint path"}, "source_ref": "Aletheia proposed graph playbook"},
            ]
            elements.append(
                {
                    "element_type": "finding",
                    "name": f"{terms['chokepoints'][0]} risk propagates to {', '.join(terms['countries'][:3])}",
                    "payload": {
                        "finding_type": "deep_graph_finding",
                        "title": f"{terms['chokepoints'][0]} risk propagates to dependent countries",
                        "conclusion": summary,
                        "evidence_chain": evidence_chain,
                        "deep_graph_profile": deep_graph_profile(evidence_chain),
                        "extraction": {
                            "prompt_version": extraction["prompt_version"],
                            "ontology_candidates": extraction["ontology_candidates"],
                            "quality": extraction["quality"],
                            "review_boundary": "candidate_finding_review",
                            "canonical_ontology_write": False,
                            "formal_graph_write": False,
                        },
                        "recommended_action": "Run analyst review on exposed country/chokepoint path",
                        "writes_canonical": False,
                    },
                    "evidence_refs": [source_ref],
                    "source_url": source_ref,
                    "confidence": 0.73,
                    "iteration": iteration,
                }
            )
        return elements

    def _upsert_element(self, session, run_id: int, item: dict[str, Any]) -> ProposedGraphElement:
        element_key = f"proposed-graph:{self.tenant}:{_slug(item['element_type'])}:{_digest({'name': item['name'], 'payload': item['payload']})}"
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
        row.status = "draft"
        row.iteration = item.get("iteration", 1)
        return row

    def run(
        self,
        objective: str,
        artifact_keys: list[str] | None = None,
        frontier_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        session = self.Session()
        run_key = f"iterative-graph:{self.tenant}:{datetime.utcnow().strftime('%Y%m%d%H%M%S')}:{os.getpid()}"
        run = IterativeGraphEnrichmentRun(
            project_id=self.tenant,
            run_key=run_key,
            objective=objective,
            status="running",
            safety_profile_json=_json_dump(
                {
                    "space": "proposed_graph",
                    "canonical_writes": "disabled",
                    "graph_writes": "disabled",
                    "baseline_writes_to_graph": "disabled",
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
            frontier = list(frontier_items or [])[: self.max_frontier] if frontier_items else self._frontier_from_artifacts(session, artifact_keys)
            trace = []
            skipped_sources = []
            next_frontier = list(frontier)
            proposed_count = 0
            pruned_count = 0
            finding_count = 0
            for iteration in range(1, self.max_iterations + 1):
                current_frontier = next_frontier[: self.max_frontier]
                next_frontier = []
                for item in current_frontier:
                    query_plan = self._query_plan_for_frontier(item, objective)
                    query = query_plan["query"]
                    results = self.provider.search(query, self.max_results_per_query)
                    extracted_keys = []
                    pruned = []
                    for result in results:
                        if not result.url or not _is_public_web_url(result.url):
                            reason = "blocked_non_public_or_sensitive_url"
                            skipped_sources.append({"iteration": iteration, "frontier_key": item.get("key"), "url": result.url, "reason": reason, "search_query": query, "query_terms": query_plan["query_terms"]})
                            pruned.append({"url": result.url, "reason": reason})
                            pruned_count += 1
                            continue
                        allowed, blocked_reason = _is_crawl_allowed(result.url, self.allowed_domains, self.allow_discovered_domains)
                        if not allowed:
                            skipped_sources.append({"iteration": iteration, "frontier_key": item.get("key"), "url": result.url, "reason": blocked_reason, "search_query": query, "query_terms": query_plan["query_terms"]})
                            pruned.append({"url": result.url, "reason": blocked_reason})
                            pruned_count += 1
                            continue
                        summary = _clean_text(result.snippet or result.title, 700)
                        extraction_profile = _extract_graph_semantics(item, result, summary)
                        candidates = self._candidate_elements(item, result, summary, iteration)
                        if not candidates:
                            pruned.append({"url": result.url, "reason": "no_graph_candidate_extracted"})
                            pruned_count += 1
                            continue
                        for candidate in candidates:
                            row = self._upsert_element(session, run.id, candidate)
                            extracted_keys.append(row.element_key)
                            proposed_count += 1
                            if candidate["element_type"] == "finding":
                                finding_count += 1
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
                            "query_terms": query_plan["query_terms"],
                            "graph_context_used": query_plan["graph_context_used"],
                            "path_context_used": query_plan["path_context_used"],
                            "excluded_terms": query_plan["excluded_terms"],
                            "result_count": len(results),
                            "extracted_candidates": extracted_keys,
                            "extraction_prompt_version": GRAPH_EXTRACTION_PROMPT_VERSION,
                            "extraction_contract": {
                                "outputs": ["ontology_candidates", "nodes", "edges", "properties", "descriptions", "findings"],
                                "rules": [
                                    "typed entities only",
                                    "typed binary relations with properties",
                                    "evidence quote and source URL required",
                                    "candidate ontology remains review-gated",
                                    "formal graph writes disabled",
                                ],
                            },
                            "last_extraction_profile": extraction_profile if extracted_keys else None,
                            "pruned": pruned,
                        }
                    )
            run.frontier_json = _json_dump(frontier)
            run.expansion_trace_json = _json_dump(trace)
            run.skipped_sources_json = _json_dump(skipped_sources)
            run.proposed_count = proposed_count
            run.pruned_count = pruned_count
            run.finding_count = finding_count
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
            return {
                "ontology_artifacts": [list(row) for row in artifacts],
                "proposed_graph_elements": proposed,
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
                "difference": "Aletheia requires explicit hazard -> chokepoint -> country -> metric -> action paths.",
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
