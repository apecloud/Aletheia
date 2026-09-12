"""TraversalMixin: the approved-graph-scoped reasoning pipeline -- building the
scoped graph context (BFS/DFS traversal analysis, per-edge/target reasoning
units, business-conclusion synthesis, and the reasoning_response_v1 payload
shape), the LLM-trace event payload helpers, and the two task-runner entry
points, run_scoped_graph_task_streaming (SSE-driving generator) and
run_scoped_graph_task (synchronous).

run_scoped_graph_task used to independently duplicate run_scoped_graph_task_streaming's
~185 lines of control flow. It is now a thin wrapper that drains the streaming
generator and returns the terminal run_complete event's payload -- see its
docstring below for the exact behavior-preserving mapping of the streaming
generator's exit paths (missing evidence, no approved/demo projection,
success, task-not-found, closed-task) onto the original synchronous method's
return-value/exception contract.

Extracted from the monolithic reasoning.py -- part of the reasoning/ package
split. No behavior change beyond the documented run_scoped_graph_task
collapse.
"""

import json
import time
from urllib.parse import quote
from aletheia.reasoning.datalog_reasoner import DatalogReasoner
from aletheia.reasoning.engine import ReasoningEngine
from aletheia.reasoning.finding_framework import (
    entity_profile_aggregate_evidence,
    plain_reasoning_conclusion,
    plain_reasoning_title,
    review_graph_scope_action,
    scoped_graph_finding,
    wants_zh_output,
)
from aletheia.reasoning.graph_facts import load_facts

# Minimum related-edge count at which a depth-escalation pass is considered
# to have found real connected evidence -- deliberately a modest "found
# some actual structure" bar, not a quality score (unlike
# _evaluate_reasoning_conclusion's 9-check score, which several checks make
# structurally unreachable for graph-native tenants -- see
# run_scoped_graph_task_streaming's depth-escalation loop for why that
# score is not used as the stop condition here).
_MIN_SUFFICIENT_RELATED_EDGES = 3


