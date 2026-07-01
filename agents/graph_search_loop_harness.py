"""Loop-engineering harness for approved graph search quality.

The harness evaluates retrieval health without writing graph data. It turns
approved graph search signals into metrics and repair frontier suggestions that
the enrichment loop can execute later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import text


DEFAULT_GRAPH_SEARCH_LOOP_CONFIG: dict[str, Any] = {
    "loop_id": "graph-search-loop-v1",
    "targets": {
        "min_edges_per_object": 1,
        "min_evidence_ratio": 0.8,
        "min_semantic_items_per_object": 1,
        "max_isolated_node_ratio": 0.2,
        "max_global_fallback_ratio": 0.3,
    },
    "next_action_policy": {
        "if_many_isolated_nodes": "relation_coverage_repair",
        "if_low_evidence_ratio": "evidence_repair",
        "if_low_semantic_context": "semantic_context_repair",
        "if_unanswerable_local_queries": "query_context_repair",
        "if_many_global_fallbacks": "query_alias_repair",
        "if_targets_met": "continue_graph_search_monitoring",
    },
    "repair_policy": {
        "max_items": 100,
        "frontier_priority": 140,
    },
    "evaluation": {
        "use_repository_local_context": False,
        "query_eval_mode": "fast",
    },
}


def load_graph_search_loop_config(path: str | Path | None) -> dict[str, Any]:
    config = json.loads(json.dumps(DEFAULT_GRAPH_SEARCH_LOOP_CONFIG))
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


def evaluate_graph_search_loop(
    repo,
    tenant,
    *,
    config: dict[str, Any] | None = None,
    limit: int = 200,
    sample_size: int | None = 40,
    query_samples: list[str] | None = None,
    query_eval_mode: str | None = None,
) -> dict[str, Any]:
    config = _deep_merge(json.loads(json.dumps(DEFAULT_GRAPH_SEARCH_LOOP_CONFIG)), config or {})
    graph = repo.full_graph(tenant, limit=limit) or {}
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    sampled_nodes = _sample_nodes(nodes, sample_size)
    if ((config.get("evaluation") or {}).get("use_repository_local_context")):
        local_contexts = _local_contexts(repo, tenant, sampled_nodes, limit=limit)
    else:
        local_contexts = _local_contexts_from_graph(repo, tenant, sampled_nodes, edges, limit=limit)
    community_summary = repo.graph_community_summaries(tenant, limit=max(limit, 80))
    query_eval_mode = str(query_eval_mode or (config.get("evaluation") or {}).get("query_eval_mode") or "fast").strip().lower()
    questions = query_samples or _default_query_samples(nodes)
    if query_eval_mode == "real":
        query_results = _query_results(repo, tenant, questions, limit=min(limit, 80))
    else:
        query_results = _query_results_from_graph(repo, tenant, questions, nodes, edges, limit=min(limit, 80))
    metrics = _metrics(nodes, sampled_nodes, edges, local_contexts, community_summary, query_results)
    verdict = _verdict(metrics, config)
    repair_plan = _repair_plan(tenant, local_contexts, query_results, verdict.get("next_focus"), config)
    return {
        "loop_id": config.get("loop_id"),
        "tenant": tenant.tenant_id,
        "status": "ready" if graph.get("approved") else "no_approved_graph",
        "metrics": metrics,
        "verdict": verdict,
        "repair_plan": repair_plan,
        "query_results": query_results,
        "community_summary": {
            "retrieval_mode": community_summary.get("retrieval_mode"),
            "community_count": (community_summary.get("eval") or {}).get("community_count", 0),
            "node_count": (community_summary.get("eval") or {}).get("node_count", 0),
            "edge_count": (community_summary.get("eval") or {}).get("edge_count", 0),
        },
    }


def _sample_nodes(nodes: list[dict[str, Any]], sample_size: int | None) -> list[dict[str, Any]]:
    if sample_size is None or int(sample_size) <= 0:
        return list(nodes)
    sample_size = int(sample_size)
    connected = [node for node in nodes if node.get("id")]
    return connected[:sample_size]


def _local_contexts(repo, tenant, nodes: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for node in nodes:
        node_id = str(node.get("id") or "")
        if ":" not in node_id or node_id in seen:
            continue
        seen.add(node_id)
        object_type, instance_id = node_id.split(":", 1)
        context = repo.local_rag_context(
            tenant,
            object_type,
            instance_id,
            question=f"Assess graph search context for {node.get('label') or node_id}",
            limit=min(max(10, int(limit or 200)), 80),
        )
        if not context:
            result.append({"node": node, "context": None, "eval": {"coverage": {}}})
            continue
        result.append({"node": node, "context": context, "eval": context.get("eval") or {}})
    return result


def _local_contexts_from_graph(repo, tenant, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if source:
            adjacency.setdefault(source, []).append(edge)
        if target:
            adjacency.setdefault(target, []).append(edge)
    semantic_counts = _semantic_counts_by_node(repo, tenant, nodes, limit=limit)
    result = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        local_edges = adjacency.get(node_id, [])
        evidence_count = sum(1 for edge in local_edges if _edge_has_evidence(edge))
        unsupported_edge_count = sum(1 for edge in local_edges if not _edge_has_evidence(edge))
        semantic_count = int(semantic_counts.get(node_id) or 0)
        context = {
            "center": {
                "id": node_id,
                "label": node.get("label") or node_id,
                "type": node.get("type"),
            },
            "edges": local_edges[: min(max(1, int(limit or 200)), 80)],
        }
        eval_payload = {
            "edge_count": len(local_edges),
            "evidence_count": evidence_count,
            "semantic_item_count": semantic_count,
            "unsupported_edge_count": unsupported_edge_count,
            "coverage": {
                "has_center": True,
                "has_relation": bool(local_edges),
                "has_evidence": evidence_count > 0,
                "has_semantic_context": semantic_count > 0,
            },
        }
        result.append({"node": node, "context": context, "eval": eval_payload})
    return result


def _edge_has_evidence(edge: dict[str, Any]) -> bool:
    properties = edge.get("properties") if isinstance(edge.get("properties"), dict) else {}
    evidence_refs = properties.get("evidence_refs") or edge.get("evidence_refs") or []
    return bool(
        edge.get("source_url")
        or edge.get("evidence_quote")
        or evidence_refs
        or properties.get("source_url")
        or properties.get("evidence_quote")
    )


def _semantic_counts_by_node(repo, tenant, nodes: list[dict[str, Any]], *, limit: int) -> dict[str, int]:
    if not hasattr(repo, "metadata_engine_for"):
        return {}
    labels_by_node: dict[str, set[str]] = {}
    for node in nodes:
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        labels = {
            str(value or "").strip().lower()
            for value in [node_id, node.get("label"), *(node.get("aliases") or [])]
            if str(value or "").strip()
        }
        short_id = node_id.split(":", 1)[1] if ":" in node_id else node_id
        if short_id:
            labels.add(short_id.lower())
        labels_by_node[node_id] = {label for label in labels if len(label) >= 3}
    if not labels_by_node:
        return {}
    try:
        with repo.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT name, element_type, payload_json, source_url
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id
                      AND element_type IN ('situation', 'metric_observation', 'metric_change_observation',
                                           'impact_claim', 'indicator_claim', 'recommendation')
                      AND status IN ('approved', 'needs_more_evidence')
                    ORDER BY created_at DESC NULLS LAST, id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": max(50, min(int(limit or 200) * 4, 1000))},
            ).mappings().all()
    except Exception:
        return {}
    counts = {node_id: 0 for node_id in labels_by_node}
    for row in rows:
        payload = _json_load(row["payload_json"], {}) if row["payload_json"] else {}
        text_blob = " ".join(
            str(value or "").lower()
            for value in [
                row["name"],
                row["element_type"],
                row["source_url"],
                payload.get("subject"),
                payload.get("target"),
                payload.get("metric_key"),
                payload.get("recommended_action"),
                payload.get("evidence_quote"),
                payload.get("summary"),
                payload.get("description"),
            ]
        )
        for node_id, labels in labels_by_node.items():
            if any(label in text_blob for label in labels):
                counts[node_id] += 1
    return counts


def _json_load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except Exception:
        return default


def _default_query_samples(nodes: list[dict[str, Any]]) -> list[str]:
    samples = []
    for node in nodes[:10]:
        label = node.get("label") or node.get("id")
        if label:
            samples.append(f"What is connected to {label}?")
    samples.append("Summarize the approved graph.")
    return samples


def _query_results(repo, tenant, questions: list[str], *, limit: int) -> list[dict[str, Any]]:
    result = []
    for question in questions:
        context = repo.graph_rag_query_context(tenant, question=question, limit=limit)
        route = context.get("query_route") or {}
        expected_route = _expected_query_route(question)
        result.append(
            {
                "question": question,
                "expected_route": expected_route,
                "route": route.get("route"),
                "reason": route.get("reason"),
                "matched_node": route.get("matched_node"),
                "retrieval_mode": context.get("retrieval_mode"),
                "eval": context.get("eval") or {},
            }
        )
    return result


def _query_results_from_graph(repo, tenant, questions: list[str], nodes: list[dict[str, Any]], edges: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    labels = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        for value in [node_id, node.get("label"), *(node.get("aliases") or [])]:
            text_value = str(value or "").strip()
            if len(text_value) >= 3:
                labels.append((text_value.lower(), node))
    result = []
    for question in questions:
        expected_route = _expected_query_route(question)
        lowered_question = str(question or "").lower()
        matched_node = None
        if expected_route == "local":
            for label, node in sorted(labels, key=lambda item: len(item[0]), reverse=True):
                if label in lowered_question:
                    matched_node = node
                    break
        if matched_node:
            local_context = _local_contexts_from_graph(repo, tenant, [matched_node], edges, limit=limit)
            eval_payload = (local_context[0].get("eval") if local_context else {}) or {}
            result.append(
                {
                    "question": question,
                    "expected_route": expected_route,
                    "route": "local",
                    "reason": "fast_label_match",
                    "matched_node": matched_node.get("id"),
                    "retrieval_mode": "local_graph_context",
                    "eval": eval_payload,
                }
            )
            continue
        route = "global"
        result.append(
            {
                "question": question,
                "expected_route": expected_route,
                "route": route,
                "reason": "fast_global_summary" if expected_route == "global" else "fast_no_label_match",
                "matched_node": None,
                "retrieval_mode": "community_summary",
                "eval": {},
            }
        )
    return result


def _metrics(
    nodes: list[dict[str, Any]],
    sampled_nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    local_contexts: list[dict[str, Any]],
    community_summary: dict[str, Any],
    query_results: list[dict[str, Any]],
) -> dict[str, Any]:
    object_count = len(nodes)
    context_count = len(local_contexts)
    contexts_with_relation = 0
    contexts_with_semantic = 0
    contexts_with_evidence = 0
    total_evidence = 0
    total_unsupported_edges = 0
    isolated_nodes = []
    low_evidence_nodes = []
    low_semantic_nodes = []
    for item in local_contexts:
        node = item.get("node") or {}
        context = item.get("context") or {}
        eval_payload = item.get("eval") or {}
        coverage = eval_payload.get("coverage") or {}
        edge_count = int(eval_payload.get("edge_count") or 0)
        evidence_count = int(eval_payload.get("evidence_count") or 0)
        semantic_count = int(eval_payload.get("semantic_item_count") or 0)
        unsupported_count = int(eval_payload.get("unsupported_edge_count") or 0)
        total_evidence += evidence_count
        total_unsupported_edges += unsupported_count
        if coverage.get("has_relation"):
            contexts_with_relation += 1
        else:
            isolated_nodes.append(_node_ref(node, context))
        if coverage.get("has_evidence"):
            contexts_with_evidence += 1
        if not coverage.get("has_evidence") and edge_count > 0:
            low_evidence_nodes.append(_node_ref(node, context))
        if coverage.get("has_semantic_context"):
            contexts_with_semantic += 1
        else:
            low_semantic_nodes.append(_node_ref(node, context))
    local_context_pass_rate = _ratio(contexts_with_relation, context_count)
    semantic_context_ratio = _ratio(contexts_with_semantic, context_count)
    evidence_ratio = _ratio(contexts_with_evidence, context_count)
    global_fallbacks = [item for item in query_results if item.get("route") == "global"]
    unexpected_global_fallbacks = [
        item for item in query_results
        if item.get("expected_route") == "local" and item.get("route") == "global"
    ]
    unanswerable_local_queries = [
        item for item in query_results
        if item.get("route") == "local"
        and not (((item.get("eval") or {}).get("coverage") or {}).get("has_relation"))
    ]
    return {
        "object_count": object_count,
        "sampled_object_count": len(sampled_nodes),
        "edge_count": len(edges),
        "local_context_count": context_count,
        "objects_with_relation": contexts_with_relation,
        "isolated_node_count": len(isolated_nodes),
        "isolated_node_ratio": _ratio(len(isolated_nodes), context_count),
        "local_context_pass_rate": local_context_pass_rate,
        "objects_with_evidence": contexts_with_evidence,
        "evidence_ratio": evidence_ratio,
        "total_evidence_count": total_evidence,
        "unsupported_edge_count": total_unsupported_edges,
        "objects_with_semantic_context": contexts_with_semantic,
        "semantic_context_ratio": semantic_context_ratio,
        "query_count": len(query_results),
        "global_fallback_count": len(global_fallbacks),
        "global_fallback_ratio": _ratio(len(global_fallbacks), len(query_results)),
        "unexpected_global_fallback_count": len(unexpected_global_fallbacks),
        "unexpected_global_fallback_ratio": _ratio(len(unexpected_global_fallbacks), len(query_results)),
        "unanswerable_local_query_count": len(unanswerable_local_queries),
        "unanswerable_local_query_ratio": _ratio(len(unanswerable_local_queries), len(query_results)),
        "community_count": (community_summary.get("eval") or {}).get("community_count", 0),
        "isolated_nodes": isolated_nodes[:50],
        "low_evidence_nodes": low_evidence_nodes[:50],
        "low_semantic_nodes": low_semantic_nodes[:50],
    }


def _node_ref(node: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    center = (context or {}).get("center") or {}
    node_id = node.get("id") or center.get("id")
    label = node.get("label") or center.get("label") or node_id
    return {
        "id": node_id,
        "label": label,
        "type": node.get("type") or center.get("type"),
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _verdict(metrics: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    targets = config.get("targets") or {}
    policy = config.get("next_action_policy") or {}
    reasons = []
    if metrics.get("isolated_node_ratio", 0.0) > float(targets.get("max_isolated_node_ratio", 0.2)):
        reasons.append("too many approved objects have no local graph relation")
        focus = policy.get("if_many_isolated_nodes", "relation_coverage_repair")
    elif metrics.get("evidence_ratio", 1.0) < float(targets.get("min_evidence_ratio", 0.8)):
        reasons.append("too many local graph contexts lack evidence")
        focus = policy.get("if_low_evidence_ratio", "evidence_repair")
    elif metrics.get("semantic_context_ratio", 1.0) < 1.0 and float(targets.get("min_semantic_items_per_object", 1)) > 0:
        reasons.append("too many local graph contexts lack semantic items")
        focus = policy.get("if_low_semantic_context", "semantic_context_repair")
    elif metrics.get("unanswerable_local_query_count", 0) > 0:
        reasons.append("some local graph search queries route to objects without answerable relations")
        focus = policy.get("if_unanswerable_local_queries", "query_context_repair")
    elif metrics.get("unexpected_global_fallback_ratio", 0.0) > float(targets.get("max_global_fallback_ratio", 0.3)):
        reasons.append("too many local-intent graph search queries fall back to global community summary")
        focus = policy.get("if_many_global_fallbacks", "query_alias_repair")
    else:
        focus = policy.get("if_targets_met", "continue_graph_search_monitoring")
        reasons.append("graph search loop targets met")
    return {
        "passed": focus == policy.get("if_targets_met", "continue_graph_search_monitoring"),
        "next_focus": focus,
        "reasons": reasons,
    }


def _repair_plan(
    tenant,
    local_contexts: list[dict[str, Any]],
    query_results: list[dict[str, Any]],
    focus: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    max_items = max(1, int(((config.get("repair_policy") or {}).get("max_items")) or 100))
    priority = float(((config.get("repair_policy") or {}).get("frontier_priority")) or 140)
    items = []
    if focus == "relation_coverage_repair":
        for item in local_contexts:
            eval_payload = item.get("eval") or {}
            if (eval_payload.get("coverage") or {}).get("has_relation"):
                continue
            node = _node_ref(item.get("node") or {}, item.get("context") or {})
            items.append(_frontier_item(tenant, node, priority=priority, reason="approved object has no local graph relation"))
            if len(items) >= max_items:
                break
    elif focus == "evidence_repair":
        for item in local_contexts:
            eval_payload = item.get("eval") or {}
            if (eval_payload.get("coverage") or {}).get("has_evidence") or int(eval_payload.get("edge_count") or 0) <= 0:
                continue
            node = _node_ref(item.get("node") or {}, item.get("context") or {})
            items.append(_frontier_item(tenant, node, priority=priority, reason="local graph relation lacks evidence"))
            if len(items) >= max_items:
                break
    elif focus == "semantic_context_repair":
        for item in local_contexts:
            eval_payload = item.get("eval") or {}
            if (eval_payload.get("coverage") or {}).get("has_semantic_context"):
                continue
            node = _node_ref(item.get("node") or {}, item.get("context") or {})
            items.append(_frontier_item(tenant, node, priority=priority, reason="local graph context lacks semantic item"))
            if len(items) >= max_items:
                break
    elif focus == "query_alias_repair":
        for query in query_results:
            if query.get("expected_route") != "local" or query.get("route") != "global":
                continue
            items.append(
                {
                    "kind": "query_alias_repair",
                    "question": query.get("question"),
                    "reason": "query fell back to global graph search",
                    "frontier_item": {
                        "key": f"graph-search-query:{tenant.tenant_id}:{abs(hash(query.get('question') or ''))}",
                        "name": query.get("question") or "graph search query",
                        "source": "graph_search_loop",
                        "source_kind": "graph_search_query_alias_repair",
                        "priority": priority,
                        "reason": "Improve aliases or graph labels so this query can route to local search.",
                        "payload": {"question": query.get("question")},
                    },
                }
            )
            if len(items) >= max_items:
                break
    elif focus == "query_context_repair":
        for query in query_results:
            coverage = ((query.get("eval") or {}).get("coverage") or {})
            if query.get("route") != "local" or coverage.get("has_relation"):
                continue
            matched_node = query.get("matched_node") or ""
            object_type, instance_id = matched_node.split(":", 1) if ":" in matched_node else ("Object", matched_node)
            items.append(
                {
                    "kind": "query_context_repair",
                    "question": query.get("question"),
                    "matched_node": matched_node,
                    "reason": "query routed local but matched context has no answerable relation",
                    "frontier_item": {
                        "key": f"graph-search-query-context:{object_type}:{instance_id}",
                        "name": matched_node or query.get("question") or "graph search query context",
                        "object_type": object_type,
                        "source": "graph_search_loop",
                        "source_kind": "graph_search_query_context_repair",
                        "priority": priority,
                        "reason": "Enrich relation coverage for the object selected by graph search query routing.",
                        "payload": {
                            "question": query.get("question"),
                            "matched_node": matched_node,
                            "object_type": object_type,
                            "repair_reason": "local query context lacks relation",
                        },
                    },
                }
            )
            if len(items) >= max_items:
                break
    return {
        "focus": focus,
        "actionable": bool(items),
        "item_count": len(items),
        "items": items,
    }


def _expected_query_route(question: str) -> str:
    lowered = str(question or "").strip().lower()
    global_markers = {
        "summarize",
        "summary",
        "overview",
        "overall",
        "global",
        "communities",
        "graph",
        "all",
        "整体",
        "总结",
        "全局",
        "概览",
    }
    local_markers = {
        "connected to",
        "related to",
        "around",
        "profile",
        "what is connected",
        "关联",
        "连接",
        "画像",
    }
    if any(marker in lowered for marker in local_markers):
        return "local"
    if any(marker in lowered for marker in global_markers):
        return "global"
    return "local"


def _frontier_item(tenant, node: dict[str, Any], *, priority: float, reason: str) -> dict[str, Any]:
    node_id = str(node.get("id") or "")
    object_type, instance_id = node_id.split(":", 1) if ":" in node_id else (node.get("type") or "Object", node_id)
    return {
        "kind": "graph_search_repair",
        "node": node,
        "reason": reason,
        "frontier_item": {
            "key": f"graph-search-coverage:{object_type}:{instance_id}",
            "name": node.get("label") or node_id,
            "object_type": object_type,
            "source": "graph_search_loop",
            "source_kind": "graph_search_loop_repair",
            "priority": priority,
            "reason": reason,
            "payload": {
                "node_id": node_id,
                "label": node.get("label"),
                "object_type": object_type,
                "repair_reason": reason,
            },
        },
    }
