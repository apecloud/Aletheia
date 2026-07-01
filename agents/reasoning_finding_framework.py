import re
from urllib.parse import quote


DEEP_GRAPH_REQUIRED_STEPS = ("source_entity", "relation", "target_entity", "evidence", "action")


def finding_canonical_boundary():
    return {
        "finding_approval_writes": ["aletheia_reasoning_findings", "aletheia_reasoning_reviews"],
        "canonical_ontology_write": False,
        "graph_write": False,
        "auto_business_action": False,
        "promotion_requires": "separate ontology/graph/rule proposal review gate",
    }


def review_graph_scope_action(structured_answer=None, structured_response=None):
    action = {
        "type": "review_graph_scope",
        "title": "Review scoped graph evidence before operational action",
        "description": "Use this draft as a reviewer prompt; do not treat it as an approved finding until it passes the review gate.",
        "execution_boundary": "proposal_only",
    }
    if structured_answer:
        action["structured_answer"] = structured_answer
        action["structured_response"] = structured_response
    return action


def scope_limit_counter_evidence(has_structured_answer):
    summary = (
        "Conclusions are based solely on the approved graph and controlled aggregation; external benchmarks, thresholds, and unapproved evidence are not included."
        if has_structured_answer
        else "The task cannot expand beyond the selected approved graph scope without a new bounded graph request."
    )
    return [{"kind": "scope_limit", "summary": summary}]


def scoped_graph_finding(task_key, title, conclusion, evidence_paths, structured_answer=None, structured_response=None, now_ms=None):
    run_suffix = now_ms if now_ms is not None else "pending"
    return {
        "canonical_key": f"finding:graph-scope:{task_key}:run-{run_suffix}",
        "title": title,
        "conclusion": conclusion,
        "confidence": 0.78 if structured_answer else 0.72,
        "supporting_evidence": evidence_paths,
        "counter_evidence": scope_limit_counter_evidence(bool(structured_answer)),
        "recommended_action": review_graph_scope_action(structured_answer, structured_response),
    }


def entity_profile_aggregate_evidence(tenant_id, task_key, center_node, scope_depth, metrics):
    metrics = metrics or {}
    rankings = metrics.get("rankings") or []
    source_key_profile = metrics.get("source_key_profile") or {}
    label = metrics.get("label") or center_node
    if source_key_profile.get("related_tables"):
        top_paths = source_key_profile.get("top_paths") or []
        path_summary = ", ".join(
            f"{path.get('label')} ({path.get('metric')} {_format_number(path.get('metric_value'))})"
            for path in top_paths[:3]
        ) or "no ranked paths"
        ranking_summary = f"{source_key_profile.get('total_key_rows', 0)} source rows; top paths: {path_summary}"
        second_hop_paths = source_key_profile.get("second_hop_paths") or []
        if second_hop_paths:
            shared_summary = "; ".join(
                f"{path.get('label')} -> {', '.join(str(peer.get('key')) for peer in path.get('top_peers', [])[:4])}"
                for path in second_hop_paths[:3]
            )
            ranking_summary += f"; depth-{source_key_profile.get('scope_depth', scope_depth)} shared paths: {shared_summary}"
        aggregate_label = f"{label} Source Evidence Profile"
        aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + degree + source-key metric aggregation"
    else:
        ranking_summary = "; ".join(
            f"{ranking.get('my_count')} {ranking.get('target_type')}(s) (#{ranking.get('rank')}/{ranking.get('total_peers')}, {ranking.get('level')})"
            for ranking in rankings
            if ranking.get("my_count", 0) > 0
        ) or "no ranked relationships"
        aggregate_label = f"{label} Business Profile"
        aggregate_source_ref = f"{metrics.get('object_type', 'entity')} + peer ranking + value aggregation"
    evidence = {
        "kind": "controlled_aggregate",
        "label": aggregate_label,
        "summary": f"{label}: {ranking_summary}",
        "url": f"/reasoning.html?tenant={tenant_id}&task={quote(str(task_key or ''))}",
        "source_ref": aggregate_source_ref,
        "payload": metrics,
    }
    return evidence, ranking_summary


def _format_number(value):
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def display_label_from_question(question, fallback):
    question = question or ""
    fallback = fallback or "selected entity"
    match = re.search(r"([A-Z][A-Za-z .'-]{1,80}\s+\([A-Z]{3}\))", question)
    return match.group(1) if match else fallback


def plain_reasoning_title(question, label, ranked_paths, second_hop_paths=None):
    wants_zh = bool(re.search(r"[\u4e00-\u9fff]", question or ""))
    label = display_label_from_question(question, label)
    top_labels = [str(path.get("label")) for path in (ranked_paths or []) if path.get("label")][:3]
    if not top_labels:
        return f"{label} 风险画像" if wants_zh else f"{label} risk profile"
    if wants_zh:
        return f"{label} 主要关联路径：{'、'.join(top_labels)}"
    return f"{label} main relationship paths: {', '.join(top_labels)}"