class TraversalMixin:
    def run_scoped_graph_task_streaming(self, tenant, task_key):
        started = time.monotonic()
        task = self._get_task_row(tenant, task_key)
        if task is None:
            yield {"event": "error", "data": {"message": f"Task not found: {task_key}"}}
            return
        if task.get("status") == "closed":
            yield {"event": "error", "data": {"message": "Cannot run a closed task"}}
            return
        if task.get("status") == "completed":
            self.update_task_status(tenant, task_key, "active")
            task["status"] = "active"
        scope = task.get("scope") or {}
        query_plan = [
            "Validate tenant-scoped graph task and approved-only scope.",
            "Read only the selected node or edge evidence path from Graph Explorer.",
            "Propose a draft finding without approving, ingesting, or changing canonical graph data.",
        ]
        yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
        tool_calls = [
            {"tool": "graph_query", "tenant_id": tenant.tenant_id, "approved_only": True, "status": "completed"},
            {"tool": "propose_finding", "tenant_id": tenant.tenant_id, "write_scope": "draft_reasoning_artifact", "status": "completed"},
        ]
        evidence_paths = list(scope.get("evidence_paths") or [])
        yield {"event": "step", "data": {"tool": "graph_query", "status": "completed" if evidence_paths else "blocked", "step": 1, "total": 3}}
        if not evidence_paths:
            tool_calls[0]["status"] = "blocked"
            output = {"summary": "Scoped graph reasoning blocked because no evidence paths were provided.", "unsupported_claims": ["missing evidence path"]}
            eval_result = {"passed": False, "approved_only": True, "draft_only": True, "unsupported_claims": ["missing evidence path"], "evidence_path_count": 0}
            run = self._record_run(tenant, task, query_plan, tool_calls, [], output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        center_node = scope.get("center_node")
        depth_ceiling = max(1, min(int(scope.get("depth") or 1), 3))
        scope_limit = int(scope.get("node_limit") or 200)
        scope_edge_limit = int(scope.get("edge_limit") or scope_limit)
        demo_mode = self._explicit_demo_mode(scope)

        # Depth-escalation loop: start shallow (depth 1) and only pay for a
        # deeper Nebula traversal when the shallower pass didn't already
        # find enough connected evidence. Approval/projection status is
        # checked once, on the first pass only -- it's a review-status
        # property of the graph scope, not something a deeper BFS can
        # change, so there's no point escalating into a scope that will
        # never become approved. related_edge_count (not degree.center,
        # which is just the center node's direct-edge count and is
        # therefore depth-invariant) is the signal, because it actually
        # grows as _scoped_graph_prompt_context's BFS visits more hops.
        graph_context = None
        depth_attempts = []
        scope_depth = depth_ceiling
        blocked = False
        previous_related_count = -1
        for current_depth in range(1, depth_ceiling + 1):
            graph_context = self._scoped_graph_prompt_context(
                tenant,
                center_node,
                current_depth,
                scope_limit,
                scope_edge_limit,
                demo_mode=demo_mode,
            )
            if current_depth == 1 and not self._approved_or_explicit_demo_graph_context(graph_context):
                blocked = True
                scope_depth = current_depth
                break
            related_count = len(graph_context.get("related_edges") or [])
            if related_count >= _MIN_SUFFICIENT_RELATED_EDGES:
                decision = "sufficient"
            elif related_count <= previous_related_count:
                decision = "no_additional_evidence"
            elif current_depth == depth_ceiling:
                decision = "ceiling_reached"
            else:
                decision = "escalating"
            depth_attempts.append({
                "depth": current_depth,
                "depth_ceiling": depth_ceiling,
                "related_edge_count": related_count,
                "decision": decision,
            })
            yield {"event": "depth_attempt", "data": depth_attempts[-1]}
            scope_depth = current_depth
            previous_related_count = related_count
            if decision != "escalating":
                break

        yield {
            "event": "llm_request_body",
            "data": self._llm_request_trace_payload(
                tenant,
                task,
                scope,
                request_body=self._formatted_scoped_reasoning_prompt_request(
                    tenant,
                    task,
                    scope,
                    evidence_paths,
                    scope_depth,
                    scope_limit,
                    scope_edge_limit,
                    graph_context=graph_context,
                ),
            ),
        }
        if blocked:
            tool_calls[0]["status"] = "blocked"
            tool_calls[0]["projection_source"] = graph_context.get("projection_source")
            tool_calls[0]["demo_mode"] = graph_context.get("demo_mode", False)
            tool_calls[0]["degraded_reason"] = graph_context.get("degraded_reason")
            tool_calls[1]["status"] = "skipped"
            output, eval_result = self._missing_projection_block_payload(tenant, graph_context, evidence_paths)
            yield {
                "event": "llm_response_body",
                "data": self._llm_response_trace_payload(
                    tenant,
                    task,
                    scope,
                    response_body={
                        "schema_version": "reasoning_response_v1",
                        "status": "blocked",
                        "structured_answer": None,
                        "projection_source": output["projection_source"],
                        "demo_mode": output["demo_mode"],
                        "degraded_reason": output["degraded_reason"],
                    },
                ),
            }
            run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "blocked", started)
            yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [], "approved": False}}
            return
        datalog_evidence = graph_context.get("datalog_evidence")
        if datalog_evidence:
            yield {"event": "datalog_facts", "data": datalog_evidence}
            evidence_paths.append(datalog_evidence)
        engine = ReasoningEngine(self.instance_repository)
        structured_answer = engine.analyze(tenant, center_node, task.get("question"), depth=scope_depth, limit=scope_limit)
        structured_response = (
            self._reasoning_response_v1(tenant, task, scope, structured_answer, evidence_paths, graph_context)
            if structured_answer
            else None
        )
        yield {
            "event": "llm_response_body",
            "data": self._llm_response_trace_payload(
                tenant,
                task,
                scope,
                response_body=structured_response or {
                    "schema_version": "reasoning_response_v1",
                    "structured_answer": None,
                    "note": "No structured entity profile answer was produced; fallback finding text will be used.",
                },
            ),
        }
        if structured_answer:
            query_plan = [
                "Validate tenant-scoped entity profile task and approved-only graph scope.",
                "Read the selected entity node evidence path from the approved graph.",
                "Materialize the response metrics into controlled evidence for review.",
                "Persist a draft finding from reasoning_response_v1 with evidence limits and next validation questions.",
            ]
            yield {"event": "plan", "data": {"query_plan": query_plan, "task": task}}
            tool_calls.insert(1, {"tool": "entity_profile_aggregate", "tenant_id": tenant.tenant_id, "approved_only": True, "write_scope": "read_only_source_aggregate", "status": "completed"})
            metrics = structured_answer.get("metrics") or {}
            aggregate_evidence, ranking_summary = entity_profile_aggregate_evidence(
                tenant.tenant_id,
                task_key,
                center_node,
                scope_depth,
                metrics,
            )
            evidence_paths.append(aggregate_evidence)
            yield {
                "event": "no_llm_call",
                "data": {
                    "stage": "entity_profile_aggregate",
                    "reason": "No additional LLM request is made. This stage materializes the metrics already used in reasoning_response_v1 into supporting evidence so reviewers can audit degree, related edges, source rows, and ranked paths.",
                    "output_summary": ranking_summary,
                },
            }
            yield {"event": "step", "data": {"tool": "entity_profile_aggregate", "status": "completed", "step": 2, "total": 3}}
            title = structured_response["answer"]["title"]
            conclusion = structured_response["answer"]["conclusion"]
        else:
            title, conclusion = self._edge_or_scoped_finding_text(tenant, task, scope)
        yield {"event": "evidence", "data": {"evidence_paths": evidence_paths}}
        finding = scoped_graph_finding(
            task_key,
            title,
            conclusion,
            evidence_paths,
            structured_answer,
            structured_response,
            now_ms=int(time.time() * 1000),
        )
        output = {
            "summary": conclusion,
            "finding_keys": [finding["canonical_key"]],
            "unsupported_claims": [],
            "draft_only": True,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
            "degraded_reason": graph_context.get("degraded_reason"),
            "depth_exploration": {"attempts": depth_attempts, "final_depth": scope_depth, "depth_ceiling": depth_ceiling},
            **({"structured_answer": structured_answer, "structured_response": structured_response} if structured_answer else {}),
        }
        eval_result = {
            "passed": True,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": [],
            "evidence_path_count": len(evidence_paths),
            "tenant_id": tenant.tenant_id,
            "projection_source": graph_context.get("projection_source"),
            "demo_mode": graph_context.get("demo_mode", False),
        }
        if structured_response and structured_response.get("conclusion_evaluation"):
            eval_result["conclusion_evaluation"] = structured_response["conclusion_evaluation"]
        run = self._record_run(tenant, task, query_plan, tool_calls, evidence_paths, output, eval_result, "completed", started)
        yield {
            "event": "no_llm_call",
            "data": {
                "stage": "propose_finding",
                "reason": "No additional LLM request is made. The draft finding title, conclusion, actions, and boundaries are persisted from reasoning_response_v1 plus the supporting evidence chain.",
                "output_summary": conclusion,
            },
        }
        yield {"event": "step", "data": {"tool": "propose_finding", "status": "completed", "step": 3, "total": 3}}
        finding_row = self._record_finding(tenant, run, finding)
        yield {"event": "finding", "data": {"finding": finding_row}}
        yield {"event": "run_complete", "data": {"tenant": tenant.public_dict(), "task": task, "run": run, "findings": [finding_row], "approved": True}}

    def _llm_request_trace_payload(self, tenant, task, scope=None, request_body=None):
        scope = scope or task.get("scope") or {}
        return {
            "request_body": request_body or {},
            "request_title": "formatted prompt request",
            "tenant_id": tenant.tenant_id,
            "task_key": task.get("task_key") or task.get("key"),
            "center_node": scope.get("center_node"),
            "center_edge": scope.get("center_edge"),
            "depth": scope.get("depth"),
            "node_limit": scope.get("node_limit") or scope.get("limit"),
            "write_boundary": "draft_only",
        }

    def _llm_response_trace_payload(self, tenant, task, scope=None, response_body=None):
        scope = scope or task.get("scope") or {}
        return {
            "response_body": response_body or {},
            "response_title": "structured reasoning response",
            "tenant_id": tenant.tenant_id,
            "task_key": task.get("task_key") or task.get("key"),
            "center_node": scope.get("center_node"),
            "depth": scope.get("depth"),
            "node_limit": scope.get("node_limit") or scope.get("limit"),
            "write_boundary": "draft_only",
        }

    def _reasoning_response_v1(self, tenant, task, scope, structured_answer, evidence_paths, graph_context=None):
        scope = scope or task.get("scope") or {}
        structured_answer = structured_answer or {}
        metrics = structured_answer.get("metrics") or {}
        source_key_profile = metrics.get("source_key_profile") or {}
        graph_context = graph_context or self._scoped_graph_prompt_context(
            tenant,
            scope.get("center_node"),
            int(scope.get("depth") or 1),
            int(scope.get("node_limit") or 200),
            int(scope.get("edge_limit") or scope.get("node_limit") or 200),
        )

        ranked_paths = []
        for idx, path in enumerate(source_key_profile.get("top_paths") or [], start=1):
            ranked_paths.append({
                "rank": idx,
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table"),
                "label_column": path.get("label_col"),
                "evidence_role": "source_key_path_metric",
            })
        if not ranked_paths:
            source_node_labels = {
                node.get("id"): node.get("label")
                for node in graph_context.get("source_backed_related_nodes") or []
                if node.get("id") and node.get("label")
            }
            seen_paths = set()
            for edge in graph_context.get("source_backed_related_edges") or []:
                target = edge.get("target")
                label = source_node_labels.get(target) or str(target or "").split(":", 1)[-1]
                metric = edge.get("metric")
                marker = (label, metric, edge.get("source_table"))
                if not label or marker in seen_paths:
                    continue
                seen_paths.add(marker)
                ranked_paths.append({
                    "rank": len(ranked_paths) + 1,
                    "label": label,
                    "metric": metric,
                    "metric_value": edge.get("metric_value"),
                    "row_count": edge.get("row_count"),
                    "source_table": edge.get("source_table"),
                    "label_column": None,
                    "evidence_role": "source_backed_graph_edge_metric",
                })
        second_hop_paths = []
        for path in source_key_profile.get("second_hop_paths") or []:
            second_hop_paths.append({
                "label": path.get("label"),
                "source_table": path.get("table"),
                "metric": path.get("metric"),
                "top_peers": path.get("top_peers") or [],
                "evidence_role": "shared_path_peer_context",
            })

        graph_degree = graph_context.get("degree") or {}
        display_label = metrics.get("label") or scope.get("center_node")
        for node in graph_context.get("related_nodes") or []:
            if node.get("id") == scope.get("center_node") and node.get("label"):
                display_label = node.get("label")
                break
        evidence_refs = []
        for item in evidence_paths or []:
            evidence_refs.append({
                "kind": item.get("kind"),
                "label": item.get("label") or item.get("title"),
                "summary": item.get("summary"),
                "source_ref": item.get("source_ref"),
                "url": item.get("url"),
            })
        language = scope.get("language")
        plain_conclusion = self._plain_reasoning_conclusion(
            task.get("question"),
            display_label,
            structured_answer.get("profile_summary") or "",
            ranked_paths,
            second_hop_paths,
            graph_degree,
            language=language,
        )
        plain_title = self._plain_reasoning_title(
            task.get("question"),
            display_label,
            ranked_paths,
            second_hop_paths,
            language=language,
        )
        traversal_analysis = self._joint_graph_traversal_analysis(
            display_label,
            scope.get("center_node"),
            graph_context,
            metrics,
            ranked_paths,
            second_hop_paths,
        )
        edge_target_reasoning = self._edge_target_reasoning_units(
            display_label,
            scope.get("center_node"),
            graph_context,
            metrics,
            evidence_refs,
        )
        deep_conclusion = self._business_conclusion_from_traversal(
            display_label,
            metrics,
            traversal_analysis,
            plain_conclusion,
            edge_target_reasoning,
            language=language,
        )
        conclusion_eval = self._evaluate_reasoning_conclusion(
            deep_conclusion,
            traversal_analysis,
            evidence_refs,
            structured_answer,
            edge_target_reasoning,
        )

        return {
            "schema_version": "reasoning_response_v1",
            "answer": {
                "title": plain_title or structured_answer.get("title") or task.get("question") or ("范围化图谱推理" if wants_zh_output(language) else "Scoped graph reasoning"),
                "plain_conclusion": deep_conclusion.get("plain_conclusion") or plain_conclusion,
                "conclusion": deep_conclusion.get("conclusion") or plain_conclusion,
                "detailed_conclusion": deep_conclusion.get("detailed_conclusion") or structured_answer.get("profile_summary") or "",
                "confidence": conclusion_eval.get("confidence", 0.78),
                "status": "draft",
            },
            "scope": {
                "tenant_id": tenant.tenant_id,
                "task_key": task.get("task_key") or task.get("key") or task.get("canonical_key"),
                "question": task.get("question"),
                "center_node": scope.get("center_node"),
                "depth": int(scope.get("depth") or 1),
                "node_limit": int(scope.get("node_limit") or 200),
                "edge_limit": int(scope.get("edge_limit") or scope.get("node_limit") or 200),
                "approved_only": True,
            },
            "graph_context": {
                "center_node": graph_context.get("center_node") or scope.get("center_node"),
                "degree": {
                    "visible_graph_center": graph_degree.get("visible_graph_center", graph_degree.get("center")),
                    "center": graph_degree.get("center"),
                    "by_link": graph_degree.get("by_link") or {},
                    "neighbor_type_counts": graph_degree.get("neighbor_type_counts") or {},
                    "source_key_row_degree": graph_degree.get("source_key_row_degree"),
                    "source_key_top_path_count": graph_degree.get("source_key_top_path_count"),
                },
                "related_nodes": graph_context.get("related_nodes") or [],
                "related_edges": graph_context.get("related_edges") or [],
                "source_backed_related_nodes": graph_context.get("source_backed_related_nodes") or [],
                "source_backed_related_edges": graph_context.get("source_backed_related_edges") or [],
                "truncated": graph_context.get("truncated") or {},
            },
            "key_facts": structured_answer.get("key_facts") or [],
            "ranked_paths": ranked_paths,
            "second_hop_paths": second_hop_paths,
            "traversal_analysis": traversal_analysis,
            "edge_target_reasoning": edge_target_reasoning,
            "business_interpretation": structured_answer.get("business_interpretation") or [],
            "conclusion_evaluation": conclusion_eval,
            "evidence": evidence_refs,
            "metrics": metrics,
            "limits": structured_answer.get("evidence_limits") or [],
            "next_questions": structured_answer.get("next_questions") or [],
            "actions": [review_graph_scope_action()],
            "write_boundary": {
                "status": "draft_only",
                "approved_finding_write": "review_gate_required",
                "must_not_write": ["canonical_ontology", "formal_graph"],
            },
        }

    def _joint_graph_traversal_analysis(self, label, center_node, graph_context, metrics, ranked_paths, second_hop_paths):
        nodes = graph_context.get("related_nodes") or []
        edges = graph_context.get("related_edges") or []
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency = {}
        relation_counts = {}
        relation_neighbor_types = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            relation = edge.get("label") or edge.get("link_key") or "relation"
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            for node_id, other_id in ((source, target), (target, source)):
                other_type = (nodes_by_id.get(other_id) or {}).get("type") or "unknown"
                relation_neighbor_types.setdefault(relation, {})
                relation_neighbor_types[relation][other_type] = relation_neighbor_types[relation].get(other_type, 0) + 1
                adjacency.setdefault(node_id, []).append({**edge, "_other": other_id, "_relation": relation})

        bfs_layers = []
        visited = {center_node} if center_node else set()
        frontier = {center_node} if center_node else set()
        for depth in range(1, 4):
            next_frontier = set()
            layer_relations = {}
            layer_types = {}
            sample_nodes = []
            for node_id in frontier:
                for edge in adjacency.get(node_id, []):
                    other = edge.get("_other")
                    if not other or other in visited:
                        continue
                    visited.add(other)
                    next_frontier.add(other)
                    relation = edge.get("_relation")
                    layer_relations[relation] = layer_relations.get(relation, 0) + 1
                    other_node = nodes_by_id.get(other) or {"id": other, "label": other, "type": "unknown"}
                    other_type = other_node.get("type") or "unknown"
                    layer_types[other_type] = layer_types.get(other_type, 0) + 1
                    if len(sample_nodes) < 8:
                        sample_nodes.append({
                            "id": other,
                            "label": other_node.get("label") or other,
                            "type": other_type,
                            "via_relation": relation,
                        })
            if not next_frontier:
                break
            bfs_layers.append({
                "depth": depth,
                "node_count": len(next_frontier),
                "relation_counts": dict(sorted(layer_relations.items())),
                "node_type_counts": dict(sorted(layer_types.items())),
                "sample_nodes": sample_nodes,
            })
            frontier = next_frontier

        dfs_paths = []
        max_paths = 8

        def dfs(node_id, path, seen, remaining):
            if len(dfs_paths) >= max_paths or remaining <= 0:
                return
            def onward_count(item):
                other = item.get("_other")
                if not other:
                    return 0
                return sum(
                    1
                    for next_edge in adjacency.get(other, [])
                    if next_edge.get("_other") not in seen and next_edge.get("_other") != node_id
                )

            candidates = sorted(
                adjacency.get(node_id, []),
                key=lambda item: (
                    -onward_count(item),
                    item.get("_relation") in {"Country Chokepoint Dependency", "relation"},
                    item.get("_relation") or "",
                    item.get("_other") or "",
                ),
            )
            for edge in candidates[:12]:
                other = edge.get("_other")
                if not other or other in seen:
                    continue
                other_node = nodes_by_id.get(other) or {"id": other, "label": other, "type": "unknown"}
                step = {
                    "from": node_id,
                    "relation": edge.get("_relation"),
                    "to": other,
                    "to_label": other_node.get("label") or other,
                    "to_type": other_node.get("type") or "unknown",
                }
                next_path = [*path, step]
                if len(next_path) >= 2 or onward_count(edge) == 0:
                    dfs_paths.append(next_path)
                dfs(other, next_path, {*seen, other}, remaining - 1)
                if len(dfs_paths) >= max_paths:
                    break

        if center_node:
            dfs(center_node, [], {center_node}, 3)

        source_profile = (metrics or {}).get("source_key_profile") or {}
        top_source_metrics = [
            {
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("source_table") or path.get("table"),
            }
            for path in ranked_paths[:5]
        ]
        relation_summary = [
            {
                "relation": relation,
                "edge_count": count,
                "neighbor_types": relation_neighbor_types.get(relation) or {},
            }
            for relation, count in sorted(relation_counts.items(), key=lambda item: (-item[1], item[0]))[:8]
        ]
        return {
            "strategy": "joint_bfs_dfs_approved_graph_reasoning_v1",
            "center": {"id": center_node, "label": label},
            "max_observed_depth": bfs_layers[-1]["depth"] if bfs_layers else 0,
            "breadth": {
                "visited_node_count": len(visited),
                "relation_type_count": len(relation_counts),
                "layers": bfs_layers,
            },
            "depth_paths": [
                {
                    "path_length": len(path),
                    "steps": path,
                    "path_label": " -> ".join(
                        [label, *[step.get("to_label") or step.get("to") for step in path]]
                    ),
                }
                for path in dfs_paths
            ],
            "relation_summary": relation_summary,
            "source_metric_summary": {
                "total_rows": source_profile.get("total_key_rows"),
                "related_table_count": len(source_profile.get("related_tables") or []),
                "top_metrics": top_source_metrics,
            },
            "shared_peer_paths": second_hop_paths[:5],
        }

    def _edge_target_reasoning_units(self, label, center_node, graph_context, metrics, evidence_refs):
        retrieval_context = graph_context.get("retrieval_context") or {}
        related_nodes = graph_context.get("related_nodes") or []
        related_edges = graph_context.get("related_edges") or []
        retrieval_nodes = retrieval_context.get("nodes") or []
        retrieval_edges = retrieval_context.get("edges") or []
        semantic_items = retrieval_context.get("semantic_items") or []
        prior_findings = [
            item for item in evidence_refs or []
            if str(item.get("kind") or "").lower() in {"prior_finding", "finding", "draft_finding"}
            or item.get("label")
        ]

        nodes_by_id = {}
        for node in [*related_nodes, *retrieval_nodes]:
            node_id = node.get("id")
            if node_id:
                nodes_by_id.setdefault(node_id, {}).update(
                    {key: value for key, value in node.items() if value not in (None, "", [])}
                )

        edges_by_key = {}
        for edge in [*retrieval_edges, *related_edges]:
            source = edge.get("source")
            target = edge.get("target")
            relation = edge.get("relation") or edge.get("label") or edge.get("link_key") or "relation"
            if not source or not target:
                continue
            key = (source, relation, target)
            edges_by_key.setdefault(key, {}).update(
                {field: value for field, value in edge.items() if value not in (None, "", [])}
            )
            if edge.get("id"):
                edges_by_key[key].setdefault("id", edge.get("id"))
            edges_by_key[key].setdefault("relation", relation)

        def text_blob(*items):
            return " ".join(str(item or "").lower() for item in items if item not in (None, "", []))

        def numeric_metrics(properties):
            selected = {}
            priority_terms = (
                "risk", "trade", "piracy", "geopolitical", "canal", "share",
                "v_", "q_", "impact", "likelihood", "severity", "cost", "flow",
            )
            for key, value in (properties or {}).items():
                if isinstance(value, bool):
                    continue
                if isinstance(value, (int, float)):
                    lower_key = str(key).lower()
                    if any(term in lower_key for term in priority_terms):
                        selected[key] = value
                if len(selected) >= 12:
                    break
            return selected

        def attached_semantic(source_label, relation, target_label, source_url):
            markers = text_blob(source_label, relation, target_label).split()
            result = []
            for item in semantic_items:
                item_blob = text_blob(
                    item.get("label"),
                    item.get("summary"),
                    item.get("evidence_quote"),
                    item.get("subject"),
                    item.get("target"),
                    item.get("metric_key"),
                )
                source_match = bool(source_url and item.get("source_url") == source_url)
                label_match = any(marker and len(marker) >= 3 and marker in item_blob for marker in markers)
                if source_match or label_match:
                    result.append({
                        "element_key": item.get("element_key"),
                        "element_type": item.get("element_type"),
                        "label": item.get("label"),
                        "summary": item.get("summary") or item.get("evidence_quote"),
                        "metric_key": item.get("metric_key"),
                        "status": item.get("status"),
                    })
                if len(result) >= 3:
                    break
            return result

        def attached_findings(source_label, target_label):
            markers = [str(source_label or "").lower(), str(target_label or "").lower()]
            result = []
            for finding in prior_findings:
                blob = text_blob(finding.get("label"), finding.get("summary"), finding.get("source_ref"))
                if any(marker and len(marker) >= 3 and marker in blob for marker in markers):
                    result.append({
                        "label": finding.get("label"),
                        "summary": finding.get("summary"),
                        "source_ref": finding.get("source_ref"),
                    })
                if len(result) >= 2:
                    break
            return result

        def local_business_reason(relation, target_label, target_type, edge_metrics, semantic_count, finding_count):
            relation_l = str(relation or "").lower()
            target = target_label or target_type or "the target node"
            if "systemic risk" in relation_l:
                return (
                    f"{target} is an exposure channel for systemic chokepoint risk; edge metrics should be read as local loss, disruption, or trade-at-risk signals."
                )
            if "dependency" in relation_l:
                return (
                    f"{target} depends on the chokepoint, so this edge contributes demand-side exposure and should influence country or counterparty monitoring priority."
                )
            if "deploy" in relation_l or "mine" in relation_l:
                return (
                    f"{target} represents a disruption trigger or capability near the chokepoint, making the edge relevant to event escalation and scenario thresholds."
                )
            if relation_l in {"connects", "positioned_between", "situated_at_entrance_of", "provides_access_to"}:
                return (
                    f"{target} is part of the route-propagation structure; disruption can move from the center into adjacent maritime geography rather than staying local."
                )
            if edge_metrics:
                return f"{target} carries quantified local evidence, so the edge should be weighted in the combined risk conclusion."
            if semantic_count or finding_count:
                return f"{target} has attached semantic or finding context, so the edge should be reviewed as more than a topology link."
            return f"{target} contributes an approved relation that supports traversal but needs review before operational use."

        units = []
        relation_seen = {}
        for edge in edges_by_key.values():
            source = edge.get("source")
            target = edge.get("target")
            relation = edge.get("relation") or edge.get("label") or edge.get("link_key") or "relation"
            if center_node and center_node not in {source, target} and len(units) >= 12:
                continue
            relation_seen[relation] = relation_seen.get(relation, 0) + 1
            if relation_seen[relation] > 6 and len(units) >= 18:
                continue
            source_node = nodes_by_id.get(source) or {}
            target_node = nodes_by_id.get(target) or {}
            target_label = target_node.get("label") or edge.get("target_label") or target
            source_label = source_node.get("label") or edge.get("source_label") or source
            properties = edge.get("properties") or {
                key: value for key, value in edge.items()
                if key not in {
                    "id", "source", "target", "label", "relation", "link_key", "status",
                    "projection_source", "source_label", "target_label",
                }
            }
            edge_metrics = numeric_metrics(properties)
            semantic = attached_semantic(
                source_label,
                relation,
                target_label,
                edge.get("source_url") or properties.get("source_url"),
            )
            findings = attached_findings(source_label, target_label)
            units.append({
                "unit_type": "edge_target",
                "edge_id": edge.get("id"),
                "source": source,
                "source_label": source_label,
                "relation": relation,
                "target": target,
                "target_label": target_label,
                "target_type": target_node.get("type"),
                "local_metrics": edge_metrics,
                "attached_semantic_items": semantic,
                "attached_findings": findings,
                "local_reasoning": local_business_reason(
                    relation,
                    target_label,
                    target_node.get("type"),
                    edge_metrics,
                    len(semantic),
                    len(findings),
                ),
                "evaluation": {
                    "has_edge": True,
                    "has_target_node": bool(target_node),
                    "has_local_metrics": bool(edge_metrics),
                    "has_semantic_or_finding_context": bool(semantic or findings),
                    "has_business_reasoning": True,
                },
            })
            if len(units) >= 24:
                break

        metric_units = sum(1 for unit in units if unit.get("local_metrics"))
        contextual_units = sum(
            1 for unit in units
            if unit.get("attached_semantic_items") or unit.get("attached_findings")
        )
        relation_types = sorted({unit.get("relation") for unit in units if unit.get("relation")})
        source_metric_profile = (metrics or {}).get("source_key_profile") or graph_context.get("source_key_metrics") or {}
        top_source_metrics = [
            {
                "label": path.get("label"),
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table") or path.get("source_table"),
            }
            for path in (source_metric_profile.get("top_paths") or [])[:8]
        ]
        return {
            "strategy": "per_edge_target_then_aggregate_reasoning_v1",
            "center": {"id": center_node, "label": label},
            "unit_count": len(units),
            "units": units,
            "summary": {
                "relation_type_count": len(relation_types),
                "relations": relation_types[:12],
                "units_with_local_metrics": metric_units,
                "units_with_semantic_or_finding_context": contextual_units,
                "source_metric_count": len(top_source_metrics),
                "top_source_metrics": top_source_metrics,
            },
        }

    def _business_conclusion_from_traversal(self, label, metrics, traversal_analysis, fallback, edge_target_reasoning=None, language=None):
        wants_zh = wants_zh_output(language)
        source_summary = traversal_analysis.get("source_metric_summary") or {}
        relation_summary = traversal_analysis.get("relation_summary") or []
        edge_target_reasoning = edge_target_reasoning or {}
        edge_summary = edge_target_reasoning.get("summary") or {}
        breadth = traversal_analysis.get("breadth") or {}
        neighbor_types = (metrics or {}).get("neighbor_types") or {}
        source_profile = ((metrics or {}).get("source_key_profile") or {})
        top_metrics = source_summary.get("top_metrics") or []
        metric_names = {str(item.get("metric") or "").lower() for item in top_metrics}
        country_count = int(neighbor_types.get("Country") or 0)
        relation_names = [item.get("relation") for item in relation_summary[:3] if item.get("relation")]
        relation_text = ("、".join(relation_names) if wants_zh else ", ".join(relation_names)) or ("已批准的关联关系" if wants_zh else "approved relationships")
        has_trade_exposure = any("trade_at_risk" in metric for metric in metric_names)
        has_flow_concentration = any(metric in {"v_canal", "q_canal"} for metric in metric_names)
        related_tables = source_profile.get("related_tables") or []
        is_maritime = any(str(item.get("table") or "").startswith("maritime_") for item in related_tables)
        if is_maritime or has_trade_exposure or has_flow_concentration:
            drivers = []
            if has_trade_exposure:
                drivers.append("贸易风险敞口" if wants_zh else "trade-at-risk exposure")
            if has_flow_concentration:
                drivers.append("运河流量集中度" if wants_zh else "canal-flow concentration")
            if country_count:
                drivers.append(f"{country_count} 个国家依赖关联" if wants_zh else f"{country_count} country dependency links")
            if edge_target_reasoning.get("unit_count"):
                drivers.append(
                    f"{edge_target_reasoning.get('unit_count')} 个边-目标本地推理单元" if wants_zh
                    else f"{edge_target_reasoning.get('unit_count')} edge-target local reasoning units"
                )
            driver_text = ("、".join(drivers) if wants_zh else ", ".join(drivers)) or relation_text
            unit_metric_text = ""
            if edge_summary.get("units_with_local_metrics"):
                unit_metric_text = (
                    f"另有 {edge_summary.get('units_with_local_metrics')} 个本地边/节点单元附带指标数据。" if wants_zh
                    else f" {edge_summary.get('units_with_local_metrics')} local edge/node unit(s) also carry attached metrics."
                )
            if wants_zh:
                plain = (
                    f"{label} 应被列为系统性海运风险优先项，因为多条已批准证据链共同指向 {driver_text}。"
                    "业务风险并非图连通性本身，而是边级暴露、目标节点依赖和路径传播信号可能演变为贸易流中断、"
                    "海运成本压力、绕航约束以及跨国敞口，应触发监控升级。"
                )
                detail = (
                    f"广度优先遍历覆盖 {breadth.get('visited_node_count', 0)} 个已批准节点，涉及 "
                    f"{breadth.get('relation_type_count', 0)} 种关系类型；深度优先路径显示中心节点通过 "
                    f"{relation_text} 建立连接。受控来源指标新增 {source_summary.get('total_rows')} 条匹配行，"
                    f"涉及 {source_summary.get('related_table_count')} 张表。逐边/目标推理审查了 "
                    f"{edge_target_reasoning.get('unit_count', 0)} 个本地单元，涉及 {edge_summary.get('relation_type_count', 0)} 种关系类型。"
                    f"{unit_metric_text}建议的业务响应是在将此发现作为操作指引之前，先复核情景阈值、"
                    "备选路径假设和监控名单升级。"
                )
            else:
                plain = (
                    f"{label} is a systemic maritime risk priority because multiple approved evidence channels jointly point to {driver_text}. "
                    "The business risk is not the graph connectivity itself; it is that edge-level exposure, target-node dependency, and route-propagation signals can propagate into trade-flow interruption, freight-cost pressure, rerouting constraints, and cross-country exposure that should trigger monitoring escalation."
                )
                detail = (
                    f"Breadth-first traversal visits {breadth.get('visited_node_count', 0)} approved nodes across "
                    f"{breadth.get('relation_type_count', 0)} relation type(s), while depth-first paths show how the center connects through "
                    f"{relation_text}. Controlled source metrics add {source_summary.get('total_rows')} matching rows across "
                    f"{source_summary.get('related_table_count')} table(s). Per-edge/target reasoning reviewed "
                    f"{edge_target_reasoning.get('unit_count', 0)} local unit(s) across {edge_summary.get('relation_type_count', 0)} relation type(s)."
                    f"{unit_metric_text} The recommended business response is to review scenario thresholds, "
                    "alternate-route assumptions, and watchlist escalation before treating the finding as operational guidance."
                )
            return {"plain_conclusion": plain, "conclusion": plain, "detailed_conclusion": detail}
        if traversal_analysis.get("max_observed_depth", 0) >= 2:
            if wants_zh:
                plain = (
                    f"{label} 在已批准图谱中存在多跳业务风险敞口：其直接关联进一步连接到二级关联方，"
                    "审核应聚焦于风险传播路径，而非单一节点画像。"
                )
            else:
                plain = (
                    f"{label} has multi-hop business exposure in the approved graph: its direct relationships connect into second-order counterparties, "
                    "so review should focus on propagation paths rather than a single-node profile."
                )
            return {"plain_conclusion": plain, "conclusion": plain, "detailed_conclusion": fallback}
        return {"plain_conclusion": fallback, "conclusion": fallback, "detailed_conclusion": fallback}

    def _evaluate_reasoning_conclusion(self, conclusion, traversal_analysis, evidence_refs, structured_answer, edge_target_reasoning=None):
        breadth = traversal_analysis.get("breadth") or {}
        source_summary = traversal_analysis.get("source_metric_summary") or {}
        edge_target_reasoning = edge_target_reasoning or {}
        edge_summary = edge_target_reasoning.get("summary") or {}
        checks = {
            "uses_breadth_traversal": bool((breadth.get("layers") or [])),
            "uses_depth_paths": any(
                int(path.get("path_length") or 0) >= 2
                for path in traversal_analysis.get("depth_paths") or []
            ),
            "uses_multiple_relations_or_sources": (
                int(breadth.get("relation_type_count") or 0) >= 2
                or int(source_summary.get("related_table_count") or 0) >= 2
            ),
            "has_source_metrics": bool(source_summary.get("top_metrics")),
            "uses_edge_target_units": bool(edge_target_reasoning.get("units")),
            "uses_attached_edge_or_source_metrics": bool(
                edge_summary.get("units_with_local_metrics")
                or edge_summary.get("top_source_metrics")
                or source_summary.get("top_metrics")
            ),
            "uses_attached_findings_or_semantic_context": bool(edge_summary.get("units_with_semantic_or_finding_context")),
            "has_business_actionability": any(
                term in str(conclusion.get("conclusion") or "").lower()
                for term in ("monitoring", "scenario", "rerouting", "operational", "trade-flow", "cost", "escalation", "exposure")
            ),
            "states_review_boundary": bool((structured_answer or {}).get("evidence_limits")) or bool(evidence_refs),
        }
        score = round(sum(1 for value in checks.values() if value) / max(len(checks), 1), 4)
        return {
            "schema_version": "reasoning_conclusion_eval_v1",
            "score": score,
            "passed": score >= 0.75,
            "confidence": 0.82 if score >= 0.85 else 0.78 if score >= 0.75 else 0.62,
            "checks": checks,
            "limits": [
                "Evaluation checks reasoning shape and evidence linkage; it does not approve the finding.",
                "Business conclusion remains draft-only until human review.",
            ],
        }

    def _plain_reasoning_title(self, question, label, ranked_paths, second_hop_paths, language=None):
        return plain_reasoning_title(question, label, ranked_paths, second_hop_paths, language=language)

    def _plain_reasoning_conclusion(self, question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree, language=None):
        return plain_reasoning_conclusion(question, label, detailed_conclusion, ranked_paths, second_hop_paths, graph_degree, language=language)

    def _formatted_scoped_reasoning_prompt_request(self, tenant, task, scope, evidence_paths, scope_depth, scope_limit, scope_edge_limit, graph_context=None):
        question = task.get("question") or ""
        center_node = scope.get("center_node")
        graph_context = graph_context or self._scoped_graph_prompt_context(tenant, center_node, scope_depth, scope_limit, scope_edge_limit)
        evidence_text = json.dumps(evidence_paths, ensure_ascii=False, indent=2)
        graph_context_text = json.dumps(graph_context, ensure_ascii=False, indent=2)
        system_prompt = (
            "You are Aletheia's tenant-scoped graph reasoning agent. "
            "Use only approved graph evidence and controlled source aggregations. "
            "Do not ingest new data, approve findings, or write canonical ontology/formal graph data. "
            "Return a draft finding with evidence limits and review boundaries."
        )
        user_prompt = (
            f"Tenant: {tenant.tenant_id}\n"
            f"Question: {question}\n"
            f"Center node: {center_node or '—'}\n"
            f"Depth: {scope_depth}\n"
            f"Node limit: {scope_limit}\n"
            "Evidence paths:\n"
            f"{evidence_text}\n\n"
            "Graph scope context:\n"
            f"{graph_context_text}\n\n"
            "Expected output:\n"
            "- answer.plain_conclusion: 1-2 plain-language sentences. Explain impact first; mention only top paths and key counterparties. Do not list long metric rows here.\n"
            "- answer.detailed_conclusion: longer reasoning narrative when needed\n"
            "- ranked_paths, second_hop_paths, graph_context.degree, and key_facts carry the numbers, degree, related node data, and edge data\n"
            "- limitations / counter-evidence\n"
            "- draft-only write boundary"
        )
        return {
            "provider": "internal_reasoning_engine",
            "model": "ReasoningEngine.analyze",
            "prompt_version": "graph_scope_reasoning_v1",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "parameters": {
                "tenant_id": tenant.tenant_id,
                "task_key": task.get("task_key") or task.get("key"),
                "center_node": center_node,
                "depth": scope_depth,
                "node_limit": scope_limit,
                "edge_limit": scope_edge_limit,
                "approved_only": True,
            },
            "response_contract": {
                "schema_version": "reasoning_response_v1",
                "status": "draft",
                "answer": {
                    "plain_conclusion": "1-2 human-readable sentences, not an evidence dump",
                    "detailed_conclusion": "long-form support text",
                },
                "required_sections": ["graph_context", "key_facts", "ranked_paths", "second_hop_paths", "limits", "next_questions", "write_boundary"],
                "write_boundary": "draft_only",
                "must_not_write": ["canonical_ontology", "formal_graph"],
            },
        }

    def _explicit_demo_mode(self, scope):
        scope = scope or {}
        return bool(scope.get("demo_mode") is True or scope.get("allow_demo_mode") is True or scope.get("allow_demo_fallback") is True)

    def _reasoning_projection_sources(self, *contexts):
        sources = []
        for context in contexts:
            scope = (context or {}).get("scope") or {}
            for source in (scope.get("projection_source"), (context or {}).get("projection_source")):
                if not source:
                    continue
                for part in str(source).split("+"):
                    part = part.strip()
                    if part and part not in sources:
                        sources.append(part)
        return "+".join(sources) if sources else "none"

    def _approved_or_explicit_demo_graph_context(self, graph_context):
        graph_context = graph_context or {}
        return bool(graph_context.get("approved") or graph_context.get("demo_mode"))

    def _missing_projection_block_payload(self, tenant, graph_context, evidence_paths):
        graph_context = graph_context or {}
        unsupported_claims = ["missing approved graph projection"]
        output = {
            "summary": "Scoped graph reasoning blocked because no approved graph projection is available.",
            "unsupported_claims": unsupported_claims,
            "draft_only": True,
            "projection_source": graph_context.get("projection_source") or "none",
            "demo_mode": bool(graph_context.get("demo_mode")),
            "degraded_reason": graph_context.get("degraded_reason") or self._missing_projection_reason(),
        }
        eval_result = {
            "passed": False,
            "approved_only": True,
            "draft_only": True,
            "unsupported_claims": unsupported_claims,
            "evidence_path_count": len(evidence_paths or []),
            "tenant_id": tenant.tenant_id,
            "projection_source": output["projection_source"],
            "demo_mode": output["demo_mode"],
            "degraded_reason": output["degraded_reason"],
        }
        return output, eval_result

    def _missing_projection_reason(self):
        return "No reviewed SchemaGraphModelingAgent projection. Import data and run schema-to-graph modeling first."

    def _scoped_graph_prompt_context(self, tenant, center_node, depth, node_limit, edge_limit, demo_mode=False):
        demo_mode = bool(demo_mode)
        if not center_node or ":" not in str(center_node):
            projection_source = "explicit_demo_mode" if demo_mode else "none"
            return {
                "center_node": center_node,
                "nodes": [],
                "edges": [],
                "retrieval_mode": "explicit_demo_mode" if demo_mode else "degraded_no_approved_projection",
                "degree": {"center": 0},
                "approved": False,
                "projection_source": projection_source,
                "demo_mode": demo_mode,
                "degraded_reason": None if demo_mode else self._missing_projection_reason(),
            }
        object_type, instance_id = str(center_node).split(":", 1)
        node_limit = max(1, min(int(node_limit or 200), 300))
        edge_limit = max(1, min(int(edge_limit or node_limit), 300))
        depth = max(1, min(int(depth or 1), 3))
        fetch_limit = max(node_limit, edge_limit, 300)
        local_rag_context = self.instance_repository.local_rag_context(
            tenant,
            object_type,
            instance_id,
            question=None,
            depth=depth,
            limit=fetch_limit,
        ) or {}
        graph = self.instance_repository.full_graph(tenant, object_type, instance_id, limit=fetch_limit) or {}
        approved = bool(local_rag_context.get("approved") or graph.get("approved"))
        projection_source = self._reasoning_projection_sources(local_rag_context, graph)
        if demo_mode and not approved:
            projection_source = "explicit_demo_mode"
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        if local_rag_context.get("approved"):
            local_nodes = local_rag_context.get("nodes") or []
            local_edges = local_rag_context.get("edges") or []
            local_node_ids = {node.get("id") for node in local_nodes if node.get("id")}
            full_nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
            for node in local_nodes:
                if node.get("id") and node.get("id") not in full_nodes_by_id:
                    nodes.append(node)
                    full_nodes_by_id[node.get("id")] = node
            # Graph-native tenants' edges never carry a synthetic "id" field
            # (full_graph()/local_rag_context() both hand back plain
            # {source, target, label, ...} dicts) -- deduping on
            # edge.get("id") alone means it's always None/falsy on both
            # sides, so this merge would silently never add any local_edge.
            # Use (source, target, label) as a structural fallback key so
            # local_rag_context's center-scoped edges actually make it into
            # `edges` (which everything below -- adjacency, degree,
            # scoped_edges, and _datalog_transitive_evidence -- reads from).
            edge_key = lambda e: e.get("id") or (e.get("source"), e.get("target"), e.get("label") or e.get("relation"))
            edge_keys = {edge_key(edge) for edge in edges}
            for edge in local_edges:
                key = edge_key(edge)
                if key not in edge_keys:
                    edges.append(edge)
                    edge_keys.add(key)
            if local_node_ids:
                nodes = sorted(nodes, key=lambda node: 0 if node.get("id") in local_node_ids else 1)
        datalog_evidence = (
            self._datalog_transitive_evidence(nodes, edges, center_node)
            if (approved or demo_mode) else None
        )
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if not source or not target:
                continue
            adjacency.setdefault(source, []).append(edge)
            adjacency.setdefault(target, []).append(edge)

        # BFS/adjacency below is keyed on bare vertex ids (adjacency's keys
        # come from edge.get("source")/edge.get("target"), which are never
        # "Type:"-prefixed) -- use instance_id (the bare half of center_node,
        # already split out above), not center_node itself, or every lookup
        # here silently misses and this whole block degrades to a no-op
        # (visited={center_node} that nothing ever matches, center_edges
        # always empty, degree.center always 0).
        visited = {instance_id}
        frontier = {instance_id}
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                for edge in adjacency.get(node_id, []):
                    other = edge.get("target") if edge.get("source") == node_id else edge.get("source")
                    if other and other not in visited:
                        visited.add(other)
                        next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        center_edges = self._diversify_edges_by_relation(adjacency.get(instance_id, []))
        center_neighbor_ids = []
        for edge in center_edges:
            other = edge.get("target") if edge.get("source") == instance_id else edge.get("source")
            if other:
                center_neighbor_ids.append(other)
        ordered_node_ids = [instance_id] + center_neighbor_ids + [node_id for node_id in visited if node_id not in {instance_id, *center_neighbor_ids}]
        seen_ordered_nodes = set()
        scoped_nodes = []
        for node_id in ordered_node_ids:
            if node_id in seen_ordered_nodes or node_id not in nodes_by_id:
                continue
            seen_ordered_nodes.add(node_id)
            scoped_nodes.append(nodes_by_id[node_id])
        center_edge_budget = edge_limit
        if depth > 1 and len(center_edges) > 20:
            center_edge_budget = max(20, min(len(center_edges), edge_limit // 2))
        selected_center_edges = center_edges[:center_edge_budget]
        center_edge_ids = {edge.get("id") for edge in selected_center_edges}
        remaining_edge_budget = max(edge_limit - len(selected_center_edges), 0)
        secondary_edges = [
            edge for edge in edges
            if edge.get("id") not in center_edge_ids and edge.get("source") in visited and edge.get("target") in visited
        ][:remaining_edge_budget]
        scoped_edges = [
            *selected_center_edges,
            *secondary_edges,
        ]
        degree_by_link = {}
        neighbor_type_counts = {}
        for edge in center_edges:
            degree_by_link[edge.get("label") or edge.get("link_key") or "edge"] = degree_by_link.get(edge.get("label") or edge.get("link_key") or "edge", 0) + 1
            other = edge.get("target") if edge.get("source") == instance_id else edge.get("source")
            node_type = (nodes_by_id.get(other) or {}).get("type") or "unknown"
            neighbor_type_counts[node_type] = neighbor_type_counts.get(node_type, 0) + 1

        # Source-key profiling (SQL-join aggregation across tables sharing
        # a source key) was retired along with the rest of reasoning_engine's
        # SQL retrieval core -- no graph-native replacement exists yet, so
        # this stays empty rather than calling a method that no longer exists.
        source_key_profile = None

        top_source_paths = (source_key_profile or {}).get("top_paths") or []
        source_backed_related_nodes = [
            {
                "id": f"SourcePath:{path.get('label')}",
                "type": "SourcePath",
                "label": path.get("label"),
                "source_table": path.get("table"),
                "source_pk": f"{(source_key_profile or {}).get('center_key_col', 'key')}={instance_id}; {path.get('label_col') or 'label'}={path.get('label')}",
            }
            for path in top_source_paths
        ]
        source_backed_related_edges = [
            {
                "source": center_node,
                "target": f"SourcePath:{path.get('label')}",
                "label": "source path metric",
                "metric": path.get("metric"),
                "metric_value": path.get("metric_value"),
                "row_count": path.get("row_count"),
                "source_table": path.get("table"),
                "provenance": "source-key metric aggregation",
            }
            for path in top_source_paths
        ]

        def compact_node(node):
            return {
                "id": node.get("id"),
                "type": node.get("type"),
                "label": node.get("label"),
                "source_table": node.get("source_table"),
                "source_pk": node.get("source_pk"),
                "ontology_artifact": node.get("ontology_artifact"),
                "status": node.get("status"),
            }

        def compact_edge(edge):
            properties = edge.get("properties") or {}
            return {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "target": edge.get("target"),
                "label": edge.get("label"),
                "link_key": edge.get("link_key"),
                "status": edge.get("status"),
                "projection_source": edge.get("projection_source"),
                "source_url": edge.get("source_url") or properties.get("source_url"),
                "properties": {
                    key: value for key, value in properties.items()
                    if key not in {"evidence_refs", "evidence_quote"} and value not in (None, "", [])
                },
            }

        retrieval_context = None
        if local_rag_context.get("approved"):
            retrieval_context = {
                "mode": local_rag_context.get("retrieval_mode"),
                "center": local_rag_context.get("center"),
                "nodes": (local_rag_context.get("nodes") or [])[:node_limit],
                "edges": (local_rag_context.get("edges") or [])[:edge_limit],
                "semantic_items": local_rag_context.get("semantic_items") or [],
                "evidence": local_rag_context.get("evidence") or [],
                "context_text": local_rag_context.get("context_text") or "",
                "scope": local_rag_context.get("scope") or {},
                "eval": local_rag_context.get("eval") or {},
            }

        if retrieval_context:
            retrieval_mode = "local_graph_context"
        elif approved:
            retrieval_mode = "approved_graph_scope"
        elif demo_mode:
            retrieval_mode = "explicit_demo_mode"
        else:
            retrieval_mode = "degraded_no_approved_projection"

        return {
            "center_node": center_node,
            "retrieval_mode": retrieval_mode,
            "approved": approved,
            "projection_source": projection_source,
            "demo_mode": demo_mode,
            "degraded_reason": None if approved or demo_mode else self._missing_projection_reason(),
            "depth": depth,
            "node_limit": node_limit,
            "edge_limit": edge_limit,
            "degree": {
                "center": len(center_edges),
                "visible_graph_center": len(center_edges),
                "by_link": degree_by_link,
                "neighbor_type_counts": neighbor_type_counts,
                "source_key_row_degree": (source_key_profile or {}).get("total_key_rows"),
                "source_key_top_path_count": len(top_source_paths),
            },
            "related_nodes": [compact_node(node) for node in scoped_nodes[:node_limit]],
            "related_edges": [compact_edge(edge) for edge in scoped_edges[:edge_limit]],
            "source_backed_related_nodes": source_backed_related_nodes[:node_limit],
            "source_backed_related_edges": source_backed_related_edges[:edge_limit],
            "truncated": {
                "nodes": len(scoped_nodes) > node_limit,
                "edges": len(scoped_edges) > edge_limit,
                "source_graph": (graph.get("limits") or {}).get("truncated"),
            },
            "source_key_metrics": source_key_profile,
            "retrieval_context": retrieval_context,
            "context_text": (retrieval_context or {}).get("context_text", ""),
            "datalog_evidence": datalog_evidence,
        }

    def _datalog_transitive_evidence(self, nodes, edges, center_node):
        """Third, independent evidence strategy alongside ReasoningEngine's
        LLM-assisted path planner and this module's own heuristic joint-
        traversal ranking: run the *full fetched* graph (nodes/edges, before
        the depth-limited BFS scoping below trims them down to
        scoped_nodes/scoped_edges) through DatalogReasoner
        (aletheia/reasoning/datalog_reasoner.py) to derive every node
        transitively reachable from center_node via any relation, treating
        edges as undirected for reachability purposes -- Aletheia's edge
        types are directional (e.g. Commit -TOUCHES-> File, PullRequest
        -MERGES-> Commit), so a File center node has only incoming edges
        and a directed-only rule would find nothing. The linked(X, Y) base
        rule below is symmetric over graph_facts.py's edge(X, Y, relation)
        facts (emitted once per edge regardless of label), and connected(X,
        Y) is its transitive closure, so reachability doesn't depend on
        which endpoint happens to hold the outgoing edge. Using the
        pre-scoping graph (rather than the depth-limited one) is the point:
        it can surface nodes beyond what the task's configured depth would
        otherwise show. Returns None (skip the evidence/SSE event entirely)
        when there are no edges to reason over or nothing new is derivable
        beyond center_node's immediate neighbors.

        center_node arrives as "Type:Id" (the reasoning task scope's own
        convention), but graph_facts.py's facts -- like every node/edge
        dict's own "id" field from full_graph()/local_rag_context() -- use
        the bare vertex id, never "Type:"-prefixed. Query/compare using the
        bare id (center_id below) so the Datalog match actually fires,
        instead of silently matching nothing."""
        if not edges or not center_node:
            return None
        center_id = str(center_node).split(":", 1)[-1]
        reasoner = DatalogReasoner()
        try:
            load_facts(reasoner, nodes, edges)
        except ValueError:
            return None
        reasoner.add_rule("linked(?X, ?Y) :- edge(?X, ?Y, ?R).")
        reasoner.add_rule("linked(?X, ?Y) :- edge(?Y, ?X, ?R).")
        reasoner.add_rule("connected(?X, ?Y) :- linked(?X, ?Y).")
        reasoner.add_rule("connected(?X, ?Y) :- linked(?X, ?Z), connected(?Z, ?Y).")
        reasoner.derive_all()
        try:
            bindings = reasoner.query(f"connected({center_id}, ?Y)")
        except ValueError:
            return None
        direct_neighbors = {
            edge.get("target") if edge.get("source") == center_id else edge.get("source")
            for edge in edges
            if edge.get("source") == center_id or edge.get("target") == center_id
        }
        reachable = sorted({binding["Y"] for binding in bindings} - {center_id} - direct_neighbors)
        if not reachable:
            return None
        return {
            "kind": "datalog_derived",
            "rule": "connected(X, Y) :- linked(X, Y) | linked(X, Z), connected(Z, Y) -- linked is symmetric over edge(X, Y, _)",
            "center_node": center_node,
            "derived_reachable_count": len(reachable),
            "derived_reachable_nodes": reachable[:50],
        }

    def _diversify_edges_by_relation(self, edges):
        buckets = {}
        for edge in edges or []:
            relation = edge.get("label") or edge.get("link_key") or edge.get("kind") or "relation"
            buckets.setdefault(relation, []).append(edge)
        ordered = []
        bucket_items = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
        while bucket_items:
            next_items = []
            for relation, values in bucket_items:
                if values:
                    ordered.append(values.pop(0))
                if values:
                    next_items.append((relation, values))
            bucket_items = next_items
        return ordered

    def run_scoped_graph_task(self, tenant, task_key):
        """Thin wrapper over run_scoped_graph_task_streaming: drains the streaming
        generator and returns the terminal run_complete event's payload, which is
        exactly the dict shape this method returned before this refactor (each of
        the three run_scoped_graph_task_streaming exit paths -- missing evidence
        paths, no approved/demo graph projection, and the successful run -- yields
        a run_complete event with {"tenant", "task", "run", "findings", "approved"},
        matching what handler.py's /run route and TasksMixin.run_task expect).

        The two non-run_complete exits (task not found, task closed) don't yield a
        run_complete event; this wrapper recovers the original synchronous
        contract for those from the streamed error event: return None for "task
        not found" (matching the original's `if task is None: return None`), and
        re-raise ValueError for "Cannot run a closed task" (matching the
        original's `raise ValueError("Cannot run a closed task")`), since
        run_task's caller (handler.py) distinguishes 404-vs-400 on exactly those
        two outcomes.
        """
        events = list(self.run_scoped_graph_task_streaming(tenant, task_key))
        for event in events:
            if event.get("event") == "run_complete":
                return event["data"]
        error_message = None
        for event in events:
            if event.get("event") == "error":
                error_message = (event.get("data") or {}).get("message")
                break
        if error_message == "Cannot run a closed task":
            raise ValueError(error_message)
        return None

    def _edge_or_scoped_finding_text(self, tenant, task, scope):
        wants_zh = wants_zh_output(scope.get("language"), task.get("question"))
        center_edge = scope.get("center_edge") or {}
        question = task.get("question") or ("此范围化图谱问题" if wants_zh else "the scoped graph question")
        if center_edge.get("source") and center_edge.get("target"):
            source = center_edge["source"]
            target = center_edge["target"]
            edge = self.instance_repository.edge_detail(tenant, source, target)
            if wants_zh:
                title = f"{source} -> {target} 已批准边证据"
                conclusion = f'针对问题"{question}"，已批准图谱中包含所选的 {source} -> {target} 关系。'
                if edge:
                    conclusion += (
                        f"该关系由 {edge.get('source_ref') or '来源行证据'} "
                        f"以及本体链接 {edge.get('ontology_link') or edge.get('link_key') or '链接'} 支持。"
                    )
                conclusion += "此为待审核的草稿答案，不会更改正式本体或图谱。"
                return title, conclusion
            title = f"{source} -> {target} approved edge evidence"
            conclusion = (
                f'For the question "{question}", the approved graph contains the selected '
                f"{source} -> {target} relationship. "
            )
            if edge:
                conclusion += (
                    f"The relationship is supported by {edge.get('source_ref') or 'source-row evidence'} "
                    f"and ontology link {edge.get('ontology_link') or edge.get('link_key') or 'link'}. "
                )
            conclusion += "This is a draft answer for review and does not change canonical ontology or graph."
            return title, conclusion
        center = scope.get("center_node") or f"{center_edge.get('source', 'scope')} -> {center_edge.get('target', 'scope')}"
        if wants_zh:
            return (
                f"{center} 的范围化答案",
                (
                    f'针对问题"{question}"，本次运行限定在所选的已批准图谱范围内。'
                    "此为待审核的草稿答案，不会更改正式本体或图谱。"
                ),
            )
        return (
            f"Scoped answer for {center}",
            (
                f'For the question "{question}", the run is constrained to the selected approved graph scope. '
                "This is a draft answer for review and does not change canonical ontology or graph."
            ),
        )

    def _is_legacy_scoped_finding(self, finding):
        title = (finding.get("title") or "").lower()
        conclusion = (finding.get("conclusion") or "").lower()
        return (
            "scoped graph reasoning remains draft-only" in title
            or "created from graph explorer evidence" in conclusion
            or "work snapshot" in title
            or "approved order relationships" in title
            or "loaded in the current evidence scope" in conclusion
        )

    def _normalize_scoped_finding_display(self, tenant, finding):
        task = finding.get("task") or {}
        if not task:
            task = {
                "question": finding.get("question"),
                "scope": finding.get("task_scope") or {},
            }
        scope = task.get("scope") or finding.get("task_scope") or {}
        structured_answer = finding.get("structured_answer") or (finding.get("recommended_action") or {}).get("structured_answer")
        structured_response = finding.get("structured_response") or (finding.get("recommended_action") or {}).get("structured_response")
        center_node = scope.get("center_node")
        if not structured_answer:
            engine = ReasoningEngine(self.instance_repository)
            structured_answer = engine.analyze(
                tenant,
                center_node,
                task.get("question"),
                depth=int(scope.get("depth") or 1),
                limit=int(scope.get("node_limit") or 200),
            )
            if structured_answer:
                raw_recommended_action = finding.get("recommended_action") or {}
                finding["recommended_action"] = {
                    **raw_recommended_action,
                    "structured_answer": structured_answer,
                }
                finding["structured_answer"] = structured_answer
                for key in ("profile_summary", "key_facts", "business_interpretation", "evidence_limits", "next_questions"):
                    finding[key] = structured_answer.get(key) or ([] if key != "profile_summary" else "")
        # NOTE: this used to also opportunistically re-run ReasoningEngine.analyze()
        # on every display whenever the existing structured_answer lacked
        # metrics.source_key_profile.related_tables, to "upgrade" older
        # findings. That field comes from the SQL-schema source-key
        # profiling path, which has been fully retired with no graph-native
        # replacement (see _scoped_graph_prompt_context's source_key_profile
        # comment) -- so related_tables can never be populated again, and
        # that refresh silently re-ran a full (LLM-backed, when the planner
        # is enabled) analyze() call on every single read of every
        # graph-native finding, forever, for a refresh that could never
        # succeed. Removed rather than gated, since it never did anything.
        if not structured_answer and not self._is_legacy_scoped_finding(finding):
            return finding
        raw_title = finding.get("title")
        raw_conclusion = finding.get("conclusion")
        if structured_answer:
            existing_graph_context = (structured_response or {}).get("graph_context") if isinstance(structured_response, dict) else None
            structured_response = self._reasoning_response_v1(
                tenant,
                task,
                scope,
                structured_answer,
                finding.get("supporting_evidence") or [],
                graph_context=existing_graph_context,
            )
            raw_recommended_action = finding.get("recommended_action") or {}
            finding["recommended_action"] = {
                **raw_recommended_action,
                "structured_response": structured_response,
            }
            finding["structured_response"] = structured_response
            answer = structured_response.get("answer") or {}
            title = answer.get("title") or structured_answer.get("title") or raw_title
            conclusion = answer.get("conclusion") or structured_answer.get("profile_summary") or raw_conclusion
            finding["confidence"] = max(float(finding.get("confidence") or 0), 0.78)
            metrics = structured_answer.get("metrics") or {}
            evidence_paths = list(finding.get("supporting_evidence") or [])
            if not any(path.get("kind") == "controlled_aggregate" for path in evidence_paths):
                rankings = metrics.get("rankings") or []
                label_val = metrics.get("label") or scope.get("center_node")
                if rankings:
                    ranking_text = "; ".join(
                        f"{r['my_count']} {r['target_type']} (#{r['rank']}/{r['total_peers']}, {r['level']})"
                        for r in rankings if r.get("my_count", 0) > 0
                    ) or "no ranked relationships"
                    summary_text = f"{label_val}: {ranking_text}"
                else:
                    neighbor_types = metrics.get("neighbor_types") or {}
                    neighbor_text = ", ".join(f"{c} {t}" for t, c in sorted(neighbor_types.items())) if neighbor_types else "scope data"
                    summary_text = f"{label_val} has {metrics.get('neighbor_count', 0)} related entities ({neighbor_text})"
                evidence_paths.append(
                    {
                        "kind": "controlled_aggregate",
                        "label": f"{label_val} Business Profile",
                        "summary": summary_text,
                        "url": f"/reasoning.html?tenant={tenant.tenant_id}&task={quote(task.get('canonical_key') or '')}",
                        "source_ref": f"{metrics.get('object_type', 'entity')} + peer ranking",
                        "payload": metrics,
                    }
                )
                finding["supporting_evidence"] = evidence_paths
        else:
            title, conclusion = self._edge_or_scoped_finding_text(tenant, task, scope)
        finding["raw_title"] = raw_title
        finding["raw_conclusion"] = raw_conclusion
        finding["title"] = title
        finding["conclusion"] = conclusion
        finding["display_normalized"] = True
        return finding
