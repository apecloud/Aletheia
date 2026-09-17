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


# Ordered script-detection fallback for when no explicit `language` is
# given -- checked in order, first match wins. Not an allowlist of
# "supported" languages (resolve_output_language's explicit-`language`
# branch accepts ANY code a caller supplies, unrestricted); this table only
# covers the question-sniffing fallback path, so it only needs to be as
# broad as "scripts we can tell apart unambiguously by Unicode block."
# Hiragana/Katakana is checked before the CJK Unified Ideographs range so
# Japanese text (which mixes kanji with kana) resolves to "ja", not "zh".
_SCRIPT_DETECTION_RULES = (
    (re.compile(r"[\u3040-\u309f\u30a0-\u30ff]"), "ja"),
    (re.compile(r"[\u4e00-\u9fff]"), "zh"),
    (re.compile(r"[\uac00-\ud7a3]"), "ko"),
    (re.compile(r"[\u0400-\u04ff]"), "ru"),
    (re.compile(r"[\u0600-\u06ff]"), "ar"),
    (re.compile(r"[\u0900-\u097f]"), "hi"),
)


def resolve_output_language(language, question=None, default="en"):
    """Normalize `language` (or, failing that, sniff `question`) into a
    lowercase ISO-639-1-ish code -- e.g. "zh", "ja", "es" -- not a boolean.
    Any explicit `language` the caller supplies is trusted and returned
    verbatim (after stripping a region/script suffix like "-CN"/"_TW"),
    with NO allowlist restricting it to a fixed set -- this is what makes
    the mechanism general rather than hardcoded to a single language pair.
    Falls back to `_SCRIPT_DETECTION_RULES` against `question` only when
    `language` is empty (older tasks created before scope.language existed,
    or callers that don't pass one, e.g. scripts), then to `default`."""
    if language:
        code = str(language).strip().lower()
        for sep in ("-", "_"):
            if sep in code:
                code = code.split(sep, 1)[0]
                break
        return code or default
    text = question or ""
    for pattern, code in _SCRIPT_DETECTION_RULES:
        if pattern.search(text):
            return code
    return default


def wants_zh_output(language, question=None):
    """Whether reasoning-conclusion text should render in Chinese. Prefers
    the explicit `language` the task was created with (the UI's own
    language setting, e.g. task.scope.language) -- so the conclusion always
    matches the setting the user is looking at -- and only falls back to
    sniffing the question text for CJK characters when no explicit language
    was recorded (older tasks created before this existed, or callers that
    don't pass one, e.g. scripts). Thin boolean wrapper around
    resolve_output_language, kept for the ~30 existing call sites across
    this module/traversal.py/planner.py that only ever need a zh/en
    decision for their hand-authored bilingual template text -- see
    resolve_output_language for the general, non-boolean resolver."""
    return resolve_output_language(language, question) == "zh"


def plain_reasoning_title(question, label, ranked_paths, second_hop_paths=None, language=None, relation_summary=None):
    wants_zh = wants_zh_output(language, question)
    label = display_label_from_question(question, label)
    top_labels = _unique_labels(path.get("label") for path in (ranked_paths or []) if path.get("label"))[:3]
    if not top_labels and relation_summary:
        # SQL-derived ranked_paths is always empty for graph-native tenants
        # (source_key_profile is retired) -- relation_summary (the actual
        # relation types the center is connected through, e.g.
        # PARENT_COMMIT/TOUCHES) is its graph-native equivalent, so reuse
        # the exact same "main relationship paths" phrasing below instead
        # of falling straight to the generic "risk profile" title.
        top_labels = _unique_labels(item.get("relation") for item in relation_summary if item.get("relation"))[:3]
    if not top_labels:
        return f"{label} 风险画像" if wants_zh else f"{label} risk profile"
    if len(top_labels) == 1 and top_labels[0].lower() == str(label).lower():
        return f"{label} 风险监控优先级" if wants_zh else f"{label} risk monitoring priority"
    if wants_zh:
        return f"{label} 主要关联路径：{'、'.join(top_labels)}"
    return f"{label} main relationship paths: {', '.join(top_labels)}"


def plain_reasoning_conclusion(question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree, language=None):
    wants_zh = wants_zh_output(language, question)
    label = display_label_from_question(question, label or "selected entity")
    top_labels = _unique_labels(path.get("label") for path in (ranked_paths or []) if path.get("label"))[:3]
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
        if len(top_labels) == 1 and top_labels[0].lower() == str(label).lower():
            if wants_zh:
                return (
                    f"{label} 应被视为业务风险监控优先点：受控证据显示其承载的暴露规模较高，"
                    "一旦出现扰动，影响更可能体现为贸易流、海运成本和应急绕航压力。"
                )
            return (
                f"{label} should be treated as a business risk monitoring priority: controlled evidence shows material exposure there, "
                "so disruption would most likely affect trade flow, shipping cost, and contingency routing pressure."
            )
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
            return f"{label} 已具备形成业务风险判断的受控证据；应优先评估扰动对运营连续性、贸易敞口和替代路径的影响。"
        return f"{label} has enough controlled evidence for a business risk readout; prioritize review of operational continuity, trade exposure, and alternate-route impact."
    if wants_zh:
        # detailed_conclusion (ReasoningEngine's profile_summary) is
        # English-only prose we can't translate here without an LLM call --
        # for the graph-native path (no SQL-derived ranked_paths/source_rows,
        # so this is the common case for graph-native tenants), synthesize a
        # Chinese sentence from the same degree count instead of leaking the
        # English text through when the setting asks for Chinese.
        degree_count = (graph_degree or {}).get("center") or (graph_degree or {}).get("visible_graph_center")
        if degree_count:
            return f"{label} 存在于已批准图谱中，关联 {degree_count} 个相关实体；当前证据尚不足以形成更细致的路径结论。"
        return f"{label} 暂无足够的关联证据形成直白结论。"
    return detailed_conclusion or f"{label} does not yet have enough related evidence for a clear conclusion."


def _unique_labels(labels):
    result = []
    seen = set()
    for label in labels:
        text = str(label or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


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
