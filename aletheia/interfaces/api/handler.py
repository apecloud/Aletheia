"""AletheiaServerHandler (HTTP request router) and main(), extracted from
server.py. No behavior change -- wiring in main() is unchanged."""

import argparse
import json
import mimetypes
mimetypes.add_type("text/javascript", ".jsx")
import os
import ssl
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from sqlalchemy import bindparam, create_engine, text
from aletheia.core.tenant_registry import TenantRegistry
from aletheia.interfaces.api.helpers import DB_URL, SOURCE_DB_URL, STATIC_ROOT
from aletheia.interfaces.api.http_server import LocalThreadingHTTPServer
from aletheia.interfaces.api.repositories.review import ReviewRepository
from aletheia.interfaces.api.repositories.instance import InstanceRepository
from aletheia.interfaces.api.repositories.reasoning import ReasoningRepository
from aletheia.interfaces.api.repositories.agent_gateway import AgentGatewayRepository


class AletheiaServerHandler(BaseHTTPRequestHandler):
    repository = None
    instance_repository = None
    reasoning_repository = None
    agent_gateway_repository = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            tenant = self._tenant(parsed)
        except (KeyError, ValueError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/tenants":
            self._send_json(
                {
                    "current": tenant.public_dict(),
                    "default_tenant_id": self.repository.tenant_registry.default_tenant_id,
                    "tenants": self.repository.tenant_registry.list_public(),
                }
            )
            return
        if parsed.path == "/api/artifacts":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            filters.pop("tenant", None)
            self._send_json(self.repository.list_artifacts(tenant, filters))
            return
        if parsed.path == "/api/ontology/catalog":
            filters = {key: values[0] for key, values in parse_qs(parsed.query).items() if values and values[0]}
            filters.pop("tenant", None)
            if "kind" in filters and "artifact_type" not in filters:
                filters["artifact_type"] = filters.pop("kind")
            if "q" in filters and "search" not in filters:
                filters["search"] = filters.pop("q")
            self._send_json(self.repository.list_artifacts(tenant, filters))
            return
        if parsed.path == "/api/web-enrichment/proposals":
            query = parse_qs(parsed.query)
            artifact_key = query.get("artifact", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(self.repository.list_web_enrichment(tenant, artifact_key, limit=limit))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path.startswith("/api/ontology/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/ontology/"))
            artifact = self.repository.get_artifact(tenant, canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Ontology artifact not found: {canonical_key}")
                return
            self._send_json(
                {
                    "tenant": tenant.public_dict(),
                    "artifact": artifact,
                    "definition": artifact.get("payload", {}),
                    "source_schema": artifact.get("source_schema", {}),
                    "evidence": artifact.get("evidence", []),
                    "reviews": artifact.get("reviews", []),
                    "canonical": artifact.get("canonical", {}),
                    "used_by": artifact.get("used_by", []),
                    "issues": [],
                }
            )
            return
        if parsed.path == "/api/portal/overview":
            self._send_json(self._portal_overview(tenant))
            return
        if parsed.path.startswith("/api/portal/findings/"):
            finding_key = unquote(parsed.path.removeprefix("/api/portal/findings/"))
            finding = self.reasoning_repository.finding_detail(tenant, finding_key)
            if finding is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {finding_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": finding})
            return
        if parsed.path == "/api/agent-gateway/settings":
            self._send_json(self.agent_gateway_repository.list_settings(tenant))
            return
        if parsed.path.startswith("/api/agent-gateway/runtimes/") and parsed.path.endswith("/readiness"):
            runtime_id = unquote(parsed.path.removeprefix("/api/agent-gateway/runtimes/").removesuffix("/readiness").rstrip("/"))
            result = self.agent_gateway_repository.readiness(tenant, runtime_id)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/sessions":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                result = self.reasoning_repository.list_autopilot_sessions(tenant, status=status_filter, limit=limit)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").rstrip("/"))
            result = self.reasoning_repository.get_autopilot_session(tenant, session_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            self._send_json(self.reasoning_repository.list_tasks(tenant, status_filter=status_filter))
            return
        if parsed.path == "/api/reasoning/findings":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            context = query.get("context", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                filters = {
                    "finding_type": query.get("finding_type", [None])[0],
                    "source": query.get("source", [None])[0],
                    "action_state": query.get("action_state", [None])[0],
                    "freshness": query.get("freshness", [None])[0],
                    "sort": query.get("sort", [None])[0],
                    "group": query.get("group", [None])[0],
                }
                for key in ("min_confidence", "max_confidence", "min_value", "max_value"):
                    if query.get(key, [None])[0] not in (None, ""):
                        filters[key] = float(query.get(key, [None])[0])
                filters = {key: value for key, value in filters.items() if value not in (None, "")}
                self._send_json(self.reasoning_repository.list_findings_registry(
                    tenant,
                    status=status_filter,
                    context=context,
                    limit=limit,
                    filters=filters,
                ))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/reasoning/findings/revalidation-queue":
            query = parse_qs(parsed.query)
            status_filter = query.get("status", [None])[0]
            try:
                limit = int(query.get("limit", ["50"])[0])
                self._send_json(self.reasoning_repository.finding_revalidation_queue(tenant, status=status_filter, limit=limit))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path.startswith("/api/reasoning/tasks/"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/"))
            task = self.reasoning_repository.get_task(tenant, task_key)
            if task is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json(task)
            return
        if parsed.path.startswith("/api/reasoning/findings/"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/"))
            finding = self.reasoning_repository.get_finding(tenant, finding_key)
            if finding is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {finding_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": finding})
            return
        if parsed.path == "/api/graph/context":
            query = parse_qs(parsed.query)
            depth = int(query.get("depth", ["1"])[0])
            limit = int(query.get("limit", ["200"])[0])
            view = query.get("view", ["scope"])[0]
            object_type = (query.get("type", [""])[0] or "").strip()
            instance_id = (query.get("id", [""])[0] or "").strip()
            if view != "all" and (not object_type or not instance_id):
                default_center = self.instance_repository.default_center(tenant)
                if default_center:
                    object_type = object_type or default_center["type"]
                    instance_id = instance_id or default_center["id"]
            graph = (
                self.instance_repository.full_graph(tenant, object_type, instance_id, limit=limit)
                if view == "all"
                else self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
            )
            if graph is None:
                self._send_json(
                    {
                        "approved": False,
                        "tenant": tenant.public_dict(),
                        "graph_database": tenant.graph_database,
                        "depth": depth,
                        "limit": limit,
                        "center": None,
                        "nodes": [],
                        "edges": [],
                        "scope": {
                            "tenant_id": tenant.tenant_id,
                            "view": view,
                            "type": object_type or None,
                            "id": instance_id or None,
                            "approved_only": True,
                            "projection_source": "none",
                            "reason": "No reviewed SchemaGraphModelingAgent projection. Import data and run schema-to-graph modeling first.",
                        },
                    }
                )
                return
            if graph.get("approved"):
                if view == "all":
                    graph["graph_url"] = f"/graph.html?tenant={quote(tenant.tenant_id)}&view=all&limit={graph.get('limit', limit)}"
                else:
                    graph["graph_url"] = (
                        f"/graph.html?tenant={quote(tenant.tenant_id)}&type={quote(object_type)}"
                        f"&id={quote(str(instance_id))}&depth={graph.get('depth', depth)}&limit={graph.get('limit', limit)}"
                    )
            self._send_json(graph)
            return
        if parsed.path == "/api/graph/local-rag-context":
            query = parse_qs(parsed.query)
            depth = int(query.get("depth", ["1"])[0])
            limit = int(query.get("limit", ["80"])[0])
            object_type = (query.get("type", [""])[0] or "").strip()
            instance_id = (query.get("id", [""])[0] or "").strip()
            question = query.get("question", [None])[0]
            if not object_type or not instance_id:
                default_center = self.instance_repository.default_center(tenant)
                if default_center:
                    object_type = object_type or default_center["type"]
                    instance_id = instance_id or default_center["id"]
            if not object_type or not instance_id:
                self._send_error(HTTPStatus.BAD_REQUEST, "type and id are required when the tenant has no approved graph center")
                return
            context = self.instance_repository.local_rag_context(
                tenant,
                object_type,
                instance_id,
                question=question,
                depth=depth,
                limit=limit,
            )
            if context is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Local RAG context not found or not approved")
                return
            self._send_json(context)
            return
        if parsed.path == "/api/graph/community-summaries":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["300"])[0])
            self._send_json(self.instance_repository.graph_community_summaries(tenant, limit=limit))
            return
        if parsed.path == "/api/graph/leiden-communities":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["300"])[0])
            resolution = float(query.get("resolution", ["1.0"])[0])
            self._send_json(self.instance_repository.graph_leiden_communities(tenant, limit=limit, resolution=resolution))
            return
        if parsed.path == "/api/graph/centrality-ranking":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["300"])[0])
            method = query.get("method", ["betweenness"])[0]
            top_n = int(query.get("top_n", ["20"])[0])
            self._send_json(self.instance_repository.graph_centrality_ranking(tenant, limit=limit, method=method, top_n=top_n))
            return
        if parsed.path == "/api/graph/rag-query-context":
            query = parse_qs(parsed.query)
            depth = int(query.get("depth", ["1"])[0])
            limit = int(query.get("limit", ["80"])[0])
            object_type = (query.get("type", [""])[0] or "").strip() or None
            instance_id = (query.get("id", [""])[0] or "").strip() or None
            question = query.get("question", [""])[0]
            self._send_json(
                self.instance_repository.graph_rag_query_context(
                    tenant,
                    question=question,
                    object_type=object_type,
                    instance_id=instance_id,
                    depth=depth,
                    limit=limit,
                )
            )
            return
        if parsed.path == "/api/graph/ontology-model":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["300"])[0])
            self._send_json(self.instance_repository.ontology_model_graph(tenant, limit=limit))
            return
        if parsed.path in {"/api/graph/proposed-elements", "/api/knowledge/candidates"}:
            query = parse_qs(parsed.query)
            run_key = query.get("run_key", [""])[0] or None
            limit = int(query["limit"][0]) if query.get("limit", [""])[0] else None
            status_filter = query.get("status", ["pending"])[0]
            element_type = (query.get("element_type", [""])[0] or "").strip() or None
            compact = query.get("compact", ["0"])[0] in {"1", "true", "yes"}
            try:
                self._send_json(self.instance_repository.proposed_graph_elements(
                    tenant,
                    run_key=run_key,
                    limit=limit,
                    status_filter=status_filter,
                    element_type=element_type,
                    compact=compact,
                ))
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/agent-runs/console":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["20"])[0])
            self._send_json(self.instance_repository.agent_runs_console(tenant, limit=limit))
            return
        if parsed.path == "/api/enrichment/sessions":
            self._send_json(self.instance_repository.continuous_enrichment_sessions(tenant))
            return
        if parsed.path.startswith("/api/enrichment/sessions/"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").rstrip("/"))
            if "/" in session_key:
                session_key = session_key.split("/", 1)[0]
            result = self.instance_repository.continuous_enrichment_session(tenant, session_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/graph/node/"):
            node_key = unquote(parsed.path.removeprefix("/api/graph/node/"))
            if ":" not in node_key:
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected node key in the form Type:Id")
                return
            # Graph-native tenants hand back the raw Nebula VID as the node id, which
            # itself may embed a colon (e.g. "<doc_hash>:<slug>") -- it is not a
            # Type:Id pair. Try the key verbatim first; only fall back to splitting
            # on the first colon for legacy SQL-schema tenants where ids really are
            # Type:Id.
            object_type, instance_id = "", node_key
            detail = self.instance_repository.detail(tenant, object_type, instance_id)
            if detail is None:
                object_type, instance_id = node_key.split(":", 1)
                detail = self.instance_repository.detail(tenant, object_type, instance_id)
            if detail is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph node not found or not approved")
                return
            object_type = detail.get("type") or object_type
            graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=1, limit=300)
            by_relation = {}
            if graph and graph.get("approved"):
                for edge in graph.get("edges", []):
                    relation = edge.get("link_key") or edge.get("ontology_link") or edge.get("label") or "edge"
                    by_relation[relation] = by_relation.get(relation, 0) + 1
            detail["neighborhood_summary"] = {
                "nodes": len(graph.get("nodes", [])) if graph and graph.get("approved") else 1,
                "edges": len(graph.get("edges", [])) if graph and graph.get("approved") else 0,
                "by_relation": by_relation,
                "projection_source": (graph.get("scope") or {}).get("projection_source") if graph else None,
            }
            self._send_json({"tenant": tenant.public_dict(), "node": detail})
            return
        if parsed.path.startswith("/api/graph/edge/"):
            edge_key = unquote(parsed.path.removeprefix("/api/graph/edge/"))
            if "->" not in edge_key:
                self._send_error(HTTPStatus.BAD_REQUEST, "Expected edge key in the form Type:Id->Type:Id")
                return
            source, target = edge_key.split("->", 1)
            edge = self.instance_repository.edge_detail(tenant, source, target)
            if edge is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph edge not found or not approved")
                return
            self._send_json({"tenant": tenant.public_dict(), "edge": edge})
            return
        if parsed.path == "/api/instances/types":
            query = parse_qs(parsed.query)
            include_draft = query.get("include_draft", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self.instance_repository.types(tenant, include_draft=include_draft))
            return
        if parsed.path == "/api/instances/search":
            query = parse_qs(parsed.query)
            object_type = query.get("type", [""])[0].strip()
            if not object_type:
                default_center = self.instance_repository.default_center(tenant, include_draft=query.get("include_draft", ["0"])[0] in {"1", "true", "yes"})
                object_type = default_center["type"] if default_center else ""
            if not object_type:
                self._send_error(HTTPStatus.BAD_REQUEST, "type is required when the tenant has no approved graph center")
                return
            search = query.get("q", [""])[0]
            limit = int(query.get("limit", ["25"])[0])
            include_draft = query.get("include_draft", ["0"])[0] in {"1", "true", "yes"}
            self._send_json(self.instance_repository.search(tenant, object_type, search, limit=limit, include_draft=include_draft))
            return
        if parsed.path == "/api/instances/edge":
            query = parse_qs(parsed.query)
            source = query.get("source", [""])[0]
            target = query.get("target", [""])[0]
            edge = self.instance_repository.edge_detail(tenant, source, target)
            if edge is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Edge not found or not approved")
                return
            self._send_json(edge)
            return
        if parsed.path.startswith("/api/instances/"):
            parts = parsed.path.removeprefix("/api/instances/").split("/")
            if len(parts) == 2:
                object_type, instance_id = unquote(parts[0]), unquote(parts[1])
                detail = self.instance_repository.detail(tenant, object_type, instance_id)
                if detail is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Instance not found or object type is not approved")
                    return
                self._send_json(detail)
                return
            if len(parts) == 3 and parts[2] == "neighborhood":
                object_type, instance_id = unquote(parts[0]), unquote(parts[1])
                query = parse_qs(parsed.query)
                depth = int(query.get("depth", ["1"])[0])
                limit = int(query.get("limit", ["200"])[0])
                graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
                if graph is None:
                    self._send_error(HTTPStatus.NOT_FOUND, "Neighborhood not found")
                    return
                self._send_json(graph)
                return
        if parsed.path.startswith("/api/artifacts/"):
            canonical_key = unquote(parsed.path.removeprefix("/api/artifacts/"))
            artifact = self.repository.get_artifact(tenant, canonical_key)
            if artifact is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {canonical_key}")
                return
            self._send_json(artifact)
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            tenant = self._tenant(parsed)
        except (KeyError, ValueError) as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path in {"/api/graph/proposed-elements/batch-review", "/api/knowledge/candidates/batch-review"}:
            try:
                body = self._read_json()
                result = self.instance_repository.review_proposed_graph_elements_batch(
                    tenant,
                    body.get("element_keys") or [],
                    body.get("action") or "",
                    body,
                )
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/graph/proposed-elements/") or parsed.path.startswith("/api/knowledge/candidates/"):
            prefix = "/api/knowledge/candidates/" if parsed.path.startswith("/api/knowledge/candidates/") else "/api/graph/proposed-elements/"
            parts = parsed.path.removeprefix(prefix).split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.BAD_REQUEST, f"Expected {prefix}{{element_key}}/{{action}}")
                return
            element_key, action = unquote(parts[0]), unquote(parts[1])
            try:
                body = self._read_json()
                result = self.instance_repository.review_proposed_graph_element(tenant, element_key, action, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Proposed graph element not found")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/agent-gateway/runtimes/") and parsed.path.endswith("/health"):
            runtime_id = unquote(parsed.path.removeprefix("/api/agent-gateway/runtimes/").removesuffix("/health").rstrip("/"))
            result = self.agent_gateway_repository.health_check(tenant, runtime_id)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/agent-gateway/runs":
            try:
                body = self._read_json()
                runtime_id = body.get("runtime_id") or "generic_cli_builtin"
                result = self.agent_gateway_repository.run_smoke(tenant, runtime_id, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/agent-gateway/safe-demo":
            try:
                body = self._read_json()
                runtime_id = body.get("runtime_id") or "generic_cli_builtin"
                result = self.agent_gateway_repository.run_safe_demo(tenant, runtime_id, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Runtime not found: {runtime_id}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/graph/expand":
            try:
                body = self._read_json()
                node_key = body.get("node_key") or body.get("center_node")
                if not node_key:
                    default_center = self.instance_repository.default_center(tenant)
                    node_key = default_center["node"]["id"] if default_center and default_center.get("node") else None
                if not node_key:
                    raise ValueError("node_key is required when the tenant has no approved graph center")
                if ":" not in node_key:
                    raise ValueError("node_key must be in the form Type:Id")
                object_type, instance_id = node_key.split(":", 1)
                depth = int(body.get("depth") or 1)
                limit = int(body.get("limit") or body.get("node_limit") or 200)
                graph = self.instance_repository.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if graph is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Graph expansion not found")
                return
            self._send_json(graph)
            return
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith("/run-cycle"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix("/run-cycle").rstrip("/"))
            try:
                body = self._read_json()
                result = self.instance_repository.run_continuous_enrichment_cycle(tenant, session_key, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith("/configure"):
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix("/configure").rstrip("/"))
            try:
                body = self._read_json()
                result = self.instance_repository.configure_continuous_enrichment_session(tenant, session_key, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/enrichment/sessions/") and parsed.path.endswith(("/pause", "/resume", "/stop")):
            action = parsed.path.rstrip("/").rsplit("/", 1)[1]
            session_key = unquote(parsed.path.removeprefix("/api/enrichment/sessions/").removesuffix(f"/{action}").rstrip("/"))
            status = {"pause": "paused", "resume": "idle", "stop": "stopped"}[action]
            try:
                result = self.instance_repository.update_continuous_enrichment_session_status(tenant, session_key, status)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Continuous enrichment session not found: {session_key}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/sessions":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_autopilot_session(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/autopilot/playbooks/creditcardfraud/run":
            try:
                body = self._read_json()
                result = self.reasoning_repository.run_creditcardfraud_playbook(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/") and parsed.path.endswith("/hypotheses"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").removesuffix("/hypotheses").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.add_autopilot_hypothesis(tenant, session_key, body)
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/sessions/") and parsed.path.endswith("/candidate-findings"):
            session_key = unquote(parsed.path.removeprefix("/api/reasoning/autopilot/sessions/").removesuffix("/candidate-findings").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.add_autopilot_candidate_finding(tenant, session_key, body)
            except KeyError:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot session not found: {session_key}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/autopilot/candidate-findings/"):
            parts = parsed.path.removeprefix("/api/reasoning/autopilot/candidate-findings/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/autopilot/candidate-findings/<canonical_key>/<action>")
                return
            candidate_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                result = self.reasoning_repository.review_autopilot_candidate(
                    tenant,
                    candidate_key,
                    action,
                    body.get("reviewer") or "Itachi",
                    body.get("reason") or "",
                )
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Autopilot candidate not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks/from-graph":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_scoped_task_from_graph(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/questions":
            try:
                body = self._read_json()
                result = self.reasoning_repository.create_question_task(tenant, body)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/tasks/bulk-close":
            try:
                body = self._read_json()
                keys = body.get("keys")
                before = body.get("before")
                if not keys and not before:
                    self._send_error(HTTPStatus.BAD_REQUEST, "Provide 'keys' (array) or 'before' (ISO date)")
                    return
                result = self.reasoning_repository.bulk_close_tasks(tenant, keys=keys, before=before)
            except Exception as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), **result})
            return
        if parsed.path == "/api/reasoning/tasks/bulk-delete-closed":
            try:
                result = self.reasoning_repository.bulk_delete_closed_tasks(tenant)
            except Exception as exc:
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), **result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/delete"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/delete").rstrip("/"))
            result = self.reasoning_repository.delete_task(tenant, task_key)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Task not found: {task_key}")
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/close"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/close").rstrip("/"))
            result = self.reasoning_repository.update_task_status(tenant, task_key, "closed")
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "task": result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/reopen"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/reopen").rstrip("/"))
            result = self.reasoning_repository.update_task_status(tenant, task_key, "active")
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json({"tenant": tenant.public_dict(), "task": result})
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/run/stream"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/run/stream").rstrip("/"))
            self._stream_run(tenant, task_key)
            return
        if parsed.path.startswith("/api/reasoning/tasks/") and parsed.path.endswith("/run"):
            task_key = unquote(parsed.path.removeprefix("/api/reasoning/tasks/").removesuffix("/run").rstrip("/"))
            try:
                result = self.reasoning_repository.run_task(tenant, task_key)
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning task not found: {task_key}")
                return
            self._send_json(result)
            return
        if parsed.path == "/api/reasoning/findings/revalidation-batch":
            try:
                body = self._read_json()
                result = self.reasoning_repository.batch_revalidate_findings(tenant, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/finding-actions/"):
            parts = parsed.path.removeprefix("/api/reasoning/finding-actions/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/finding-actions/<action_key>/<action>")
                return
            action_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                result = self.reasoning_repository.update_finding_action(tenant, action_key, action, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Finding action not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/") and parsed.path.endswith("/actions"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/").removesuffix("/actions").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.finding_workspace_action(tenant, finding_key, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/") and parsed.path.endswith("/change-proposals"):
            finding_key = unquote(parsed.path.removeprefix("/api/reasoning/findings/").removesuffix("/change-proposals").rstrip("/"))
            try:
                body = self._read_json()
                result = self.reasoning_repository.finding_change_proposal(tenant, finding_key, body)
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path.startswith("/api/reasoning/findings/"):
            parts = parsed.path.removeprefix("/api/reasoning/findings/").split("/")
            if len(parts) != 2:
                self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/reasoning/findings/<canonical_key>/<action>")
                return
            finding_key = unquote(parts[0])
            action = parts[1]
            try:
                body = self._read_json()
                reviewer = body.get("reviewer") or "Itachi"
                reason = body.get("reason") or ""
                if action == "approve":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "approved", reviewer, reason)
                elif action == "reject":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "rejected", reviewer, reason)
                elif action in {"needs-changes", "needs-evidence", "needs-more-evidence"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "needs_more_evidence", reviewer, reason)
                elif action in {"mark-stale", "stale"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "stale", reviewer, reason)
                elif action in {"supersede", "superseded"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "superseded", reviewer, reason)
                elif action in {"reaffirm", "reaffirmed"}:
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "reaffirmed", reviewer, reason)
                elif action == "comment":
                    result = self.reasoning_repository.review_finding(tenant, finding_key, "comment", reviewer, reason)
                else:
                    self._send_error(HTTPStatus.NOT_FOUND, f"Unknown reasoning action: {action}")
                    return
            except KeyError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, f"Reasoning finding not found: {exc.args[0]}")
                return
            except json.JSONDecodeError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
                return
            except ValueError as exc:
                self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            except Exception as exc:  # pragma: no cover - displayed to local operator
                self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
                return
            self._send_json({"tenant": tenant.public_dict(), "finding": result})
            return
        if not parsed.path.startswith("/api/artifacts/"):
            self._send_error(HTTPStatus.NOT_FOUND, "Unknown API endpoint")
            return
        parts = parsed.path.removeprefix("/api/artifacts/").split("/")
        if len(parts) != 2:
            self._send_error(HTTPStatus.NOT_FOUND, "Expected /api/artifacts/<canonical_key>/<action>")
            return
        canonical_key = unquote(parts[0])
        action = parts[1]
        try:
            body = self._read_json()
            reviewer = body.get("reviewer") or "Itachi"
            reason = body.get("reason") or ""
            if action == "approve":
                result = self.repository.review_status(tenant, canonical_key, "approved", reviewer, reason)
            elif action == "reject":
                result = self.repository.review_status(tenant, canonical_key, "rejected", reviewer, reason)
            elif action == "needs-changes":
                result = self.repository.review_status(tenant, canonical_key, "needs_changes", reviewer, reason)
            elif action == "comment":
                result = self.repository.comment(tenant, canonical_key, reviewer, reason)
            elif action == "edit":
                payload = body.get("payload") if "payload" in body else None
                result = self.repository.edit(
                    tenant,
                    canonical_key,
                    reviewer,
                    reason,
                    name=body.get("name"),
                    description=body.get("description"),
                    payload=payload,
                )
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown action: {action}")
                return
        except KeyError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, f"Artifact not found: {exc.args[0]}")
            return
        except json.JSONDecodeError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid JSON: {exc}")
            return
        except ValueError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        except Exception as exc:  # pragma: no cover - displayed to local operator
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return
        self._send_json(result)

    def _portal_overview(self, tenant):
        artifact_result = self.repository.list_artifacts(tenant, {})
        artifacts = artifact_result.get("artifacts", [])
        artifact_stats = artifact_result.get("stats", [])
        tasks = self.reasoning_repository.list_tasks(tenant).get("tasks", [])
        findings = self.reasoning_repository.list_findings_overview(tenant, limit=25)
        runs = self.reasoning_repository.list_runs_overview(tenant, limit=25)
        agent_settings = self.agent_gateway_repository.list_settings(tenant)
        agent_runs = agent_settings.get("runs", [])
        default_center = self.instance_repository.default_center(tenant)
        default_graph = (
            self.instance_repository.neighborhood(tenant, default_center["type"], default_center["id"], depth=1, limit=200)
            if default_center
            else self.instance_repository.full_graph(tenant, limit=200)
        )
        approved_artifacts = [artifact for artifact in artifacts if artifact.get("status") == "approved"]
        draft_findings = [finding for finding in findings if finding.get("status") == "draft"]
        low_confidence = [finding for finding in findings if float(finding.get("confidence") or 0) < 0.75]
        blocked_runs = [run for run in runs if run.get("status") in {"blocked", "failed"}]
        blocked_agent_runs = [
            run
            for run in agent_runs
            if run.get("status") in {"blocked", "failed"} or run.get("policy_violations")
        ]
        attention_items = []
        for finding in draft_findings[:5]:
            attention_items.append(
                {
                    "kind": "draft",
                    "severity": "review",
                    "title": "Draft finding awaits review",
                    "summary": finding.get("title"),
                    "href": f"/findings.html?tenant={quote(tenant.tenant_id)}&finding={quote(finding.get('canonical_key'))}",
                }
            )
        for finding in low_confidence[:4]:
            attention_items.append(
                {
                    "kind": "low_confidence",
                    "severity": "medium",
                    "title": "Low confidence conclusion",
                    "summary": f"{finding.get('title')} · confidence {float(finding.get('confidence') or 0):.2f}",
                    "href": f"/findings.html?tenant={quote(tenant.tenant_id)}&finding={quote(finding.get('canonical_key'))}",
                }
            )
        for run in blocked_runs[:4]:
            attention_items.append(
                {
                    "kind": "blocked_reasoning",
                    "severity": "high",
                    "title": "Reasoning run blocked",
                    "summary": run.get("output", {}).get("summary") or run.get("run_key"),
                    "href": f"/questions.html?tenant={quote(tenant.tenant_id)}&task={quote(run.get('task_key'))}",
                }
            )
        for run in blocked_agent_runs[:4]:
            attention_items.append(
                {
                    "kind": "policy_violation",
                    "severity": "high",
                    "title": "Agent runtime requires attention",
                    "summary": run.get("run_key"),
                    "href": f"/settings.html?tenant={quote(tenant.tenant_id)}",
                }
            )
        latest_times = [
            item.get("updated_at") or item.get("created_at")
            for item in [*findings, *tasks, *artifacts]
            if item.get("updated_at") or item.get("created_at")
        ]
        return {
            "tenant": tenant.public_dict(),
            "knowledge_status": {
                "entity_count": len(default_graph.get("nodes", [])) if default_graph and default_graph.get("approved") else 0,
                "relation_count": len(default_graph.get("edges", [])) if default_graph and default_graph.get("approved") else 0,
                "artifact_count": len(artifacts),
                "approved_artifact_count": len(approved_artifacts),
                "finding_count": len(findings),
                "task_count": len(tasks),
                "approved_only": True,
                "system_state": "ready" if default_graph and default_graph.get("approved") else "blocked",
                "latest_update": max(latest_times) if latest_times else None,
                "graph_database": tenant.graph_database,
                "namespace": tenant.namespace,
            },
            "artifact_stats": artifact_stats,
            "key_findings": findings[:8],
            "attention_items": attention_items[:12],
            "quality": {
                "draft_findings": len(draft_findings),
                "low_confidence_findings": len(low_confidence),
                "blocked_reasoning_runs": len(blocked_runs),
                "blocked_agent_runs": len(blocked_agent_runs),
            },
            "recent_changes": {
                "tasks": tasks[:8],
                "runs": runs[:8],
                "findings": findings[:8],
                "agent_runs": agent_runs[:5],
            },
            "quick_tasks": [
                {"label": "Ask a question", "href": f"/questions.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Explain a finding", "href": f"/findings.html?tenant={quote(tenant.tenant_id)}"},
                {
                    "label": "Inspect an entity",
                    "href": (
                        f"/instances.html?tenant={quote(tenant.tenant_id)}"
                        + (
                            f"&type={quote(default_center['type'])}&id={quote(default_center['id'])}"
                            if default_center
                            else ""
                        )
                    ),
                },
                {"label": "View evidence chain", "href": f"/findings.html?tenant={quote(tenant.tenant_id)}"},
                {
                    "label": "Trace graph path",
                    "href": (
                        f"/graph.html?tenant={quote(tenant.tenant_id)}"
                        + (
                            f"&type={quote(default_center['type'])}&id={quote(default_center['id'])}&depth=1&limit=200"
                            if default_center
                            else "&view=all&limit=200"
                        )
                    ),
                },
                {"label": "Check quality issues", "href": f"/quality.html?tenant={quote(tenant.tenant_id)}"},
                {"label": "Run scoped reasoning", "href": f"/questions.html?tenant={quote(tenant.tenant_id)}&template=scoped"},
            ],
        }

    def _tenant(self, parsed):
        query = parse_qs(parsed.query)
        tenant_id = query.get("tenant", [None])[0]
        return self.repository.tenant(tenant_id)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _cors_headers(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def _send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_sse_event(self, event_type, data):
        msg = f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        self.wfile.write(msg.encode("utf-8"))
        self.wfile.flush()

    def _stream_run(self, tenant, task_key):
        self.send_response(HTTPStatus.OK)
        self._cors_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for item in self.reasoning_repository.run_task_streaming(tenant, task_key):
                self._send_sse_event(item["event"], item["data"])
        except Exception as exc:
            self._send_sse_event("error", {"message": str(exc)})

    def _send_static(self, request_path):
        relative_path = "index.html" if request_path in ("", "/") else request_path.lstrip("/")
        file_path = (STATIC_ROOT / relative_path).resolve()
        if not str(file_path).startswith(str(STATIC_ROOT.resolve())) or not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        if file_path.suffix in {".js", ".jsx", ".html"}:
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


ReviewWorkbenchHandler = AletheiaServerHandler


def main():
    parser = argparse.ArgumentParser(description="Run the Aletheia API and frontend app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db-url", default=DB_URL)
    parser.add_argument("--source-db-url", default=SOURCE_DB_URL)
    parser.add_argument("--tenants-file", help="JSON file defining tenant_id/namespace/graph_database mappings")
    parser.add_argument("--ensure-schema", action="store_true", help="Create/migrate artifact tables before serving")
    parser.add_argument(
        "--disable-continuous-enrichment-scheduler",
        action="store_true",
        help="Do not start the background continuous enrichment scheduler",
    )
    parser.add_argument("--tls-cert", help="Path to TLS certificate PEM file (enables HTTPS)")
    parser.add_argument("--tls-key", help="Path to TLS private key PEM file")
    args = parser.parse_args()

    # Reasoning's LLM planner (relation selection + multi-center answer
    # derivation, see reasoning_engine.py) is opt-in by env var so tests never
    # make network calls -- for the actual server process, default it on so
    # it isn't silently off just because nobody remembered to export it.
    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "1")
    # Relation-insight synthesis (traversal.py's _synthesize_relation_insight)
    # is a separate LLM call site from the planner above -- same opt-in-for-
    # tests/default-on-for-server convention, kept as its own flag so an
    # operator can disable one without the other.
    os.environ.setdefault("ALETHEIA_LLM_INSIGHT_ENABLED", "1")
    os.environ["ALETHEIA_PG_URL"] = args.db_url
    os.environ["ALETHEIA_MYSQL_URL"] = args.source_db_url
    registry = TenantRegistry.load(args.tenants_file)
    AletheiaServerHandler.repository = ReviewRepository(registry, ensure_schema=args.ensure_schema)
    AletheiaServerHandler.instance_repository = InstanceRepository(registry, ensure_schema=args.ensure_schema)
    AletheiaServerHandler.reasoning_repository = ReasoningRepository(
        registry,
        AletheiaServerHandler.instance_repository,
        ensure_schema=args.ensure_schema,
    )
    AletheiaServerHandler.instance_repository.reasoning_repository = AletheiaServerHandler.reasoning_repository
    AletheiaServerHandler.agent_gateway_repository = AgentGatewayRepository(registry, ensure_schema=args.ensure_schema)
    server = LocalThreadingHTTPServer((args.host, args.port), AletheiaServerHandler)
    scheme = "http"
    if args.tls_cert and args.tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        ctx.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    if not args.disable_continuous_enrichment_scheduler:
        AletheiaServerHandler.instance_repository.start_continuous_enrichment_scheduler(interval_seconds=60)
    print(f"Aletheia Server: {scheme}://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        AletheiaServerHandler.instance_repository.stop_continuous_enrichment_scheduler()


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