def plain_reasoning_conclusion(question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree):
    wants_zh = bool(re.search(r"[\u4e00-\u9fff]", question or ""))
    label = display_label_from_question(question, label or "selected entity")
    top_labels = [str(path.get("label")) for path in (ranked_paths or []) if path.get("label")][:3]
    peer_keys = []
    for path in second_hop_paths or []:
        for peer in path.get("top_peers") or []:
            key = peer.get("key") or peer.get("label") or peer.get("id")
            if key and key not in peer_keys:
                peer_keys.append(str(key))
            if len(peer_keys) >= 5:
                break
        if len(peer_keys) >= 5:
            break
    source_rows = (graph_degree or {}).get("source_key_row_degree")
    if top_labels:
        paths_text = "、".join(top_labels) if wants_zh else ", ".join(top_labels)
        if peer_keys:
            peers_text = "、".join(peer_keys) if wants_zh else ", ".join(peer_keys)
            if wants_zh:
                return (
                    f"{label} 的主要敏感路径集中在 {paths_text}。这些路径还连接 {peers_text} 等相关方，"
                    "说明风险来自高价值路径和关键对象的重叠。"
                )
            return (
                f"{label}'s main exposure is concentrated in {paths_text}. "
                f"Those paths also connect {peers_text}, so the risk is driven by overlap between high-value paths and key counterparties."
            )
        if wants_zh:
            return f"{label} 的主要敏感路径集中在 {paths_text}；具体排序和数值见下方关键路径。"
        return f"{label}'s main exposure is concentrated in {paths_text}; see the ranked paths below for the supporting metrics."
    if source_rows:
        if wants_zh:
            return f"{label} 在受控源数据中有 {source_rows} 条相关记录；当前证据足以做画像，但还需要关键路径指标来判断风险优先级。"
        return f"{label} has {source_rows} related controlled source rows; it can be profiled, but path-level metrics are needed to rank risk priority."
    return detailed_conclusion or (f"{label} 暂无足够的关联证据形成直白结论。" if wants_zh else f"{label} does not yet have enough related evidence for a clear conclusion.")


def paths_with_peer(second_hop_paths, peer_keys):
    wanted = {str(key).upper() for key in peer_keys}
    labels = []
    for path in second_hop_paths or []:
        path_label = path.get("label")
        if not path_label:
            continue
        for peer in path.get("top_peers") or []:
            key = str(peer.get("key") or peer.get("label") or peer.get("id") or "").upper()
            if key in wanted:
                labels.append(str(path_label))
                break
    return labels


def deep_graph_profile(evidence_chain):
    def step_for(item):
        kind = str(item.get("kind") or "").lower()
        if "action" in kind:
            return "action"
        if "source" in kind and "entity" in kind:
            return "source_entity"
        if "target" in kind and "entity" in kind:
            return "target_entity"
        if "relation" in kind or "edge" in kind or item.get("source_label") or item.get("target_label"):
            return "relation"
        if item.get("source_label") or item.get("source") or item.get("subject"):
            return "source_entity"
        if item.get("target_label") or item.get("target") or item.get("object"):
            return "target_entity"
        if item.get("metric") or isinstance(item.get("value"), (int, float)):
            return "evidence"
        if item.get("source_ref"):
            return "evidence"
        return None

    def label_for(item):
        value = item.get("value")
        if isinstance(value, list):
            labels = [
                str(v.get("label") or v.get("name") or v.get("id") or v.get("key") or v)
                for v in value[:5]
                if isinstance(v, dict)
            ]
            return ", ".join(labels[:5]) or item.get("metric") or item.get("kind")
        if isinstance(value, dict):
            return value.get("label") or value.get("name") or value.get("id") or value.get("key") or item.get("metric") or item.get("kind")
        return str(value) if value not in (None, "") else item.get("metric") or item.get("kind")

    step_order = []
    nodes = []
    for item in evidence_chain or []:
        if not isinstance(item, dict):
            continue
        step = step_for(item)
        if not step:
            continue
        if step not in step_order:
            step_order.append(step)
        nodes.append(
            {
                "step": step,
                "kind": item.get("kind"),
                "source_ref": item.get("source_ref"),
                "metric": item.get("metric"),
                "label": label_for(item),
            }
        )
    missing_steps = [step for step in DEEP_GRAPH_REQUIRED_STEPS if step not in step_order]
    hop_count = max(len(step_order) - 1, 0)
    multi_hop = hop_count >= 3 and not missing_steps
    return {
        "reasoning_type": "graph_multi_hop" if multi_hop else "evidence_chain",
        "finding_emphasis": "deep_graph_finding" if multi_hop else "candidate_finding",
        "required_steps": list(DEEP_GRAPH_REQUIRED_STEPS),
        "observed_steps": step_order,
        "missing_steps": missing_steps,
        "hop_count": hop_count,
        "multi_hop": multi_hop,
        "path": nodes,
        "path_label": " -> ".join(node["label"] for node in nodes if node.get("label")),
    }
