"""InstanceRepository, extracted from server.py. No behavior change."""

import hashlib
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from urllib.parse import parse_qs, quote, unquote, urlparse
import igraph as ig
import leidenalg as la
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import sessionmaker
from aletheia.reasoning.engine import ReasoningEngine
from aletheia.reasoning.finding_framework import (
    DEEP_GRAPH_REQUIRED_STEPS as REASONING_DEEP_GRAPH_REQUIRED_STEPS,
    deep_graph_profile,
    entity_profile_aggregate_evidence,
    finding_canonical_boundary,
    plain_reasoning_conclusion,
    plain_reasoning_title,
    review_graph_scope_action,
    scoped_graph_finding,
)
from aletheia.enrichment.iterative_enrichment import (
    IterativeGraphEnrichmentAgent,
    _configured_api_key,
    _edge_fact_identity_compatible,
    _edge_fact_identity_parts,
)
from aletheia.ontology.quality import concrete_object_quality
from aletheia.ontology.store import ensure_artifact_schema, upsert_artifact
from aletheia.ontology.label_embeddings import (
    find_nearest_label, label_embedding_count, sync_label_embeddings,
)
from aletheia.graph_store.nebula_client import NebulaGraphClient, insert_with_schema_retry
import aletheia.ontology.registry as ontology_registry
from aletheia.interfaces.api.helpers import CONTINUOUS_RUNNING_STALE_SECONDS, DEFAULT_LLM_MODEL, _ONTOLOGY_CONCEPT_EDGE_PROPERTIES, _ONTOLOGY_CONCEPT_VERTEX_PROPERTIES, _apply_edge_source_identity_presentation_guard, _apply_possible_duplicate_presentation_guard, _attach_proposal_match_summaries, _compact_candidate_payload, _dedup_audit_from_payload, _first_nonempty, _graph_edge_fact_key, _graph_identity_text, _is_current_graph_proposal, _json_dump, _jsonable, _knowledge_candidate_profile, _load_json, _proposal_match_keys_from_payload, _proposal_match_summary, _safe_error_message, _slug, _web_enrichment_query
from aletheia.interfaces.api.repositories.base import _TenantScopedEngineCache


class InstanceRepository(_TenantScopedEngineCache):
    _register_tenant_on_engine_create = False

    def __init__(self, tenant_registry, ensure_schema=False):
        super().__init__(tenant_registry, ensure_schema)
        self.reasoning_repository = None
        self._continuous_scheduler_lock = threading.Lock()
        self._continuous_scheduler_thread = None
        self._continuous_scheduler_stop = None
        self._graph_repos = {}
        self._graph_repos_lock = threading.Lock()

    def _graph_repo_for(self, tenant):
        """Lazily construct/cache a GraphInstanceRepository for a
        backend="graph" tenant -- ReasoningEngine's SQL-retrieval core was
        retired, so such tenants delegate entirely to real Nebula traversal
        (see agents/graph_instance_repository.py) instead of the SQL logic
        the rest of this class implements. Cached per tenant_id since each
        instance owns its own Nebula connection."""
        cached = self._graph_repos.get(tenant.tenant_id)
        if cached is not None:
            return cached
        from aletheia.graph_store.instance_repository import GraphInstanceRepository

        with self._graph_repos_lock:
            cached = self._graph_repos.get(tenant.tenant_id)
            if cached is not None:
                return cached
            repo = GraphInstanceRepository(
                space=tenant.graph_database,
                nebula_ip=tenant.graph_ip,
                nebula_port=tenant.graph_port,
                nebula_user=tenant.graph_user,
                nebula_password=tenant.graph_password,
                relation_catalog_db_url=tenant.metadata_db_url,
                relation_catalog_scope=tenant.relation_catalog_scope or tenant.tenant_id,
            )
            self._graph_repos[tenant.tenant_id] = repo
        return repo

    def _graph_native_types(self, tenant):
        """Approved graph-native types, in the same dict shape
        `_ontology_concrete_object_types` produces, so `_merge_instance_types`
        can combine both sources without knowing which pipeline produced
        which entry."""
        entity_config = self._graph_repo_for(tenant).reasoning_entity_config(tenant.tenant_id)
        return [
            {
                "type": entry["type_name"],
                "label": entry["type_name"],
                "table": "approved graph-native types",
                "ontology_artifact": entry.get("artifact"),
                "artifact_status": "approved",
                "approved": True,
                "tenant_id": tenant.tenant_id,
                "projection_source": "GraphNativeOntology",
                "ontology_object_count": 0,
            }
            for entry in entity_config.values()
            if entry.get("type_name")
        ]

    def types(self, tenant, include_draft=False):
        graph_native_types = self._graph_native_types(tenant)
        ontology_types = self._ontology_concrete_object_types(tenant, include_draft=include_draft)
        merged = self._merge_instance_types(graph_native_types, ontology_types)
        if merged:
            return {"tenant": tenant.public_dict(), "types": merged, "approved": True}
        return {
            "tenant": tenant.public_dict(),
            "types": [],
            "approved": False,
            "reason": "No approved types for this tenant, neither graph-native ontology nor SQL-schema projection.",
        }

    def search(self, tenant, object_type, query, limit=25, include_draft=False):
        graph_native_instances = (
            self._graph_repo_for(tenant).search_instances(object_type, query, limit=limit) if object_type else []
        )
        ontology_instances = self._ontology_concrete_object_search(
            tenant,
            object_type,
            query,
            limit=limit,
            include_draft=include_draft,
        )
        if graph_native_instances or ontology_instances:
            instances = []
            seen = set()
            for node in graph_native_instances:
                node_id = node.get("id")
                if node_id and node_id not in seen:
                    seen.add(node_id)
                    instances.append(node)
            for node in ontology_instances:
                node_id = node.get("id")
                if not node_id:
                    continue
                if node_id in seen:
                    for existing in instances:
                        if existing.get("id") != node_id:
                            continue
                        aliases = list(dict.fromkeys((existing.get("aliases") or []) + (node.get("aliases") or [])))
                        if node.get("label") and node.get("label") != existing.get("label"):
                            aliases.append(node.get("label"))
                        existing["aliases"] = list(dict.fromkeys([alias for alias in aliases if alias]))
                        existing["projection_source"] = self._join_projection_sources(
                            existing.get("projection_source"),
                            node.get("projection_source"),
                        )
                        break
                    continue
                seen.add(node_id)
                instances.append(node)
                if len(instances) >= max(1, min(int(limit), 100)):
                    break
            return {
                "instances": instances[: max(1, min(int(limit), 100))],
                "approved": True,
                "artifact_status": "approved",
                "tenant": tenant.public_dict(),
                "projection_source": self._join_projection_sources(
                    "GraphNativeInstance" if graph_native_instances else None,
                    "OntologyConcreteObject" if ontology_instances else None,
                ),
            }
        return {
            "tenant": tenant.public_dict(),
            "instances": [],
            "approved": False,
            "reason": f"No approved instances of type {object_type}, neither graph-native nor SQL-schema projection.",
        }

    def default_center(self, tenant, include_draft=False):
        """Return a tenant-local default graph center without domain
        fixtures. Source-agnostic: `types()`/`search()` already merge the
        graph-native and SQL-schema projections, so this loop works
        unchanged for either (or both) without knowing which one is live."""
        for type_info in self.types(tenant, include_draft=include_draft).get("types", []):
            object_type = type_info.get("type")
            if not object_type:
                continue
            result = self.search(tenant, object_type, "", limit=1, include_draft=include_draft)
            instances = result.get("instances") or []
            if not instances:
                continue
            instance_id = instances[0].get("instance_id") or instances[0].get("source_pk", "").split("=", 1)[-1]
            if instance_id:
                return {"type": object_type, "id": str(instance_id), "node": instances[0]}
        return None

    def detail(self, tenant, object_type, instance_id):
        """Single-node lookup: try graph-native first (using `instance_id`
        verbatim -- it's the raw Nebula VID, never split), then SQL-schema.
        Unlike the list-shaped methods above, a first-non-null precedence is
        correct here (not a merge) -- a single id can only belong to one
        underlying storage, same as `neighborhood()`."""
        vertex = self._graph_repo_for(tenant)._fetch_vertex(instance_id)
        if vertex is not None:
            return {
                "id": vertex["id"],
                "type": vertex["types"][0] if vertex["types"] else object_type,
                "label": vertex["label"],
                "aliases": [],
                "key_properties": vertex["properties"],
                "projection_source": "GraphNativeInstance",
            }
        return self._ontology_concrete_object_detail(tenant, object_type, instance_id)

    def _join_projection_sources(self, *sources):
        items = []
        for source in sources:
            for part in str(source or "").split("+"):
                part = part.strip()
                if part and part not in items:
                    items.append(part)
        return "+".join(items) if items else None

    def _compact_identifier(self, value):
        return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).lower()

    def _instance_type_matches(self, left, right):
        return self._compact_identifier(left) == self._compact_identifier(right)

    def _merge_instance_types(self, schema_types, ontology_types):
        result = []
        by_compact = {}
        for item in schema_types or []:
            item = dict(item)
            key = self._compact_identifier(item.get("type"))
            if not key:
                continue
            result.append(item)
            by_compact[key] = item
        for item in ontology_types or []:
            item = dict(item)
            key = self._compact_identifier(item.get("type"))
            if not key:
                continue
            existing = by_compact.get(key)
            if existing:
                existing["ontology_object_count"] = int(existing.get("ontology_object_count") or 0) + int(item.get("ontology_object_count") or 0)
                existing["projection_source"] = self._join_projection_sources(
                    existing.get("projection_source"),
                    item.get("projection_source"),
                )
                continue
            result.append(item)
            by_compact[key] = item
        return result

    def _ontology_concrete_object_statuses(self, include_draft=False):
        statuses = ["approved"]
        if include_draft:
            statuses.extend(["needs_more_evidence", "draft"])
        return statuses

    def _ontology_concrete_object_rows(self, tenant, include_draft=False, class_catalog=None):
        statuses = self._ontology_concrete_object_statuses(include_draft=include_draft)
        placeholders = ", ".join(f":status_{idx}" for idx, _ in enumerate(statuses))
        params = {"tenant_id": tenant.tenant_id, **{f"status_{idx}": status for idx, status in enumerate(statuses)}}
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT element_key, element_type, name, payload_json, evidence_refs_json,
                               source_url, confidence, status, created_at
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND status IN ({placeholders})
                          AND element_type = 'ontology_concept'
                        ORDER BY created_at DESC NULLS LAST, id DESC
                        """
                    ),
                    params,
                ).mappings().all()
        except Exception:
            return []

        result = []
        if class_catalog is None:
            class_catalog = self._ontology_class_catalog(tenant)
        for row in rows:
            try:
                payload = _load_json(row["payload_json"], {})
            except Exception:
                payload = {}
            if not self._is_ontology_concrete_object_payload(payload):
                continue
            node = self._ontology_concrete_object_node(tenant, row, payload, class_catalog=class_catalog)
            if node:
                result.append({"row": row, "payload": payload, "node": node})
        return result

    def _is_ontology_concrete_object_payload(self, payload):
        if not isinstance(payload, dict):
            return False
        artifact_type = str(payload.get("artifact_type") or payload.get("source_artifact_type") or "").strip().lower()
        ontology_part = str(payload.get("ontology_part") or "").strip().lower()
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        identity_role = str(identity.get("identity_role") or "").strip().lower()
        return (
            artifact_type in {"object", "entity", "instance"}
            and (
                ontology_part in {"concrete_object", "object_instance", "instance"}
                or identity_role == "concrete_object"
            )
        )

    def _ontology_class_catalog(self, tenant):
        classes = {}

        def add_class(label, description="", source="ontology"):
            label = str(label or "").strip()
            if not label:
                return
            type_name = re.sub(r"[^0-9A-Za-z]", "", label) or label
            key = self._compact_identifier(type_name)
            if not key or key in classes:
                return
            classes[key] = {
                "type": type_name,
                "label": label,
                "description": str(description or "").strip(),
                "source": source,
            }

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT name, description, payload_json
                        FROM aletheia_ontology_artifacts
                        WHERE project_id = :tenant_id
                          AND status = 'approved'
                          AND artifact_type = 'object'
                        """
                    ),
                    {"tenant_id": tenant.tenant_id},
                ).mappings().all()
                proposed_rows = conn.execute(
                    text(
                        """
                        SELECT name, payload_json
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND status = 'approved'
                          AND element_type = 'ontology_concept'
                        """
                    ),
                    {"tenant_id": tenant.tenant_id},
                ).mappings().all()
        except Exception:
            rows = []
            proposed_rows = []

        for row in rows:
            payload = _load_json(row["payload_json"], {}) if row["payload_json"] else {}
            ontology_part = str(payload.get("ontology_part") or "").strip().lower()
            source_artifact_type = str(payload.get("source_artifact_type") or "").strip().lower()
            if ontology_part in {"concrete_object", "object_instance", "instance"} or source_artifact_type in {"entity", "instance"}:
                continue
            add_class(payload.get("label") or row["name"], payload.get("description") or row["description"], source="ontology_artifact")

        for row in proposed_rows:
            payload = _load_json(row["payload_json"], {}) if row["payload_json"] else {}
            artifact_type = str(payload.get("artifact_type") or "").strip().lower()
            ontology_part = str(payload.get("ontology_part") or "").strip().lower()
            if artifact_type != "class" and ontology_part not in {"class", "abstract_class"}:
                continue
            add_class(payload.get("label") or row["name"], payload.get("description"), source="ontology_proposal")

        return list(classes.values())

    def _ontology_class_tokens(self, *values):
        stop_words = {
            "a", "an", "and", "are", "as", "by", "for", "from", "in", "is", "it", "of",
            "on", "or", "such", "that", "the", "their", "this", "to", "used", "with",
            "good", "were", "where", "whose",
        }
        tokens = []
        for value in values:
            for token in re.findall(r"[A-Za-z][A-Za-z0-9]+", str(value or "").lower()):
                if token in stop_words or len(token) < 3:
                    continue
                if len(token) > 4 and token.endswith("ies"):
                    token = token[:-3] + "y"
                elif len(token) > 4 and token.endswith("s"):
                    token = token[:-1]
                tokens.append(token)
        return set(tokens)

    def _infer_ontology_concrete_object_class(self, tenant, payload, label, class_catalog=None):
        class_catalog = class_catalog if class_catalog is not None else self._ontology_class_catalog(tenant)
        if not class_catalog:
            return None
        candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        object_tokens = self._ontology_class_tokens(
            label,
            payload.get("description"),
            payload.get("evidence_quote"),
            candidate.get("description"),
        )
        if not object_tokens:
            return None
        best = None
        for item in class_catalog:
            label_tokens = self._ontology_class_tokens(item.get("label"), item.get("type"))
            description_tokens = self._ontology_class_tokens(item.get("description"))
            if not label_tokens and not description_tokens:
                continue
            label_overlap = len(object_tokens & label_tokens)
            description_overlap = len(object_tokens & description_tokens)
            coverage = (label_overlap * 3 + description_overlap) / max(len(label_tokens) * 3 + len(description_tokens), 1)
            evidence = label_overlap * 3 + description_overlap
            score = evidence + coverage
            if evidence <= 0:
                continue
            if not best or score > best["score"]:
                best = {"score": score, "class": item}
        if best and best["score"] >= 1.0:
            return best["class"]["type"]
        return None

    def _ontology_concrete_object_class(self, tenant, payload, label, class_catalog=None):
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        for value in [
            payload.get("object_type"),
            payload.get("ontology_type"),
            payload.get("class_label"),
            payload.get("class_name"),
            payload.get("class"),
            payload.get("type"),
            identity.get("entity_type"),
            candidate.get("object_type"),
            candidate.get("ontology_type"),
            candidate.get("class_label"),
            candidate.get("class_name"),
            candidate.get("class"),
            candidate.get("type"),
        ]:
            text_value = str(value or "").strip()
            if text_value:
                return re.sub(r"[^0-9A-Za-z]", "", text_value) or text_value
        dedup_decision = str(payload.get("dedup_decision") or payload.get("llm_dedup_decision_override") or "").strip().lower()
        matched_node_key = str(payload.get("matched_node_key") or "").strip()
        if dedup_decision == "merge_existing" and ":" in matched_node_key:
            prefix = matched_node_key.split(":", 1)[0].strip()
            if prefix:
                return prefix
        inferred = self._infer_ontology_concrete_object_class(tenant, payload, label, class_catalog=class_catalog)
        return inferred or "UnclassifiedOntologyObject"

    def _ontology_concrete_object_label(self, row, payload):
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        for value in [payload.get("label"), candidate.get("label"), identity.get("label"), row["name"]]:
            text_value = str(value or "").strip()
            if text_value:
                return text_value
        return row["element_key"]

    def _ontology_concrete_object_node_id(self, object_type, label, payload, row):
        dedup_decision = str(payload.get("dedup_decision") or payload.get("llm_dedup_decision_override") or "").strip().lower()
        matched_node_key = str(payload.get("matched_node_key") or "").strip()
        if dedup_decision == "merge_existing" and ":" in matched_node_key:
            return matched_node_key
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        source_identity = (
            payload.get("source_identity")
            or payload.get("instance_id")
            or identity.get("source_identity")
            or identity.get("canonical_id")
        )
        stable_id = str(source_identity or label or row["element_key"]).strip()
        return f"{object_type}:{stable_id}"

    def _ontology_concrete_object_node(self, tenant, row, payload, class_catalog=None):
        label = self._ontology_concrete_object_label(row, payload)
        object_type = self._ontology_concrete_object_class(tenant, payload, label, class_catalog=class_catalog)
        if not object_type or not label:
            return None
        node_id = self._ontology_concrete_object_node_id(object_type, label, payload, row)
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        aliases = []
        for alias in identity.get("aliases") or payload.get("aliases") or []:
            alias = str(alias or "").strip()
            if alias and alias not in aliases:
                aliases.append(alias)
        if label and label not in aliases:
            aliases.append(label)
        source_url = payload.get("source_url") or candidate.get("source_ref") or row["source_url"]
        evidence_refs = _load_json(row["evidence_refs_json"], []) if row["evidence_refs_json"] else []
        return {
            "id": node_id,
            # Bare identifier piece of `node_id` (`"{object_type}:{stable_id}"`),
            # computed once here instead of guessed later by callers looking
            # for a colon -- graph-native ids are raw Nebula VIDs that can
            # contain colons of their own, so "split on colon" isn't a safe
            # heuristic anywhere outside this SQL-schema-specific id scheme.
            "instance_id": node_id.split(":", 1)[1] if ":" in node_id else node_id,
            "tenant_id": tenant.tenant_id,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "type": object_type,
            "label": label,
            "aliases": aliases,
            "status": row["status"],
            "source_table": None,
            "source_pk": f"element_key={row['element_key']}",
            "ontology_artifact": None,
            "ontology_part": payload.get("ontology_part"),
            "element_key": row["element_key"],
            "confidence": row["confidence"],
            "source_url": source_url,
            "projection_source": "OntologyConcreteObject",
            "key_properties": {
                "label": label,
                "description": payload.get("description") or candidate.get("description"),
                "evidence_quote": payload.get("evidence_quote"),
                "source_url": source_url,
                "dedup_decision": payload.get("dedup_decision"),
                "matched_node_key": payload.get("matched_node_key"),
                "element_key": row["element_key"],
            },
            "properties": {
                "artifact_type": payload.get("artifact_type"),
                "ontology_part": payload.get("ontology_part"),
                "dedup_decision": payload.get("dedup_decision"),
                "matched_node_key": payload.get("matched_node_key"),
                "evidence_refs": evidence_refs,
            },
            "ontology_concrete_object": {
                "element_key": row["element_key"],
                "dedup_decision": payload.get("dedup_decision"),
                "matched_node_key": payload.get("matched_node_key"),
            },
        }

    def _ontology_concrete_object_nodes(self, tenant, include_draft=False, class_catalog=None):
        nodes = []
        by_id = {}
        class_catalog = class_catalog if class_catalog is not None else self._ontology_class_catalog(tenant)
        for item in self._ontology_concrete_object_rows(tenant, include_draft=include_draft, class_catalog=class_catalog):
            node = dict(item["node"])
            existing = by_id.get(node["id"])
            if existing:
                existing["aliases"] = list(dict.fromkeys((existing.get("aliases") or []) + (node.get("aliases") or [])))
                existing["projection_source"] = self._join_projection_sources(
                    existing.get("projection_source"),
                    node.get("projection_source"),
                )
                continue
            by_id[node["id"]] = node
            nodes.append(node)
        return nodes

    def _ontology_concrete_object_types(self, tenant, include_draft=False):
        class_catalog = self._ontology_class_catalog(tenant)
        counts = {}
        for node in self._ontology_concrete_object_nodes(tenant, include_draft=include_draft, class_catalog=class_catalog):
            object_type = node.get("type")
            if not object_type:
                continue
            counts[object_type] = counts.get(object_type, 0) + 1
        class_rows = []
        seen = set()
        for item in class_catalog:
            object_type = item.get("type")
            if not object_type:
                continue
            object_count = counts.get(object_type, 0)
            if object_count <= 0:
                continue
            seen.add(self._compact_identifier(object_type))
            class_rows.append(
                {
                    "type": object_type,
                    "label": item.get("label") or object_type,
                    "table": "approved ontology classes",
                    "ontology_artifact": None,
                    "artifact_status": "approved",
                    "approved": True,
                    "tenant_id": tenant.tenant_id,
                    "projection_source": "OntologyClassCatalog",
                    "ontology_object_count": object_count,
                }
            )
        object_rows = [
            {
                "type": object_type,
                "label": object_type,
                "table": "approved ontology objects",
                "ontology_artifact": None,
                "artifact_status": "approved",
                "approved": True,
                "tenant_id": tenant.tenant_id,
                "projection_source": "OntologyConcreteObject",
                "ontology_object_count": count,
            }
            for object_type, count in sorted(counts.items(), key=lambda item: item[0].lower())
            if self._compact_identifier(object_type) not in seen
            and self._compact_identifier(object_type) != "unclassifiedontologyobject"
        ]
        return class_rows + object_rows

    def _ontology_concrete_object_search(self, tenant, object_type, query, limit=25, include_draft=False):
        limit = max(1, min(int(limit), 100))
        normalized_query = str(query or "").strip().lower()
        result = []
        for node in self._ontology_concrete_object_nodes(tenant, include_draft=include_draft):
            if not self._instance_type_matches(node.get("type"), object_type):
                continue
            node_id = str(node.get("id") or "")
            short_id = node_id.split(":", 1)[1] if ":" in node_id else node_id
            haystack = [
                node_id,
                short_id,
                node.get("label"),
                *(node.get("aliases") or []),
                *((node.get("key_properties") or {}).values()),
            ]
            normalized_values = [str(value or "").strip().lower() for value in haystack if value not in (None, "")]
            if normalized_query and not any(normalized_query in value for value in normalized_values):
                continue
            result.append(node)
            if len(result) >= limit:
                break
        return result

    def _ontology_concrete_object_detail(self, tenant, object_type, instance_id):
        expected_id = f"{object_type}:{instance_id}"
        nodes = self._ontology_concrete_object_nodes(tenant, include_draft=True)
        for node in nodes:
            node_id = str(node.get("id") or "")
            short_id = node_id.split(":", 1)[1] if ":" in node_id else node_id
            if not self._instance_type_matches(node.get("type"), object_type):
                continue
            if node_id != expected_id and short_id != str(instance_id):
                continue
            relations_summary = self._ontology_node_relations_summary(tenant, node_id, nodes=nodes)
            return {
                **node,
                "source_row": node.get("key_properties") or {},
                "relations_summary": relations_summary,
            }
        return None

    def _ontology_relation_instance_rows(self, tenant):
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT element_key, name, payload_json, evidence_refs_json,
                               source_url, confidence, status, created_at
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND status = 'approved'
                          AND element_type = 'ontology_concept'
                        ORDER BY created_at DESC NULLS LAST, id DESC
                        """
                    ),
                    {"tenant_id": tenant.tenant_id},
                ).mappings().all()
        except Exception:
            return []
        result = []
        for row in rows:
            try:
                payload = _load_json(row["payload_json"], {})
            except Exception:
                continue
            artifact_type = str(payload.get("artifact_type") or "").strip().lower()
            ontology_part = str(payload.get("ontology_part") or "").strip().lower()
            if artifact_type not in {"link", "relation"} and ontology_part != "relation":
                continue
            if not payload.get("source_label") or not payload.get("target_label"):
                continue
            result.append({"row": row, "payload": payload})
        return result

    def _ontology_node_lookup(self, nodes):
        lookup = {}

        def add(key, node_id):
            key = self._compact_identifier(key)
            if key and node_id:
                lookup.setdefault(key, node_id)

        for node in nodes or []:
            node_id = node.get("id")
            node_type = node.get("type")
            label = node.get("label")
            short_id = str(node_id or "").split(":", 1)[1] if ":" in str(node_id or "") else node_id
            for value in [node_id, short_id, label, *(node.get("aliases") or [])]:
                add(value, node_id)
                add(f"{node_type}:{value}", node_id)
        return lookup

    def _ontology_relation_instance_edges(self, tenant, nodes):
        lookup = self._ontology_node_lookup(nodes)
        edges = []
        seen = set()
        for item in self._ontology_relation_instance_rows(tenant):
            row = item["row"]
            payload = item["payload"]
            source_type = payload.get("source_object_type") or payload.get("source_type") or payload.get("domain")
            target_type = payload.get("target_object_type") or payload.get("target_type") or payload.get("range")
            source_label = payload.get("source_label")
            target_label = payload.get("target_label")
            source_id = (
                lookup.get(self._compact_identifier(f"{source_type}:{source_label}"))
                or lookup.get(self._compact_identifier(source_label))
            )
            target_id = (
                lookup.get(self._compact_identifier(f"{target_type}:{target_label}"))
                or lookup.get(self._compact_identifier(target_label))
            )
            if not source_id or not target_id or source_id == target_id:
                continue
            relation = payload.get("relation") or payload.get("label") or row["name"] or "relation"
            edge_id = f"{source_id}->{target_id}:ontology:{row['element_key']}"
            if edge_id in seen:
                continue
            seen.add(edge_id)
            evidence_refs = _load_json(row["evidence_refs_json"], []) if row["evidence_refs_json"] else []
            edges.append(
                {
                    "id": edge_id,
                    "tenant_id": tenant.tenant_id,
                    "source": source_id,
                    "target": target_id,
                    "label": relation,
                    "kind": relation,
                    "status": row["status"],
                    "projection_source": "OntologyRelationInstance",
                    "element_key": row["element_key"],
                    "confidence": row["confidence"],
                    "source_url": payload.get("source_url") or row["source_url"],
                    "properties": {
                        "artifact_type": payload.get("artifact_type"),
                        "ontology_part": payload.get("ontology_part"),
                        "source_label": source_label,
                        "source_object_type": source_type,
                        "target_label": target_label,
                        "target_object_type": target_type,
                        "evidence_quote": payload.get("evidence_quote"),
                        "evidence_refs": evidence_refs,
                    },
                }
            )
        return edges

    def _ontology_node_relations_summary(self, tenant, node_id, nodes=None):
        node_id = str(node_id or "").strip()
        if not node_id:
            return {"nodes": 0, "edges": 0, "by_relation": {}, "projection_source": "OntologyRelationInstance"}
        nodes = nodes if nodes is not None else self._ontology_concrete_object_nodes(tenant, include_draft=False)
        edges = self._ontology_relation_instance_edges(tenant, nodes)
        by_relation = {}
        related_nodes = set()
        edge_count = 0
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source != node_id and target != node_id:
                continue
            edge_count += 1
            relation = str(edge.get("label") or edge.get("kind") or "relation")
            by_relation[relation] = int(by_relation.get(relation) or 0) + 1
            if source and source != node_id:
                related_nodes.add(source)
            if target and target != node_id:
                related_nodes.add(target)
        return {
            "nodes": len(related_nodes) + (1 if edge_count else 0),
            "edges": edge_count,
            "by_relation": by_relation,
            "projection_source": "OntologyRelationInstance",
        }

    def _fetch_entity(self, tenant, object_type, instance_id):
        """Fetch an entity row -- ReasoningEngine calls this repository-level
        adapter directly. All tenants are graph-backed now; this delegates
        to GraphInstanceRepository unconditionally."""
        return self._graph_repo_for(tenant)._fetch_entity(tenant.tenant_id, object_type, instance_id)

    def _entity_node(self, tenant, object_type, row):
        return self._graph_repo_for(tenant)._entity_node(tenant.tenant_id, object_type, row)

    def reasoning_entity_config(self, tenant):
        return self._graph_repo_for(tenant).reasoning_entity_config(tenant.tenant_id)

    def reasoning_link_config(self, tenant):
        return self._graph_repo_for(tenant).reasoning_link_config(tenant.tenant_id)

    def neighborhood(self, tenant, object_type, instance_id, depth=1, limit=200):
        graph = self._graph_repo_for(tenant).neighborhood(tenant.tenant_id, object_type, instance_id, depth=depth, limit=limit)
        if graph is not None:
            return self._merge_ontology_concrete_objects_into_graph(
                tenant,
                graph,
                object_type=object_type,
                instance_id=instance_id,
                limit=limit,
            )
        ontology_detail = self._ontology_concrete_object_detail(tenant, object_type, instance_id)
        if ontology_detail is not None:
            requested_limit = int(limit)
            applied_limit = max(1, min(requested_limit, 300))
            return {
                "approved": True,
                "tenant": tenant.public_dict(),
                "graph_database": tenant.graph_database,
                "depth": max(1, min(int(depth), 2)),
                "limit": applied_limit,
                "limits": {"requested_limit": requested_limit, "applied_limit": applied_limit, "hard_limit": 300, "truncated": False},
                "center": ontology_detail,
                "nodes": [ontology_detail],
                "edges": [],
                "scope": {
                    "tenant_id": tenant.tenant_id,
                    "center_node": ontology_detail["id"],
                    "type": ontology_detail["type"],
                    "id": str(instance_id),
                    "depth": max(1, min(int(depth), 2)),
                    "node_limit": applied_limit,
                    "edge_limit": applied_limit,
                    "allowed_node_types": [ontology_detail["type"]],
                    "allowed_link_keys": [],
                    "approved_only": True,
                    "projection_source": "OntologyConcreteObject",
                },
            }
        return None

    def full_graph(self, tenant, object_type=None, instance_id=None, limit=200):
        """Sampled cross-section across every approved type, from both
        projections. Always asks both sources and merges -- never branches
        on which pipeline the tenant happens to use (mirrors `neighborhood`
        and reuses the same merge helper)."""
        requested_limit = int(limit)
        applied_limit = max(1, min(requested_limit, 300))
        ontology_nodes = self._ontology_concrete_object_nodes(tenant, include_draft=False)

        entity_config = self._graph_repo_for(tenant).reasoning_entity_config(tenant.tenant_id)
        graph_native = (
            self._graph_repo_for(tenant).full_graph(
                entity_config, node_limit=applied_limit, edge_limit=applied_limit * 3,
            )
            if entity_config
            else None
        )
        if not ontology_nodes and graph_native is None:
            return None

        graph = {
            "approved": True,
            "tenant": tenant.public_dict(),
            "graph_database": tenant.graph_database,
            "depth": 0,
            "limit": applied_limit,
            "limits": {
                "requested_limit": requested_limit, "applied_limit": applied_limit,
                "hard_limit": 300, "truncated": len(ontology_nodes) > applied_limit,
            },
            "center": None,
            "nodes": list((graph_native or {}).get("nodes") or []),
            "edges": list((graph_native or {}).get("edges") or []),
            "scope": {
                "tenant_id": tenant.tenant_id,
                "view": "all",
                "node_limit": applied_limit,
                "edge_limit": applied_limit * 3,
                "approved_only": True,
                "projection_source": self._join_projection_sources(
                    "GraphNativeSample" if graph_native else None,
                    "OntologyConcreteObject" if ontology_nodes else None,
                ),
            },
        }
        return self._merge_ontology_concrete_objects_into_graph(
            tenant,
            graph,
            object_type=object_type,
            instance_id=instance_id,
            limit=limit,
            ontology_nodes=ontology_nodes,
        )

    def _merge_ontology_concrete_objects_into_graph(self, tenant, graph, object_type=None, instance_id=None, limit=200, ontology_nodes=None):
        if graph is None:
            return None
        requested_limit = int(limit)
        applied_limit = max(1, min(requested_limit, 300))
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])
        by_id = {node.get("id"): node for node in nodes if node.get("id")}
        ontology_nodes = ontology_nodes if ontology_nodes is not None else self._ontology_concrete_object_nodes(tenant, include_draft=False)
        center = graph.get("center")

        for node in ontology_nodes:
            node_id = node.get("id")
            if not node_id:
                continue
            if node_id in by_id:
                existing = by_id[node_id]
                aliases = list(dict.fromkeys((existing.get("aliases") or []) + (node.get("aliases") or [])))
                if node.get("label") and node.get("label") != existing.get("label"):
                    aliases.append(node.get("label"))
                existing["aliases"] = list(dict.fromkeys([alias for alias in aliases if alias]))
                existing["projection_source"] = self._join_projection_sources(
                    existing.get("projection_source"),
                    node.get("projection_source"),
                )
                existing.setdefault("ontology_concrete_object", node.get("ontology_concrete_object"))
            elif len(nodes) < applied_limit:
                nodes.append(node)
                by_id[node_id] = node

            short_id = node_id.split(":", 1)[1] if ":" in node_id else node_id
            if object_type and instance_id and self._instance_type_matches(node.get("type"), object_type) and short_id == str(instance_id):
                center = by_id.get(node_id) or node

        seen_edges = {edge.get("id") for edge in edges if edge.get("id")}
        ontology_edges = self._ontology_relation_instance_edges(tenant, nodes)
        promoted_edges = []
        for edge in ontology_edges:
            edge_id = edge.get("id")
            if not edge_id or edge_id in seen_edges:
                continue
            if edge.get("source") not in by_id or edge.get("target") not in by_id:
                continue
            promoted_edges.append(edge)
            seen_edges.add(edge_id)
        if promoted_edges:
            edges = [*promoted_edges, *edges]

        scope = dict(graph.get("scope") or {})
        scope["projection_source"] = self._join_projection_sources(
            scope.get("projection_source"),
            "OntologyConcreteObject" if ontology_nodes else None,
            "OntologyRelationInstance" if ontology_edges else None,
        )
        allowed_node_types = set(scope.get("allowed_node_types") or [])
        allowed_node_types.update(node.get("type") for node in ontology_nodes if node.get("type"))
        if allowed_node_types and "allowed_node_types" in scope:
            scope["allowed_node_types"] = sorted(allowed_node_types)
        limits = dict(graph.get("limits") or {})
        limits.setdefault("requested_limit", requested_limit)
        limits.setdefault("applied_limit", applied_limit)
        limits.setdefault("hard_limit", 300)
        limits["truncated"] = bool(limits.get("truncated")) or len(nodes) >= applied_limit and len(ontology_nodes) > 0 or len(edges) > applied_limit * 3
        return {
            **graph,
            "approved": bool(graph.get("approved")) or bool(nodes),
            "limit": graph.get("limit") or applied_limit,
            "limits": limits,
            "center": center,
            "nodes": nodes[:applied_limit],
            "edges": edges[: applied_limit * 3],
            "scope": scope,
        }

    def ontology_model_graph(self, tenant, limit=300):
        requested_limit = int(limit or 300)
        limit = max(1, min(requested_limit, 600))
        semantic_types = {
            "situation",
            "metric_observation",
            "metric_change_observation",
            "impact_claim",
            "indicator_claim",
            "recommendation",
        }
        nodes = []
        edges = []
        seen_nodes = set()
        seen_edges = set()

        def normalize(value):
            return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

        def compact(value):
            return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

        def add_node(node):
            node_id = node.get("id")
            if not node_id or node_id in seen_nodes or len(nodes) >= limit:
                return False
            seen_nodes.add(node_id)
            nodes.append(node)
            return True

        def add_edge(source, target, label, **extra):
            if not source or not target or source == target or len(edges) >= limit * 3:
                return
            edge_id = extra.pop("id", None) or f"{source}->{target}:{label}"
            if edge_id in seen_edges:
                return
            seen_edges.add(edge_id)
            edges.append(
                {
                    "id": edge_id,
                    "source": source,
                    "target": target,
                    "label": label,
                    "kind": label,
                    "status": extra.pop("status", "approved"),
                    "projection_source": "OntologyModelGraph",
                    **extra,
                }
            )

        with self.metadata_engine_for(tenant).connect() as conn:
            artifact_rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, artifact_type, name, description, payload_json,
                           confidence, source_refs_json, status, source_agent,
                           created_at, updated_at
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND status = 'approved'
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": limit},
            ).mappings().all()
            proposed_rows = conn.execute(
                text(
                    """
                    SELECT element_key, element_type, name, payload_json, evidence_refs_json,
                           source_url, confidence, status, created_at
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id
                      AND element_type IN ('situation', 'metric_observation', 'metric_change_observation',
                                           'impact_claim', 'indicator_claim', 'recommendation')
                    ORDER BY created_at DESC NULLS LAST, id DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant.tenant_id, "limit": limit},
            ).mappings().all()

        artifact_by_key = {}
        artifact_by_label = {}
        artifact_by_source_element = {}
        artifact_by_source_url = {}
        artifact_by_quote = {}
        artifact_type_label = {
            "object": "OntologyObject",
            "link": "OntologyLink",
            "property": "OntologyProperty",
            "action": "OntologyAction",
            "event": "OntologyEvent",
        }
        for row in artifact_rows:
            payload = _load_json(row["payload_json"], {})
            node_id = f"ontology:{row['canonical_key']}"
            artifact_by_key[row["canonical_key"]] = {"row": row, "payload": payload, "node_id": node_id}
            labels = [
                row["name"],
                payload.get("label"),
                payload.get("source_artifact_type"),
                payload.get("ontology_part"),
            ]
            for label in labels:
                normalized = normalize(label)
                if normalized:
                    artifact_by_label.setdefault(normalized, node_id)
                    artifact_by_label.setdefault(compact(label), node_id)
            source_element = payload.get("source_proposed_graph_element_key") or (payload.get("promotion") or {}).get("source_element_key")
            graph_space_element_key = self._ontology_graph_space_element_key(tenant.tenant_id, row["canonical_key"])
            if source_element:
                artifact_by_source_element[source_element] = node_id
            source_url = payload.get("source_url") or next((ref for ref in _load_json(row["source_refs_json"], []) if str(ref).startswith("gpt_researcher://")), "")
            if source_url:
                artifact_by_source_url.setdefault(source_url, []).append(node_id)
            quote = normalize(payload.get("evidence_quote"))
            if quote:
                artifact_by_quote.setdefault(quote[:180], []).append(node_id)
            add_node(
                {
                    "id": node_id,
                    "type": artifact_type_label.get(row["artifact_type"], "OntologyArtifact"),
                    "label": row["name"] or row["canonical_key"],
                    "status": row["status"],
                    "canonical_key": row["canonical_key"],
                    "artifact_type": row["artifact_type"],
                    "ontology_part": payload.get("ontology_part"),
                    "source_artifact_type": payload.get("source_artifact_type"),
                    "source_agent": row["source_agent"],
                    "graph_space_element_key": graph_space_element_key,
                    "confidence": row["confidence"],
                    "description": row["description"],
                    "source_url": source_url,
                    "evidence_quote": payload.get("evidence_quote"),
                    "projection_source": "OntologyModelGraph",
                    "properties": {
                        "canonical_key": row["canonical_key"],
                        "artifact_type": row["artifact_type"],
                        "ontology_part": payload.get("ontology_part"),
                        "source_artifact_type": payload.get("source_artifact_type"),
                        "source_proposed_graph_element_key": source_element,
                        "graph_space_element_key": graph_space_element_key,
                    },
                }
            )

        def node_for_label(label):
            if not label:
                return None
            return artifact_by_label.get(normalize(label)) or artifact_by_label.get(compact(label))

        for item in artifact_by_key.values():
            row = item["row"]
            payload = item["payload"]
            source_id = item["node_id"]
            if row["artifact_type"] == "property":
                target_id = node_for_label(payload.get("property_of") or payload.get("domain"))
                add_edge(source_id, target_id, "property_of")
            if row["artifact_type"] == "link":
                add_edge(source_id, node_for_label(payload.get("domain")), "domain")
                add_edge(source_id, node_for_label(payload.get("range")), "range")
            for label in payload.get("target_object_types") or []:
                add_edge(source_id, node_for_label(label), "targets")
            for label in payload.get("affected_object_types") or []:
                add_edge(source_id, node_for_label(label), "affects")
            for label in payload.get("applies_to") or []:
                add_edge(source_id, node_for_label(label), "applies_to")
            trigger_id = node_for_label(payload.get("trigger_event") or payload.get("trigger_or_condition"))
            add_edge(source_id, trigger_id, "triggered_by")

        semantic_nodes = []
        ontology_candidate_nodes = {}
        for row in proposed_rows:
            payload = _load_json(row["payload_json"], {})
            if row["element_type"] not in semantic_types:
                continue
            node_id = f"semantic:{row['element_key']}"
            quote = payload.get("evidence_quote") or ""
            source_url = payload.get("source_url") or row["source_url"]
            added = add_node(
                {
                    "id": node_id,
                    "type": "SemanticItem",
                    "label": row["name"] or row["element_type"],
                    "status": row["status"],
                    "element_key": row["element_key"],
                    "element_type": row["element_type"],
                    "source_url": source_url,
                    "evidence_quote": quote,
                    "confidence": row["confidence"],
                    "projection_source": "OntologyModelGraph",
                    "properties": {
                        "element_key": row["element_key"],
                        "element_type": row["element_type"],
                        "source_url": source_url,
                        "metric_key": payload.get("metric_key"),
                        "subject": payload.get("subject"),
                        "target": payload.get("target"),
                    },
                }
            )
            if added:
                semantic_nodes.append({"row": row, "payload": payload, "node_id": node_id, "quote": quote, "source_url": source_url})

        for item in semantic_nodes:
            semantic_id = item["node_id"]
            payload = item["payload"]
            source_url = item["source_url"]
            quote = normalize(item["quote"])
            linked = set()
            for candidate_key in payload.get("related_ontology_candidates") or payload.get("supports_ontology_candidates") or []:
                target_id = ontology_candidate_nodes.get(candidate_key) or artifact_by_source_element.get(candidate_key)
                if target_id:
                    linked.add(target_id)
                    add_edge(semantic_id, target_id, "supports", status="needs_review", evidence_quote=item["quote"])
            for target_id in artifact_by_source_url.get(source_url, [])[:12]:
                linked.add(target_id)
                add_edge(semantic_id, target_id, "co_sourced_with", status="needs_review", evidence_quote=item["quote"], match_method="same_source")
            if quote:
                for artifact_quote, target_ids in artifact_by_quote.items():
                    if artifact_quote and (artifact_quote in quote or quote[:120] in artifact_quote):
                        for target_id in target_ids[:4]:
                            linked.add(target_id)
                            add_edge(semantic_id, target_id, "supports", status="needs_review", evidence_quote=item["quote"], match_method="quote_overlap")
            for field, label in (
                ("subject", payload.get("subject")),
                ("target", payload.get("target")),
                ("recommended_action", payload.get("recommended_action")),
                ("metric_key", payload.get("metric_key")),
            ):
                target_id = node_for_label(label)
                if target_id and target_id not in linked:
                    add_edge(semantic_id, target_id, f"mentions_{field}", status="needs_review")

        return {
            "approved": True,
            "tenant": tenant.public_dict(),
            "graph_database": tenant.graph_database,
            "limit": limit,
            "center": nodes[0] if nodes else None,
            "nodes": nodes[:limit],
            "edges": edges[: limit * 3],
            "scope": {
                "tenant_id": tenant.tenant_id,
                "view": "ontology_model",
                "approved_only": False,
                "projection_source": "OntologyModelGraph",
                "node_count": len(nodes),
                "edge_count": len(edges),
                "ontology_artifact_count": len(artifact_rows),
                "semantic_item_count": len(semantic_nodes),
                "edge_semantics": [
                    "supports",
                    "co_sourced_with",
                    "property_of",
                    "domain",
                    "range",
                    "targets",
                    "affects",
                    "applies_to",
                    "triggered_by",
                    "mentions_*",
                ],
            },
        }

    def edge_detail(self, tenant, source, target):
        if ":" not in source or ":" not in target:
            return None
        # Graph-native tenants hand back the raw Nebula VID as source/target,
        # which itself may embed a colon -- not a Type:Id pair. Try the source
        # verbatim first; only fall back to splitting on the first colon for
        # legacy SQL-schema tenants where ids really are Type:Id (mirrors the
        # same fallback in the /api/graph/node/ route above).
        graph = self.neighborhood(tenant, "", source, depth=1, limit=1000)
        if not graph or not graph.get("approved"):
            source_type, source_id = source.split(":", 1)
            graph = self.neighborhood(tenant, source_type, source_id, depth=1, limit=1000)
        if not graph or not graph.get("approved"):
            return None
        nodes_by_id = {node.get("id"): node for node in graph.get("nodes", [])}
        match = next(
            (
                edge
                for edge in graph.get("edges", [])
                if (edge.get("source"), edge.get("target")) in {(source, target), (target, source)}
            ),
            None,
        )
        if not match:
            return None
        link_key = match.get("link_key") or match.get("ontology_link")
        artifact = self._approved_artifacts(tenant, [link_key]).get(link_key) if link_key else None
        return {
            **match,
            "namespace": tenant.namespace,
            "graph_database": tenant.graph_database,
            "ontology_link": link_key,
            "artifact_status": artifact.get("status") if artifact else match.get("status"),
            "artifact_version": artifact.get("version") if artifact else None,
            "source_instance": nodes_by_id.get(match.get("source")),
            "target_instance": nodes_by_id.get(match.get("target")),
            "projection_source": match.get("projection_source") or (graph.get("scope") or {}).get("projection_source"),
            "evidence": "Approved graph edge resolved from tenant-scoped projection metadata.",
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "source": "approved_projection_read",
            },
        }

    def local_rag_context(self, tenant, object_type, instance_id, *, question=None, depth=1, limit=80):
        graph = self.neighborhood(tenant, object_type, instance_id, depth=depth, limit=limit)
        if not graph or not graph.get("approved"):
            return None
        applied_limit = max(1, min(int(limit or 80), 300))
        center = graph.get("center") or next(
            (node for node in graph.get("nodes") or [] if node.get("id") == f"{object_type}:{instance_id}"),
            None,
        )
        center_id = (center or {}).get("id") or f"{object_type}:{instance_id}"
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])
        if center_id and not edges:
            full_graph = self.full_graph(tenant, limit=applied_limit)
            if full_graph and full_graph.get("approved"):
                full_nodes_by_id = {node.get("id"): node for node in full_graph.get("nodes") or [] if node.get("id")}
                local_edges = [
                    edge
                    for edge in full_graph.get("edges") or []
                    if center_id in {edge.get("source"), edge.get("target")}
                ][: applied_limit * 3]
                local_node_ids = {center_id}
                for edge in local_edges:
                    local_node_ids.add(edge.get("source"))
                    local_node_ids.add(edge.get("target"))
                local_nodes = [
                    full_nodes_by_id[node_id]
                    for node_id in local_node_ids
                    if node_id in full_nodes_by_id
                ]
                if local_edges:
                    nodes = local_nodes
                    edges = local_edges
                    center = full_nodes_by_id.get(center_id) or center
        nodes = nodes[:applied_limit]
        edges = edges[: applied_limit * 3]
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}

        def node_summary(node):
            properties = node.get("key_properties") or node.get("properties") or {}
            summary_fields = {}
            for key in ("description", "evidence_quote", "source_url", "source_pk", "ontology_artifact", "element_key"):
                value = node.get(key) if key in node else properties.get(key)
                if value not in (None, "", []):
                    summary_fields[key] = value
            return {
                "id": node.get("id"),
                "label": node.get("label") or node.get("id"),
                "type": node.get("type"),
                "aliases": node.get("aliases") or [],
                "projection_source": node.get("projection_source"),
                "confidence": node.get("confidence"),
                "properties": summary_fields,
            }

        def edge_summary(edge):
            source_node = nodes_by_id.get(edge.get("source")) or {}
            target_node = nodes_by_id.get(edge.get("target")) or {}
            properties = edge.get("properties") or {}
            evidence_refs = properties.get("evidence_refs") or edge.get("evidence_refs") or []
            evidence_quote = properties.get("evidence_quote") or edge.get("evidence_quote")
            return {
                "id": edge.get("id"),
                "source": edge.get("source"),
                "source_label": source_node.get("label") or edge.get("source"),
                "relation": edge.get("label") or edge.get("kind") or edge.get("link_key"),
                "target": edge.get("target"),
                "target_label": target_node.get("label") or edge.get("target"),
                "projection_source": edge.get("projection_source"),
                "confidence": edge.get("confidence"),
                "evidence_quote": evidence_quote,
                "evidence_refs": evidence_refs,
                "source_url": edge.get("source_url") or properties.get("source_url"),
                "properties": {
                    key: value
                    for key, value in properties.items()
                    if key not in {"evidence_refs", "evidence_quote"} and value not in (None, "", [])
                },
            }

        context_nodes = [node_summary(node) for node in nodes]
        context_edges = [edge_summary(edge) for edge in edges]
        semantic_items = self._semantic_items_for_local_rag_context(
            tenant,
            context_nodes,
            context_edges,
            limit=max(10, min(applied_limit, 50)),
        )
        evidence = []
        seen_evidence = set()

        def add_evidence(kind, ref, summary, payload=None):
            ref = str(ref or "").strip()
            summary = str(summary or "").strip()
            key = (kind, ref, summary)
            if not ref and not summary:
                return
            if key in seen_evidence:
                return
            seen_evidence.add(key)
            evidence.append(
                {
                    "kind": kind,
                    "source_ref": ref or None,
                    "summary": summary or ref,
                    "payload": payload or {},
                }
            )

        for node in context_nodes:
            props = node.get("properties") or {}
            add_evidence(
                "node",
                props.get("source_url") or props.get("source_pk") or props.get("element_key") or node.get("id"),
                props.get("evidence_quote") or props.get("description") or node.get("label"),
                {"node_id": node.get("id"), "type": node.get("type")},
            )
        for edge in context_edges:
            for ref in edge.get("evidence_refs") or []:
                add_evidence(
                    "edge",
                    ref,
                    edge.get("evidence_quote") or f"{edge.get('source_label')} {edge.get('relation')} {edge.get('target_label')}",
                    {"edge_id": edge.get("id"), "relation": edge.get("relation")},
                )
            add_evidence(
                "edge",
                edge.get("source_url"),
                edge.get("evidence_quote"),
                {"edge_id": edge.get("id"), "relation": edge.get("relation")},
            )
        for item in semantic_items:
            add_evidence(
                "semantic_item",
                item.get("source_url") or item.get("element_key"),
                item.get("evidence_quote") or item.get("summary") or item.get("label"),
                {"element_key": item.get("element_key"), "element_type": item.get("element_type")},
            )

        lines = []
        if center:
            lines.append(f"Center: {center.get('label') or center.get('id')} ({center.get('type')})")
        if question:
            lines.append(f"Question: {question}")
        if context_edges:
            lines.append("Relations:")
            for edge in context_edges[: min(len(context_edges), 40)]:
                lines.append(
                    "- {source} --{relation}--> {target}".format(
                        source=edge.get("source_label"),
                        relation=edge.get("relation"),
                        target=edge.get("target_label"),
                    )
                )
        elif context_nodes:
            lines.append("No approved local relations are available for this center.")
        if semantic_items:
            lines.append("Semantic context:")
            for item in semantic_items[:10]:
                lines.append(f"- {item.get('element_type')}: {item.get('label')}")

        return {
            "tenant": tenant.public_dict(),
            "approved": True,
            "retrieval_mode": "local_graph_context",
            "question": question,
            "center": node_summary(center) if center else None,
            "nodes": context_nodes,
            "edges": context_edges,
            "semantic_items": semantic_items,
            "evidence": evidence,
            "context_text": "\n".join(lines),
            "scope": {
                **(graph.get("scope") or {}),
                "retrieval_mode": "local_graph_context",
                "approved_only": True,
                "node_count": len(context_nodes),
                "edge_count": len(context_edges),
                "semantic_item_count": len(semantic_items),
                "evidence_count": len(evidence),
            },
            "eval": self._rag_context_eval(context_nodes, context_edges, evidence, semantic_items),
        }

    def _semantic_items_for_local_rag_context(self, tenant, nodes, edges, limit=30):
        semantic_types = {
            "situation",
            "metric_observation",
            "metric_change_observation",
            "impact_claim",
            "indicator_claim",
            "recommendation",
        }
        node_labels = {
            str(value or "").strip().lower()
            for node in nodes or []
            for value in [node.get("id"), node.get("label"), *(node.get("aliases") or [])]
            if str(value or "").strip()
        }
        source_urls = {
            str(value or "").strip()
            for value in [
                *[(node.get("properties") or {}).get("source_url") for node in nodes or []],
                *[edge.get("source_url") for edge in edges or []],
            ]
            if str(value or "").strip()
        }
        if not node_labels and not source_urls:
            return []
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT element_key, element_type, name, payload_json, evidence_refs_json,
                               source_url, confidence, status, created_at
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND element_type IN ('situation', 'metric_observation', 'metric_change_observation',
                                               'impact_claim', 'indicator_claim', 'recommendation')
                          AND status IN ('approved', 'needs_more_evidence')
                        ORDER BY created_at DESC NULLS LAST, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": max(1, min(int(limit or 30) * 4, 200))},
                ).mappings().all()
        except Exception:
            return []

        result = []
        seen = set()
        for row in rows:
            payload = _load_json(row["payload_json"], {}) if row["payload_json"] else {}
            element_type = row["element_type"]
            if element_type not in semantic_types:
                continue
            source_url = str(payload.get("source_url") or row["source_url"] or "").strip()
            text_values = [
                row["name"],
                payload.get("subject"),
                payload.get("target"),
                payload.get("metric_key"),
                payload.get("recommended_action"),
                payload.get("evidence_quote"),
                payload.get("summary"),
            ]
            text_blob = " ".join(str(value or "").lower() for value in text_values)
            source_match = bool(source_url and source_url in source_urls)
            label_match = any(label and label in text_blob for label in node_labels if len(label) >= 3)
            if not source_match and not label_match:
                continue
            key = row["element_key"]
            if key in seen:
                continue
            seen.add(key)
            evidence_refs = _load_json(row["evidence_refs_json"], []) if row["evidence_refs_json"] else []
            result.append(
                {
                    "element_key": key,
                    "element_type": element_type,
                    "label": row["name"] or element_type,
                    "status": row["status"],
                    "confidence": row["confidence"],
                    "source_url": source_url or None,
                    "evidence_quote": payload.get("evidence_quote"),
                    "summary": payload.get("summary") or payload.get("conclusion") or payload.get("description"),
                    "subject": payload.get("subject"),
                    "target": payload.get("target"),
                    "metric_key": payload.get("metric_key"),
                    "evidence_refs": evidence_refs,
                    "context_role": "semantic_review_context",
                    "match": {
                        "source_url": source_match,
                        "label": label_match,
                    },
                }
            )
            if len(result) >= max(1, min(int(limit or 30), 50)):
                break
        return result

    def _rag_context_eval(self, nodes, edges, evidence, semantic_items=None):
        semantic_items = semantic_items or []
        unsupported_edge_count = len([
            edge for edge in edges or []
            if not edge.get("evidence_quote") and not edge.get("evidence_refs") and not edge.get("source_url")
        ])
        return {
            "node_count": len(nodes or []),
            "edge_count": len(edges or []),
            "semantic_item_count": len(semantic_items),
            "evidence_count": len(evidence or []),
            "unsupported_edge_count": unsupported_edge_count,
            "coverage": {
                "has_center": bool(nodes),
                "has_relation": bool(edges),
                "has_evidence": bool(evidence),
                "has_semantic_context": bool(semantic_items),
            },
        }

    def graph_community_summaries(self, tenant, *, limit=300):
        graph = self.full_graph(tenant, limit=limit) or {}
        if not graph.get("approved"):
            return {
                "tenant": tenant.public_dict(),
                "approved": False,
                "retrieval_mode": "community_summary",
                "communities": [],
                "summary_text": "",
                "eval": {"community_count": 0, "node_count": 0, "edge_count": 0},
            }
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])
        nodes_by_id = {node.get("id"): node for node in nodes if node.get("id")}
        adjacency = {node_id: set() for node_id in nodes_by_id}
        edge_by_pair = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source not in nodes_by_id or target not in nodes_by_id:
                continue
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)
            edge_by_pair.setdefault(source, []).append(edge)
            edge_by_pair.setdefault(target, []).append(edge)

        visited = set()
        communities = []
        for node_id in nodes_by_id:
            if node_id in visited:
                continue
            stack = [node_id]
            component = []
            visited.add(node_id)
            while stack:
                current = stack.pop()
                component.append(current)
                for neighbor in adjacency.get(current, set()):
                    if neighbor in visited:
                        continue
                    visited.add(neighbor)
                    stack.append(neighbor)
            component_nodes = [nodes_by_id[item] for item in component if item in nodes_by_id]
            component_node_ids = {node.get("id") for node in component_nodes}
            component_edges = [
                edge for edge in edges
                if edge.get("source") in component_node_ids and edge.get("target") in component_node_ids
            ]
            type_counts = {}
            relation_counts = {}
            labels = []
            evidence_refs = []
            for node in component_nodes:
                node_type = node.get("type") or "Unknown"
                type_counts[node_type] = type_counts.get(node_type, 0) + 1
                if node.get("label"):
                    labels.append(node.get("label"))
            for edge in component_edges:
                relation = edge.get("label") or edge.get("kind") or edge.get("link_key") or "relation"
                relation_counts[relation] = relation_counts.get(relation, 0) + 1
                properties = edge.get("properties") or {}
                for ref in properties.get("evidence_refs") or []:
                    if ref and ref not in evidence_refs:
                        evidence_refs.append(ref)
                if edge.get("source_url") and edge.get("source_url") not in evidence_refs:
                    evidence_refs.append(edge.get("source_url"))
            top_types = sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
            top_relations = sorted(relation_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
            title = ", ".join(label for label in labels[:3]) or component[0]
            summary_parts = [
                f"{len(component_nodes)} objects",
                f"{len(component_edges)} relations",
            ]
            if top_types:
                summary_parts.append("types: " + ", ".join(f"{key}({value})" for key, value in top_types))
            if top_relations:
                summary_parts.append("relations: " + ", ".join(f"{key}({value})" for key, value in top_relations))
            communities.append(
                {
                    "community_id": f"community:{tenant.tenant_id}:{len(communities) + 1}",
                    "title": title,
                    "summary": "; ".join(summary_parts),
                    "node_count": len(component_nodes),
                    "edge_count": len(component_edges),
                    "node_types": dict(sorted(type_counts.items())),
                    "relation_types": dict(sorted(relation_counts.items())),
                    "sample_nodes": [
                        {"id": node.get("id"), "label": node.get("label"), "type": node.get("type")}
                        for node in component_nodes[:10]
                    ],
                    "sample_edges": [
                        {
                            "source": edge.get("source"),
                            "relation": edge.get("label") or edge.get("kind") or edge.get("link_key"),
                            "target": edge.get("target"),
                        }
                        for edge in component_edges[:10]
                    ],
                    "evidence_refs": evidence_refs[:20],
                    "projection_source": (graph.get("scope") or {}).get("projection_source"),
                }
            )
        communities.sort(key=lambda item: (-item["edge_count"], -item["node_count"], item["title"]))
        summary_text = "\n".join(
            f"- {item['title']}: {item['summary']}"
            for item in communities[:20]
        )
        return {
            "tenant": tenant.public_dict(),
            "approved": True,
            "retrieval_mode": "community_summary",
            "communities": communities,
            "summary_text": summary_text,
            "scope": {
                "tenant_id": tenant.tenant_id,
                "approved_only": True,
                "projection_source": (graph.get("scope") or {}).get("projection_source"),
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
            "eval": {
                "community_count": len(communities),
                "node_count": len(nodes),
                "edge_count": len(edges),
                "covered_node_count": sum(item["node_count"] for item in communities),
            },
        }

    def _build_igraph(self, tenant, limit):
        """Shared preamble for graph_leiden_communities/graph_centrality_
        ranking: pull the tenant's approved graph sample and build an
        igraph.Graph from it. Returns (graph_dict, igraph_Graph, node_ids)
        or (graph_dict, None, None) if the graph isn't approved."""
        graph = self.full_graph(tenant, limit=limit) or {}
        if not graph.get("approved"):
            return graph, None, None
        nodes = list(graph.get("nodes") or [])
        edges = list(graph.get("edges") or [])
        node_ids = [node.get("id") for node in nodes if node.get("id")]
        node_id_set = set(node_ids)
        edge_pairs = [
            (edge.get("source"), edge.get("target"))
            for edge in edges
            if edge.get("source") in node_id_set and edge.get("target") in node_id_set
            and edge.get("source") != edge.get("target")
        ]
        g = ig.Graph()
        g.add_vertices(node_ids)
        g.add_edges(edge_pairs)
        return graph, g, node_ids

    def graph_leiden_communities(self, tenant, *, limit=300, resolution=1.0):
        """Modularity-based community detection over the tenant's approved
        graph, via the Leiden algorithm (leidenalg, the reference
        implementation by the algorithm's own authors) -- distinct from
        ``graph_community_summaries`` above, which is plain connected-
        component grouping for RAG context chunking, not a real clustering.
        Fixed seed so the same graph always partitions the same way (same
        determinism rationale as the frontend's force-directed layout)."""
        graph, g, node_ids = self._build_igraph(tenant, limit)
        if g is None:
            return {
                "tenant": tenant.public_dict(),
                "approved": False,
                "communities": {},
                "community_count": 0,
                "modularity": None,
                "resolution": resolution,
            }
        partition = la.find_partition(
            g, la.RBConfigurationVertexPartition, resolution_parameter=resolution, seed=42,
        )
        communities = dict(zip(node_ids, partition.membership))

        return {
            "tenant": tenant.public_dict(),
            "approved": True,
            "communities": communities,
            "community_count": len(partition),
            "modularity": partition.modularity,
            "resolution": resolution,
            "scope": {
                "tenant_id": tenant.tenant_id,
                "node_count": len(graph.get("nodes") or []),
                "edge_count": len(graph.get("edges") or []),
            },
        }

    def graph_centrality_ranking(self, tenant, *, limit=300, method="betweenness", top_n=20):
        """Rank the tenant's approved-graph nodes by centrality (igraph, the
        same library graph_leiden_communities already depends on -- no
        second graph-algorithm library for overlapping purposes). Sibling
        of graph_leiden_communities: "which nodes cluster together" vs
        "which nodes are structurally most important/connective"."""
        methods = ("betweenness", "pagerank", "degree")
        if method not in methods:
            raise ValueError(f"method must be one of {methods}, got {method!r}")
        graph, g, node_ids = self._build_igraph(tenant, limit)
        if g is None:
            return {
                "tenant": tenant.public_dict(),
                "approved": False,
                "method": method,
                "ranking": [],
            }
        if method == "betweenness":
            scores = g.betweenness(directed=False)
        elif method == "pagerank":
            scores = g.pagerank(directed=False)
        else:
            scores = g.degree()
        ranking = sorted(
            ({"id": node_id, "score": score} for node_id, score in zip(node_ids, scores)),
            key=lambda entry: entry["score"],
            reverse=True,
        )[: max(0, int(top_n))]

        return {
            "tenant": tenant.public_dict(),
            "approved": True,
            "method": method,
            "ranking": ranking,
            "scope": {
                "tenant_id": tenant.tenant_id,
                "node_count": len(graph.get("nodes") or []),
                "edge_count": len(graph.get("edges") or []),
            },
        }

    def graph_rag_query_context(self, tenant, *, question, object_type=None, instance_id=None, depth=1, limit=80):
        question = str(question or "").strip()
        if object_type and instance_id:
            context = self.local_rag_context(
                tenant,
                object_type,
                instance_id,
                question=question,
                depth=depth,
                limit=limit,
            )
            if context:
                return {
                    **context,
                    "query_route": {
                        "route": "local",
                        "reason": "explicit_center",
                    },
                }
        if question:
            nodes = self._ontology_concrete_object_nodes(tenant)
            if nodes:
                session = sessionmaker(bind=self.metadata_engine_for(tenant))()
                try:
                    # Self-healing: only re-embed when the node count drifts
                    # from what's indexed (covers first-ever use and any
                    # additions/removals) -- see ontology_label_embeddings.py.
                    if label_embedding_count(session, tenant.tenant_id) != len(nodes):
                        sync_label_embeddings(session, tenant.tenant_id, nodes)
                    node_id = find_nearest_label(session, tenant.tenant_id, question)
                finally:
                    session.close()
                if node_id and ":" in node_id:
                    object_type, instance_id = node_id.split(":", 1)
                    context = self.local_rag_context(
                        tenant,
                        object_type,
                        instance_id,
                        question=question,
                        depth=depth,
                        limit=limit,
                    )
                    if context:
                        return {
                            **context,
                            "query_route": {
                                "route": "local",
                                "reason": "question_matched_approved_object_embedding",
                                "matched_node": node_id,
                            },
                        }
        communities = self.graph_community_summaries(tenant, limit=max(limit, 80))
        route_reason = "global_question"
        return {
            **communities,
            "question": question,
            "query_route": {
                "route": "global",
                "reason": route_reason,
            },
            "context_text": communities.get("summary_text") or "",
        }

    def _approved_artifacts(self, tenant, keys):
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT canonical_key, name, artifact_type, status, version, payload_json, description
                    FROM aletheia_ontology_artifacts
                    WHERE project_id = :tenant_id AND canonical_key = ANY(:keys) AND status = 'approved'
                    """
                ),
                {"tenant_id": tenant.tenant_id, "keys": list(keys)},
            ).mappings().all()
        return {row["canonical_key"]: dict(row) for row in rows}

    def _ensure_continuous_enrichment_schema(self, tenant):
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS aletheia_continuous_enrichment_sessions (
                        id SERIAL PRIMARY KEY,
                        project_id VARCHAR(255) NOT NULL DEFAULT 'default',
                        session_key VARCHAR(255) NOT NULL,
                        objective TEXT NOT NULL,
                        status VARCHAR(50) NOT NULL DEFAULT 'idle',
                        config_json TEXT NOT NULL DEFAULT '{}',
                        frontier_json TEXT NOT NULL DEFAULT '[]',
                        last_run_key VARCHAR(255),
                        cycle_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_continuous_enrichment_session_project_key
                    ON aletheia_continuous_enrichment_sessions (project_id, session_key)
                    """
                )
            )

    def _default_continuous_session(self, tenant):
        session_key = f"continuous:{tenant.tenant_id}:default"
        objective = ""
        config = {
            "mode": "bounded_autonomous",
            "research_mode": "frontier_enrichment",
            "research_topics": [],
            "retrieval_lanes": [
                "breaking_news",
                "official_sources",
                "academic",
                "think_tank",
                "industry",
                "historical_cases",
            ],
            "recency_windows": ["24h", "7d", "30d", "historical"],
            "max_queries_per_lane": 2,
            "run_mode": "scheduled_or_manual",
            "cadence": "manual",
            "custom_interval_minutes": 60,
            "rate_limit_per_cycle": 4,
            "stop_condition": "pause, stop, budget exhausted, or no new frontier",
            "research_provider": "gpt_researcher",
            "max_iterations": 1,
            "max_frontier": 4,
            "max_results_per_query": 4,
            "search_query_planner": "llm_with_fallback",
            "search_query_planner_model": DEFAULT_LLM_MODEL,
            "frontier_selector": "llm_with_fallback",
            "frontier_selector_model": DEFAULT_LLM_MODEL,
            "frontier_selector_shortlist": 20,
            "frontier_max_per_cluster": 2,
            "instance_coverage_min_edges": 0,
            "instance_coverage_min_enrichment_items": 2,
            "instance_coverage_per_type_limit": 75,
            "node_similarity_dedup_threshold": 0.6,
            "auto_review_similar_proposals": False,
            "auto_review_llm_verifier": True,
            "auto_review_model": DEFAULT_LLM_MODEL,
            "auto_reject_similarity_threshold": 0.92,
            "auto_approve_low_duplicate_proposals": False,
            "auto_approve_min_confidence": 0.8,
            "auto_approve_max_duplicate_score": 0.5,
            "auto_review_reviewer": "Continuous Enrichment Agent",
            "budget": {
                "max_frontier_per_cycle": 4,
                "max_results_per_query": 4,
                "max_iterations_per_cycle": 1,
                "max_cycles": None,
            },
            "backoff": {
                "failure_count": 0,
                "base_seconds": 60,
                "max_seconds": 3600,
                "backoff_until": None,
                "last_error": None,
            },
            "stop_policy": {
                "pause_on_no_frontier": True,
                "pause_on_budget_exhausted": True,
            },
            "visited_frontier_keys": [],
            "frontier_cooldown_minutes": 360,
            "frontier_priority_policy": [
                "new_graph_node_or_edge",
                "user_question_scope",
                "reasoning_finding_seed",
                "graph_coverage",
            ],
            "frontier_state": {"last_enriched_at": {}, "selected_count": {}, "coverage_cursor": 0},
            "latest_events": [],
            "canonical_writes": "disabled",
            "formal_graph_writes": "disabled",
            "ontology_review_required": True,
            "fact_graph_target": "proposed_graph_space",
            "finding_target": "candidate_findings",
        }
        frontier = []
        self._ensure_continuous_enrichment_schema(tenant)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_continuous_enrichment_sessions
                        (project_id, session_key, objective, status, config_json, frontier_json, updated_at)
                    VALUES
                        (:tenant_id, :session_key, :objective, 'idle', :config_json, :frontier_json, CURRENT_TIMESTAMP)
                    ON CONFLICT (project_id, session_key) DO NOTHING
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "objective": objective,
                    "config_json": _json_dump(config),
                    "frontier_json": _json_dump(frontier),
                },
            )
        return session_key

    def _continuous_research_mode(self, config):
        mode = str((config or {}).get("research_mode") or (config or {}).get("mode") or "").strip().lower()
        if mode in {"deep_research", "topic_research", "research"}:
            return "deep_research"
        return "frontier_enrichment"

    def _continuous_research_topics(self, objective, config, body=None):
        body = body or {}
        raw_topics = body.get("research_topics")
        if raw_topics is None:
            raw_topics = (config or {}).get("research_topics")
        topics = []
        if isinstance(raw_topics, str):
            raw_topics = [part.strip() for part in re.split(r"[\n;]+", raw_topics) if part.strip()]
        for topic in raw_topics or []:
            text = re.sub(r"\s+", " ", str(topic or "").strip())
            if text and text.lower() not in {item.lower() for item in topics}:
                topics.append(text)
        objective_text = re.sub(r"\s+", " ", str(body.get("objective") or objective or "").strip())
        if objective_text and not topics:
            topics.append(objective_text)
        return topics[:20]

    def _continuous_retrieval_lanes(self, config, body=None):
        body = body or {}
        default_lanes = [
            "breaking_news",
            "official_sources",
            "academic",
            "think_tank",
            "industry",
            "historical_cases",
        ]
        raw_lanes = body.get("retrieval_lanes")
        if raw_lanes is None:
            raw_lanes = (config or {}).get("retrieval_lanes") or default_lanes
        if isinstance(raw_lanes, str):
            raw_lanes = [part.strip() for part in re.split(r"[,\s]+", raw_lanes) if part.strip()]
        allowed = set(default_lanes)
        lanes = []
        for lane in raw_lanes or []:
            normalized = str(lane or "").strip().lower().replace("-", "_")
            if normalized in allowed and normalized not in lanes:
                lanes.append(normalized)
        return lanes or default_lanes

    def _continuous_recency_windows(self, config, body=None):
        body = body or {}
        raw_windows = body.get("recency_windows")
        if raw_windows is None:
            raw_windows = (config or {}).get("recency_windows") or ["24h", "7d", "30d", "historical"]
        if isinstance(raw_windows, str):
            raw_windows = [part.strip() for part in re.split(r"[,\s]+", raw_windows) if part.strip()]
        windows = []
        for window in raw_windows or []:
            normalized = str(window or "").strip().lower()
            if normalized in {"24h", "48h", "7d", "30d", "90d", "historical"} and normalized not in windows:
                windows.append(normalized)
        return windows or ["24h", "7d", "30d", "historical"]

    def _continuous_research_frontier_items(self, tenant, objective, config, body=None, max_frontier=4):
        topics = self._continuous_research_topics(objective, config, body)
        lanes = self._continuous_retrieval_lanes(config, body)
        windows = self._continuous_recency_windows(config, body)
        if not topics:
            return []
        items = []
        for topic in topics:
            key = f"research-topic:{tenant.tenant_id}:{_slug(topic)[:72].strip('-')}"
            item = {
                "key": key,
                "frontier_identity": f"research-topic:{topic.lower()}",
                "name": topic,
                "artifact_type": "research_topic",
                "kind": "research_topic",
                "source": "research_agenda",
                "source_kind": "research_topic",
                "priority": 120,
                "reason": "open-ended deep research topic should discover new developments, expert analysis, and graph expansion candidates",
                "depth": 0,
                "payload": {
                    "topic": topic,
                    "retrieval_lanes": lanes,
                    "recency_windows": windows,
                    "research_mode": "deep_research",
                    "tenant_id": tenant.tenant_id,
                },
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="research_topic", priority=120))
            if len(items) >= max_frontier:
                break
        return items

    def _continuous_update_config(self, config, body):
        config = dict(config or {})
        config.setdefault("auto_approve_low_duplicate_proposals", False)
        config.setdefault("auto_approve_min_confidence", 0.8)
        config.setdefault("auto_approve_max_duplicate_score", 0.5)
        for key in ("research_topic", "execution_goal"):
            if key in body:
                value = re.sub(r"\s+", " ", str(body.get(key) or "").strip())
                if value:
                    config[key] = value
                else:
                    config.pop(key, None)
        if "cadence" in body:
            cadence = (body.get("cadence") or "manual").strip()
            if cadence not in {"manual", "hourly", "daily", "custom"}:
                raise ValueError("cadence must be manual, hourly, daily, or custom")
            config["cadence"] = cadence
        if "custom_interval_minutes" in body:
            interval = max(1, min(int(body.get("custom_interval_minutes") or 60), 10080))
            config["custom_interval_minutes"] = interval
        if "search_query_planner" in body:
            planner = str(body.get("search_query_planner") or "llm_with_fallback").strip().lower()
            if planner not in {"llm_with_fallback", "llm", "deterministic", "fallback", "off", "disabled"}:
                raise ValueError("search_query_planner must be llm_with_fallback, llm, deterministic, fallback, off, or disabled")
            config["search_query_planner"] = planner
        if "research_mode" in body:
            mode = str(body.get("research_mode") or "frontier_enrichment").strip().lower()
            if mode not in {"frontier_enrichment", "deep_research", "topic_research", "research"}:
                raise ValueError("research_mode must be frontier_enrichment or deep_research")
            config["research_mode"] = "deep_research" if mode in {"deep_research", "topic_research", "research"} else "frontier_enrichment"
            if mode in {"deep_research", "topic_research", "research"}:
                config["mode"] = "deep_research"
        if "research_topics" in body:
            config["research_topics"] = self._continuous_research_topics("", config, body)
        if "retrieval_lanes" in body:
            config["retrieval_lanes"] = self._continuous_retrieval_lanes(config, body)
        if "recency_windows" in body:
            config["recency_windows"] = self._continuous_recency_windows(config, body)
        if "max_queries_per_lane" in body:
            config["max_queries_per_lane"] = max(1, min(int(body.get("max_queries_per_lane") or 2), 5))
        if "research_provider" in body:
            provider = str(body.get("research_provider") or "gpt_researcher").strip().lower().replace("-", "_")
            if provider != "gpt_researcher":
                raise ValueError("research_provider must be gpt_researcher")
            config["research_provider"] = "gpt_researcher"
        if "gpt_researcher_report_type" in body:
            report_type = str(body.get("gpt_researcher_report_type") or "research_report").strip()
            if report_type:
                config["gpt_researcher_report_type"] = report_type
        if "gpt_researcher_report_source" in body:
            report_source = str(body.get("gpt_researcher_report_source") or "").strip()
            config["gpt_researcher_report_source"] = report_source or None
        if "gpt_researcher_max_report_chars" in body:
            config["gpt_researcher_max_report_chars"] = max(4000, min(int(body.get("gpt_researcher_max_report_chars") or 24000), 100000))
        if "search_query_planner_model" in body:
            model = str(body.get("search_query_planner_model") or "").strip()
            if model:
                config["search_query_planner_model"] = model
        if "frontier_selector" in body:
            selector = str(body.get("frontier_selector") or "llm_with_fallback").strip().lower()
            if selector not in {"llm_with_fallback", "llm", "deterministic", "score", "off", "disabled"}:
                raise ValueError("frontier_selector must be llm_with_fallback, llm, deterministic, score, off, or disabled")
            config["frontier_selector"] = selector
        if "frontier_selector_model" in body:
            model = str(body.get("frontier_selector_model") or "").strip()
            if model:
                config["frontier_selector_model"] = model
        if "frontier_selector_shortlist" in body:
            config["frontier_selector_shortlist"] = max(1, min(int(body.get("frontier_selector_shortlist") or 20), 100))
        if "frontier_max_per_cluster" in body:
            config["frontier_max_per_cluster"] = max(1, min(int(body.get("frontier_max_per_cluster") or 2), 10))
        for key, upper_bound in (
            ("instance_coverage_min_edges", 50),
            ("instance_coverage_min_enrichment_items", 50),
            ("instance_coverage_per_type_limit", 500),
        ):
            if key in body:
                config[key] = max(0, min(int(body.get(key) or 0), upper_bound))
        if "instance_coverage_detail_fallback" in body:
            config["instance_coverage_detail_fallback"] = bool(body.get("instance_coverage_detail_fallback"))
        if "budget" in body:
            raw_budget = body.get("budget") or config.get("max_frontier") or 4
            budget_config = dict(config.get("budget") or {})
            if isinstance(raw_budget, dict):
                if "max_cycles" in raw_budget:
                    if raw_budget.get("max_cycles") in (None, "", 0):
                        budget_config["max_cycles"] = None
                    else:
                        budget_config["max_cycles"] = max(1, min(int(raw_budget.get("max_cycles") or 1), 1000))
                for source_key, target_key in (
                    ("max_frontier_per_cycle", "max_frontier_per_cycle"),
                    ("max_frontier", "max_frontier_per_cycle"),
                    ("max_results_per_query", "max_results_per_query"),
                    ("max_iterations_per_cycle", "max_iterations_per_cycle"),
                    ("max_iterations", "max_iterations_per_cycle"),
                ):
                    if source_key in raw_budget:
                        budget_config[target_key] = max(1, min(int(raw_budget.get(source_key) or 1), 50))
            else:
                budget_value = max(1, min(int(raw_budget or config.get("max_frontier") or 4), 25))
                budget_config["max_frontier_per_cycle"] = budget_value
                budget_config["max_results_per_query"] = max(1, min(int(config.get("max_results_per_query") or budget_value), budget_value))
            config["budget"] = budget_config
            budget = int(budget_config.get("max_frontier_per_cycle") or config.get("max_frontier") or 4)
            config["max_frontier"] = max(1, min(budget, 25))
            config["rate_limit_per_cycle"] = max(1, min(budget, 25))
        for key in ("max_iterations", "max_frontier", "max_results_per_query", "rate_limit_per_cycle"):
            if key in body:
                config[key] = max(1, min(int(body.get(key) or config.get(key) or 1), 50))
        if "frontier_cooldown_minutes" in body:
            config["frontier_cooldown_minutes"] = max(0, min(int(body.get("frontier_cooldown_minutes") or 0), 10080))
        if "node_similarity_dedup_threshold" in body:
            try:
                threshold = float(body.get("node_similarity_dedup_threshold"))
            except (TypeError, ValueError):
                raise ValueError("node_similarity_dedup_threshold must be a number between 0 and 1")
            config["node_similarity_dedup_threshold"] = round(max(0.0, min(threshold, 1.0)), 4)
        if "auto_review_similar_proposals" in body:
            config["auto_review_similar_proposals"] = bool(body.get("auto_review_similar_proposals"))
        if "auto_review_llm_verifier" in body:
            config["auto_review_llm_verifier"] = bool(body.get("auto_review_llm_verifier"))
        if "auto_review_model" in body:
            model = str(body.get("auto_review_model") or "").strip()
            if model:
                config["auto_review_model"] = model
        if "auto_reject_similarity_threshold" in body:
            try:
                threshold = float(body.get("auto_reject_similarity_threshold"))
            except (TypeError, ValueError):
                raise ValueError("auto_reject_similarity_threshold must be a number between 0 and 1")
            config["auto_reject_similarity_threshold"] = round(max(0.0, min(threshold, 1.0)), 4)
        if "auto_approve_low_duplicate_proposals" in body:
            config["auto_approve_low_duplicate_proposals"] = bool(body.get("auto_approve_low_duplicate_proposals"))
        if "auto_approve_min_confidence" in body:
            try:
                threshold = float(body.get("auto_approve_min_confidence"))
            except (TypeError, ValueError):
                raise ValueError("auto_approve_min_confidence must be a number between 0 and 1")
            config["auto_approve_min_confidence"] = round(max(0.0, min(threshold, 1.0)), 4)
        if "auto_approve_max_duplicate_score" in body:
            try:
                threshold = float(body.get("auto_approve_max_duplicate_score"))
            except (TypeError, ValueError):
                raise ValueError("auto_approve_max_duplicate_score must be a number between 0 and 1")
            config["auto_approve_max_duplicate_score"] = round(max(0.0, min(threshold, 1.0)), 4)
        if "auto_review_reviewer" in body:
            reviewer = str(body.get("auto_review_reviewer") or "").strip()
            if reviewer:
                config["auto_review_reviewer"] = reviewer
        if body.get("reset_frontier_visit_state"):
            config["visited_frontier_keys"] = []
            config["query_ladder_state"] = {}
            frontier_state = dict(config.get("frontier_state") or {})
            frontier_state["last_enriched_at"] = {}
            frontier_state["selected_count"] = {}
            frontier_state["coverage_cursor"] = 0
            config["frontier_state"] = frontier_state
            config["stop_reason"] = None
        if "stop_condition" in body:
            config["stop_condition"] = (body.get("stop_condition") or "").strip() or config.get("stop_condition")
        if "stop_policy" in body and isinstance(body.get("stop_policy"), dict):
            config["stop_policy"] = {**dict(config.get("stop_policy") or {}), **body["stop_policy"]}
        if "backoff" in body and isinstance(body.get("backoff"), dict):
            backoff = {**dict(config.get("backoff") or {}), **body["backoff"]}
            backoff["base_seconds"] = max(1, min(int(backoff.get("base_seconds") or 60), 86400))
            backoff["max_seconds"] = max(backoff["base_seconds"], min(int(backoff.get("max_seconds") or 3600), 604800))
            config["backoff"] = backoff
        return config

    def _continuous_retrieval_objective(self, row_objective, config, body):
        research_topic = re.sub(
            r"\s+",
            " ",
            str(body.get("research_topic") or (config or {}).get("research_topic") or "").strip(),
        )
        execution_goal = re.sub(
            r"\s+",
            " ",
            str(body.get("execution_goal") or (config or {}).get("execution_goal") or body.get("objective") or row_objective or "").strip(),
        )
        if research_topic:
            return research_topic, execution_goal
        fallback = str(body.get("objective") or row_objective or "").strip()
        terms = []
        for term in re.split(r"[^A-Za-z0-9_/-]+", fallback):
            value = term.strip()
            if len(value) < 4:
                continue
            lowered = value.lower().replace("_", " ").replace("-", " ")
            if lowered in {
                "gpt",
                "researcher",
                "expand",
                "using",
                "coverage",
                "summaries",
                "summary",
                "provider",
                "produce",
                "prioritize",
                "candidate",
                "candidates",
                "reviewable",
                "semantic",
                "ontology",
                "knowledge",
            }:
                continue
            terms.append(value)
        return " ".join(terms[:8]).strip(), execution_goal

    def _continuous_budget(self, config):
        budget = dict((config or {}).get("budget") or {})
        max_frontier = int(budget.get("max_frontier_per_cycle") or (config or {}).get("max_frontier") or (config or {}).get("rate_limit_per_cycle") or 4)
        max_results = int(budget.get("max_results_per_query") or (config or {}).get("max_results_per_query") or max_frontier)
        max_iterations = int(budget.get("max_iterations_per_cycle") or (config or {}).get("max_iterations") or 1)
        max_cycles = budget.get("max_cycles")
        return {
            "max_frontier_per_cycle": max(0, min(max_frontier, 50)),
            "max_results_per_query": max(0, min(max_results, 50)),
            "max_iterations_per_cycle": max(0, min(max_iterations, 50)),
            "max_cycles": None if max_cycles in (None, "", 0) else max(1, min(int(max_cycles), 1000)),
        }

    def _continuous_backoff_state(self, config):
        backoff = dict((config or {}).get("backoff") or {})
        backoff.setdefault("failure_count", 0)
        backoff.setdefault("base_seconds", 60)
        backoff.setdefault("max_seconds", 3600)
        backoff.setdefault("backoff_until", None)
        backoff.setdefault("last_error", None)
        return backoff

    def _continuous_backoff_active(self, config, now_ts=None):
        backoff = self._continuous_backoff_state(config)
        until_ts = self._continuous_parse_iso_ts(backoff.get("backoff_until"))
        if until_ts is None:
            return None
        now_ts = time.time() if now_ts is None else now_ts
        if now_ts >= until_ts:
            return None
        return {"backoff_until": backoff.get("backoff_until"), "remaining_seconds": int(until_ts - now_ts)}

    def _continuous_schedule_backoff(self, config, error):
        config = dict(config or {})
        backoff = self._continuous_backoff_state(config)
        failure_count = int(backoff.get("failure_count") or 0) + 1
        base_seconds = max(1, int(backoff.get("base_seconds") or 60))
        max_seconds = max(base_seconds, int(backoff.get("max_seconds") or 3600))
        delay_seconds = min(base_seconds * (2 ** (failure_count - 1)), max_seconds)
        backoff.update(
            {
                "failure_count": failure_count,
                "last_error": _safe_error_message(error),
                "last_failed_at": datetime.utcnow().isoformat(),
                "backoff_until": datetime.utcfromtimestamp(time.time() + delay_seconds).isoformat(),
                "delay_seconds": delay_seconds,
            }
        )
        config["backoff"] = backoff
        return config, backoff

    def _continuous_clear_backoff(self, config):
        config = dict(config or {})
        backoff = self._continuous_backoff_state(config)
        backoff.update({"failure_count": 0, "backoff_until": None, "last_error": None, "delay_seconds": 0})
        config["backoff"] = backoff
        return config

    def _continuous_cadence_seconds(self, config):
        cadence = (config or {}).get("cadence") or "manual"
        if cadence == "hourly":
            return 3600
        if cadence == "daily":
            return 86400
        if cadence == "custom":
            return max(1, int((config or {}).get("custom_interval_minutes") or 60)) * 60
        return None

    def _continuous_next_run_at(self, config):
        seconds = self._continuous_cadence_seconds(config)
        if not seconds:
            return None
        return datetime.utcfromtimestamp(time.time() + seconds).isoformat()

    def _continuous_session_auto_due(self, status, config, now_ts=None):
        now_ts = time.time() if now_ts is None else now_ts
        if status != "idle":
            return False, f"status_{status or 'unknown'}"
        if not self._continuous_cadence_seconds(config):
            return False, "manual_cadence"
        active_backoff = self._continuous_backoff_active(config, now_ts=now_ts)
        if active_backoff:
            return False, "backoff_active"
        next_ts = self._continuous_parse_iso_ts((config or {}).get("next_run_at"))
        if next_ts is None:
            return True, "cadence_without_next_run_at"
        if now_ts >= next_ts:
            return True, "next_run_due"
        return False, "next_run_pending"

    def _continuous_frontier_state(self, config):
        state = config.get("frontier_state") if isinstance(config.get("frontier_state"), dict) else {}
        state.setdefault("last_enriched_at", {})
        state.setdefault("selected_count", {})
        state.setdefault("coverage_cursor", 0)
        return state

    def _continuous_frontier_cooldown_seconds(self, config):
        minutes = int((config or {}).get("frontier_cooldown_minutes") or 360)
        return max(0, min(minutes, 10080)) * 60

    def _continuous_parse_iso_ts(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
            return (parsed - datetime(1970, 1, 1)).total_seconds()
        except Exception:
            return None

    def _continuous_running_stale_after_seconds(self, config=None):
        config = config or {}
        raw = config.get("running_stale_after_seconds") or CONTINUOUS_RUNNING_STALE_SECONDS
        try:
            return max(60, int(raw))
        except (TypeError, ValueError):
            return CONTINUOUS_RUNNING_STALE_SECONDS

    def _continuous_running_stale(self, status, config, now_ts=None):
        if status != "running":
            return None
        config = config or {}
        started_ts = self._continuous_parse_iso_ts(config.get("last_started_at"))
        if started_ts is None:
            return None
        now_ts = time.time() if now_ts is None else now_ts
        stale_after = self._continuous_running_stale_after_seconds(config)
        elapsed = max(0, int(now_ts - started_ts))
        if elapsed < stale_after:
            return None
        return {
            "elapsed_seconds": elapsed,
            "stale_after_seconds": stale_after,
            "last_started_at": config.get("last_started_at"),
        }

    def _recover_stale_continuous_session_row(self, tenant, row, now_ts=None):
        if row is None:
            return None
        config = _load_json(row["config_json"], {})
        stale = self._continuous_running_stale(row["status"], config, now_ts=now_ts)
        if not stale:
            return row
        now_iso = datetime.utcnow().isoformat()
        config["last_finished_at"] = now_iso
        config["stop_reason"] = "recovered stale running session"
        self._continuous_append_events(
            config,
            [
                {
                    "type": "stale_running_recovered",
                    "reason": "session was marked running but no cycle completed within the stale threshold",
                    "last_started_at": stale["last_started_at"],
                    "elapsed_seconds": stale["elapsed_seconds"],
                    "stale_after_seconds": stale["stale_after_seconds"],
                    "created_at": now_iso,
                }
            ],
        )
        config_json = _json_dump(config)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = 'idle',
                        config_json = :config_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id
                      AND session_key = :session_key
                      AND status = 'running'
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": row["session_key"],
                    "config_json": config_json,
                },
            )
        recovered = dict(row)
        recovered["status"] = "idle"
        recovered["config_json"] = config_json
        return recovered

    def _continuous_frontier_key(self, item):
        return str(item.get("key") or item.get("target_key") or item.get("name") or "").strip()

    def _continuous_frontier_identity(self, item):
        key = self._continuous_frontier_key(item)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        kind = str(item.get("kind") or item.get("artifact_type") or item.get("source_kind") or "").lower()
        if "edge" in kind or item.get("source_kind") == "new_graph_edge":
            endpoint_evidence = payload.get("endpoint_dedup_evidence") if isinstance(payload.get("endpoint_dedup_evidence"), dict) else {}

            def _endpoint_identity(role):
                evidence = endpoint_evidence.get(role) if isinstance(endpoint_evidence.get(role), dict) else {}
                return str(
                    evidence.get("identity_key")
                    or evidence.get("matched_node_key")
                    or payload.get(f"{role}_identity_key")
                    or payload.get(f"{role}_key")
                    or payload.get(f"{role}_label")
                    or payload.get(role)
                    or ""
                ).strip().lower()

            relation_candidate = (
                payload.get("relation_ontology_candidate")
                if isinstance(payload.get("relation_ontology_candidate"), dict)
                else {}
            )
            relation = str(
                relation_candidate.get("canonical_key")
                or relation_candidate.get("key")
                or payload.get("link_key")
                or payload.get("relation_key")
                or payload.get("relation")
                or ""
            ).strip().lower()
            stable_fact = payload.get("stable_fact_identity") or payload.get("fact_identity") or payload.get("metric_identity")
            if stable_fact is None:
                stable_fact = payload.get("metric") or payload.get("metrics") or ""
            if isinstance(stable_fact, (list, tuple, set)):
                stable_fact = "|".join(sorted(str(value).strip().lower() for value in stable_fact if str(value).strip()))
            else:
                stable_fact = str(stable_fact or "").strip().lower()
            source = _endpoint_identity("source")
            target = _endpoint_identity("target")
            if source and relation and target:
                tenant_id = payload.get("tenant") or payload.get("tenant_id") or (key.split(":")[1] if key.startswith("proposed-graph:") and len(key.split(":")) > 2 else "")
                return f"edge-fact:{tenant_id}:{source}:{relation}:{target}:{stable_fact}"
        if "node" in kind or item.get("source_kind") == "new_graph_node":
            identity = (
                payload.get("identity_key")
                or payload.get("graph_identity")
                or payload.get("node_identity")
                or payload.get("matched_node_key")
            )
            if identity:
                return f"node:{str(identity).strip().lower()}"
            label = str(payload.get("label") or payload.get("name") or item.get("name") or "").strip().lower()
            ontology_type = str(payload.get("ontology_type") or payload.get("type") or item.get("ontology_type") or "").strip().lower()
            if label:
                return f"node:{ontology_type}:{label}"
        if item.get("source_kind") == "instance_coverage":
            identity = payload.get("identity_key") or payload.get("node_id") or item.get("target_key")
            if identity:
                return f"node:{str(identity).strip().lower()}"
        return key

    def _continuous_frontier_name(self, item):
        return str(item.get("name") or item.get("target_key") or item.get("key") or "frontier").strip()

    def _continuous_source_priority(self, source_kind):
        return {
            "loop_harness_relation_completion": 135,
            "loop_harness_property_completion": 130,
            "instance_coverage": 125,
            "new_graph_edge": 90,
            "new_graph_node": 55,
            "research_topic": 120,
            "user_question_scope": 80,
            "reasoning_finding_seed": 60,
            "graph_coverage": 20,
        }.get(source_kind or "", 10)

    def _continuous_source_reason(self, source_kind):
        return {
            "new_graph_node": "new proposed graph node has not been enriched yet",
            "new_graph_edge": "new proposed graph edge/path has not been enriched yet",
            "research_topic": "open-ended deep research topic should discover new developments and expert analysis",
            "instance_coverage": "approved instance has sparse or missing relation coverage",
            "user_question_scope": "user scoped reasoning question is an active research focus",
            "reasoning_finding_seed": "reasoning finding suggests a path that needs more evidence or expansion",
            "loop_harness_relation_completion": "approved ontology object has sparse or missing relation coverage",
            "loop_harness_property_completion": "approved ontology object has sparse or missing property coverage",
            "graph_coverage": "coverage fallback is rotating through graph items after higher-priority seeds",
        }.get(source_kind or "", "continuous enrichment frontier")

    def _continuous_proposed_status_is_frontier_eligible(self, status):
        return str(status or "").replace("-", "_").lower() in {"draft", "needs_more_evidence"}

    def _continuous_frontier_item_is_storage_node(self, item):
        item = item or {}
        source_kind = str(item.get("source_kind") or "").strip().lower()
        kind = str(item.get("kind") or item.get("artifact_type") or "").strip().lower()
        key = str(self._continuous_frontier_key(item) or "").strip().lower()
        return (
            source_kind == "new_graph_node"
            or kind == "proposed_node"
            or key.startswith("proposed-graph:node:")
            or ":node:" in key
        )

    def _continuous_normalize_frontier_item(self, item, *, source_kind=None, priority=None, reason=None):
        item = dict(item or {})
        if source_kind is None:
            source_kind = item.get("source_kind")
        if source_kind is None:
            kind = str(item.get("kind") or item.get("artifact_type") or "").lower()
            if kind == "proposed_node":
                source_kind = "new_graph_node"
            elif kind == "proposed_edge":
                source_kind = "new_graph_edge"
            elif item.get("source") in {"loop_harness_relation_completion", "loop_harness_property_completion"}:
                source_kind = item.get("source")
            elif kind == "ontology_concrete_object":
                source_kind = "loop_harness_relation_completion"
            else:
                source_kind = "graph_coverage"
        item["source_kind"] = source_kind
        if priority is not None:
            normalized_priority = float(priority)
        else:
            normalized_priority = max(float(item.get("priority") or 0), float(self._continuous_source_priority(source_kind)))
        item["priority"] = normalized_priority
        item["reason"] = reason or item.get("reason") or self._continuous_source_reason(source_kind)
        return item

    def _hydrate_continuous_frontier_item(self, tenant, item):
        key = self._continuous_frontier_key(item)
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else None
        if payload or not key:
            return self._continuous_normalize_frontier_item(item)
        if not key.startswith("proposed-graph:"):
            return self._continuous_normalize_frontier_item(item)
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence,
                               e.status, e.iteration, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        LEFT JOIN aletheia_iterative_graph_enrichment_runs r
                          ON e.run_id = r.id AND e.project_id = r.project_id
                        WHERE e.project_id = :tenant_id AND e.element_key = :element_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "element_key": key},
                ).mappings().first()
        except Exception:
            return self._continuous_normalize_frontier_item(item)
        if not row:
            return self._continuous_normalize_frontier_item(item)
        if str(row["element_type"] or "").strip().lower() == "node":
            return None
        payload = _load_json(row["payload_json"], {})
        source_kind = item.get("source_kind")
        if source_kind is None and item.get("source") in {"loop_harness_relation_completion", "loop_harness_property_completion"}:
            source_kind = item.get("source")
        ontology_part = str(payload.get("ontology_part") or "").strip().lower()
        artifact_type = str(payload.get("artifact_type") or "").strip().lower()
        is_loop_relation_object = (
            source_kind in {"loop_harness_relation_completion", "loop_harness_property_completion"}
            and str(row["status"] or "").replace("-", "_").lower() == "approved"
            and artifact_type == "object"
            and ontology_part == "concrete_object"
        )
        has_loop_completion_class = bool(
            str(payload.get("object_type") or payload.get("class_label") or payload.get("ontology_type") or "").strip()
        )
        if source_kind in {"loop_harness_relation_completion", "loop_harness_property_completion"} and not is_loop_relation_object:
            return None
        if source_kind in {"loop_harness_relation_completion", "loop_harness_property_completion"} and not has_loop_completion_class:
            return None
        if not is_loop_relation_object and not self._continuous_proposed_status_is_frontier_eligible(row["status"]):
            return None
        hydrated = dict(item)
        deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
        hydrated.update(
            {
                "key": row["element_key"],
                "name": row["name"] or hydrated.get("name") or row["element_key"],
                "artifact_type": hydrated.get("artifact_type") or f"proposed_{row['element_type']}",
                "kind": hydrated.get("kind") or f"proposed_{row['element_type']}",
                "source": hydrated.get("source") or "proposed_graph",
                "source_run_key": hydrated.get("source_run_key") or row["run_key"],
                "confidence": hydrated.get("confidence") if hydrated.get("confidence") is not None else row["confidence"],
                "depth": int(hydrated.get("depth") or row["iteration"] or 0),
                "evidence_refs": hydrated.get("evidence_refs") or _load_json(row["evidence_refs_json"], []),
                "source_url": hydrated.get("source_url") or row["source_url"],
                "ontology_type": hydrated.get("ontology_type") or payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
                "payload": payload,
                "path": hydrated.get("path")
                or deep_profile.get("path_label")
                or payload.get("path_label")
                or (
                    f"{payload.get('source_label')} -> {payload.get('relation') or 'related_to'} -> {payload.get('target_label')}"
                    if payload.get("source_label") and payload.get("target_label")
                    else None
                ),
                "relation": hydrated.get("relation") or payload.get("relation"),
            }
        )
        if not source_kind:
            source_kind = "new_graph_edge" if row["element_type"] == "edge" else "new_graph_node"
        return self._continuous_normalize_frontier_item(hydrated, source_kind=source_kind)

    def _continuous_row_to_frontier_item(self, row, source_kind):
        payload = _load_json(row["payload_json"], {})
        deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
        item = {
            "key": row["element_key"],
            "name": row["name"] or row["element_key"],
            "artifact_type": f"proposed_{row['element_type']}",
            "kind": f"proposed_{row['element_type']}",
            "source": "proposed_graph",
            "source_kind": source_kind,
            "source_run_key": row.get("run_key"),
            "confidence": row.get("confidence"),
            "depth": int(row.get("iteration") or 0),
            "evidence_refs": _load_json(row.get("evidence_refs_json"), []),
            "source_url": row.get("source_url"),
            "ontology_type": payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
            "payload": payload,
            "path": deep_profile.get("path_label")
            or payload.get("path_label")
            or (
                f"{payload.get('source_label')} -> {payload.get('relation') or 'related_to'} -> {payload.get('target_label')}"
                if payload.get("source_label") and payload.get("target_label")
                else None
            ),
            "relation": payload.get("relation"),
        }
        return self._continuous_normalize_frontier_item(item, source_kind=source_kind)

    def _continuous_proposed_graph_frontier(self, tenant, config, limit=50):
        state = self._continuous_frontier_state(config)
        enriched = state.get("last_enriched_at") or {}
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence,
                               e.status, e.iteration, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        LEFT JOIN aletheia_iterative_graph_enrichment_runs r
                          ON e.run_id = r.id AND e.project_id = r.project_id
                        WHERE e.project_id = :tenant_id
                          AND e.element_type = 'edge'
                          AND e.status IN ('draft', 'needs_more_evidence')
                        ORDER BY e.created_at DESC, e.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            return []
        items = []
        for row in rows:
            is_new = row["element_key"] not in enriched
            if is_new:
                source_kind = "new_graph_edge"
            else:
                source_kind = "graph_coverage"
            items.append(self._continuous_row_to_frontier_item(row, source_kind))
        return items

    def _continuous_instance_coverage_frontier(self, tenant, config=None, limit=50):
        config = config or {}
        min_relation_edges = max(0, int(config.get("instance_coverage_min_edges") or 0))
        min_enrichment_items = max(0, int(config.get("instance_coverage_min_enrichment_items") or 2))
        per_type_limit = max(1, min(int(config.get("instance_coverage_per_type_limit") or limit or 50), 200))
        detail_fallback = bool(config.get("instance_coverage_detail_fallback"))
        projected_relations = {}
        if min_relation_edges > 0:
            try:
                graph = self.full_graph(tenant, limit=max(50, min(per_type_limit * 4, 1000))) or {}
            except Exception:
                graph = {}
            for edge in graph.get("edges") or []:
                source = edge.get("source")
                target = edge.get("target")
                relation = str(edge.get("label") or edge.get("kind") or "relation")
                for node_id, related_node in ((source, target), (target, source)):
                    if not node_id:
                        continue
                    summary = projected_relations.setdefault(
                        node_id,
                        {
                            "nodes": set(),
                            "edges": 0,
                            "by_relation": {},
                            "projection_source": "ApprovedGraphProjection",
                        },
                    )
                    summary["edges"] += 1
                    if related_node:
                        summary["nodes"].add(related_node)
                    summary["by_relation"][relation] = int(summary["by_relation"].get(relation) or 0) + 1
        try:
            type_rows = self.types(tenant, include_draft=False).get("types") or []
        except Exception:
            return []
        instance_records = []
        for type_info in type_rows:
            object_type = type_info.get("type")
            if not object_type:
                continue
            try:
                instances = self.search(tenant, object_type, "", limit=per_type_limit, include_draft=False).get("instances") or []
            except Exception:
                instances = []
            for instance in instances:
                node_id = str(instance.get("id") or "").strip()
                if not node_id:
                    continue
                instance_id = node_id.split(":", 1)[1] if ":" in node_id else str(instance.get("source_pk") or "").split("=", 1)[-1]
                relation_count = 0
                relations_summary = {}
                if min_relation_edges > 0:
                    projected_summary = projected_relations.get(node_id)
                    if projected_summary:
                        relation_count = int(projected_summary.get("edges") or 0)
                        relations_summary = {
                            "nodes": len(projected_summary.get("nodes") or []),
                            "edges": relation_count,
                            "by_relation": dict(projected_summary.get("by_relation") or {}),
                            "projection_source": projected_summary.get("projection_source"),
                        }
                    elif detail_fallback:
                        try:
                            detail = self.detail(tenant, object_type, instance_id) if instance_id else None
                            relations_summary = (detail or {}).get("relations_summary") if isinstance((detail or {}).get("relations_summary"), dict) else {}
                            relation_count = int(relations_summary.get("edges") or 0)
                        except Exception:
                            relation_count = 0
                            relations_summary = {}
                label = str(instance.get("label") or instance.get("name") or instance_id or node_id)
                instance_records.append(
                    {
                        "type_info": type_info,
                        "object_type": object_type,
                        "instance": instance,
                        "node_id": node_id,
                        "instance_id": instance_id,
                        "label": label,
                        "relation_count": relation_count,
                        "relations_summary": relations_summary,
                    }
                )
        enrichment_counts = self._continuous_instance_enrichment_counts(tenant, instance_records)
        candidates = []
        for record in instance_records:
            type_info = record["type_info"]
            object_type = record["object_type"]
            instance = record["instance"]
            node_id = record["node_id"]
            instance_id = record["instance_id"]
            label = record["label"]
            relation_count = record["relation_count"]
            relations_summary = record["relations_summary"]
            enrichment_count = enrichment_counts.get(node_id, 0)
            relation_gap = max(0, min_relation_edges - relation_count)
            enrichment_gap = max(0, min_enrichment_items - enrichment_count)
            if relation_gap <= 0 and enrichment_gap <= 0:
                continue
            source_pk = instance.get("source_pk") or (f"id={instance_id}" if instance_id else "")
            candidates.append(
                self._continuous_normalize_frontier_item(
                    {
                        "key": f"instance-coverage:{node_id}",
                        "name": f"{label} {object_type} enrichment coverage",
                        "artifact_type": "approved_instance_coverage_gap",
                        "kind": "approved_instance_coverage_gap",
                        "source": "approved_graph_instance",
                        "source_kind": "instance_coverage",
                        "priority": 70 + min((relation_gap + enrichment_gap) * 8, 32),
                        "reason": "approved instance has fewer reviewed relations or enrichment outputs than the configured coverage threshold",
                        "depth": 0,
                        "ontology_type": object_type,
                        "target_key": node_id,
                        "payload": {
                            "label": label,
                            "ontology_type": object_type,
                            "identity_key": node_id,
                            "node_id": node_id,
                            "source_table": instance.get("source_table") or type_info.get("table"),
                            "source_pk": source_pk,
                            "ontology_artifact": instance.get("ontology_artifact") or type_info.get("ontology_artifact"),
                            "relation_count": relation_count,
                            "enrichment_count": enrichment_count,
                            "min_relation_edges": min_relation_edges,
                            "min_enrichment_items": min_enrichment_items,
                            "relation_coverage_gap": relation_gap,
                            "enrichment_coverage_gap": enrichment_gap,
                            "coverage_gap": relation_gap + enrichment_gap,
                            "relations_summary": relations_summary,
                            "selection_policy": "approved_instance_relation_or_enrichment_gap_coverage",
                        },
                    },
                    source_kind="instance_coverage",
                )
            )
        candidates.sort(
            key=lambda item: (
                int((item.get("payload") or {}).get("relation_count") or 0),
                str(item.get("ontology_type") or ""),
                str((item.get("payload") or {}).get("label") or item.get("name") or ""),
            )
        )
        return candidates[: max(0, int(limit or 50))]

    def _continuous_instance_enrichment_counts(self, tenant, instance_records):
        needle_sets = {}
        unique_needles = []
        seen_needles = set()
        for record in instance_records or []:
            node_id = record.get("node_id")
            label = record.get("label")
            object_type = record.get("object_type")
            needles = {
                str(node_id or "").strip().lower(),
                str(label or "").strip().lower(),
                f"{str(object_type or '').strip().lower()}:{str(label or '').strip().lower()}",
            }
            needles = {value for value in needles if value and len(value) >= 3}
            if not node_id or not needles:
                continue
            needle_sets[node_id] = needles
            for needle in needles:
                if needle not in seen_needles:
                    seen_needles.add(needle)
                    unique_needles.append(needle)
        if not unique_needles:
            return {}
        clauses = []
        params = {"tenant_id": tenant.tenant_id}
        for index, needle in enumerate(unique_needles[:500]):
            key = f"needle_{index}"
            clauses.append(f"LOWER(CAST(payload_json AS TEXT)) LIKE :{key}")
            params[key] = f"%{needle}%"
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT element_key, LOWER(CAST(payload_json AS TEXT)) AS payload_text
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND element_type NOT IN ('node', 'edge', 'finding')
                          AND COALESCE(status, '') NOT IN ('rejected', 'duplicate')
                          AND ({' OR '.join(clauses)})
                        """
                    ),
                    params,
                ).mappings().all()
        except Exception:
            return {}
        matches = {node_id: set() for node_id in needle_sets}
        for row in rows:
            payload_text = row.get("payload_text") or ""
            element_key = row.get("element_key")
            for node_id, needles in needle_sets.items():
                if any(needle in payload_text for needle in needles):
                    matches.setdefault(node_id, set()).add(element_key)
        return {node_id: len(keys) for node_id, keys in matches.items()}

    def _continuous_graph_coverage_frontier(self, tenant, config=None, limit=50):
        items = []
        frontier_state = self._continuous_frontier_state(config or {})
        coverage_cursor = max(0, int(frontier_state.get("coverage_cursor") or 0))
        try:
            graph = self.full_graph(tenant, limit=max(50, min(int(limit) * 4, 200))) or {}
        except Exception:
            graph = {}
        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []
        degree = {}
        for edge in edges:
            source = edge.get("source")
            target = edge.get("target")
            if source:
                degree[source] = degree.get(source, 0) + 1
            if target:
                degree[target] = degree.get(target, 0) + 1
        ranked_nodes = sorted(nodes, key=lambda item: (-degree.get(item.get("id"), 0), str(item.get("label") or item.get("id") or "")))
        for node in ranked_nodes[coverage_cursor:]:
            node_id = node.get("id")
            if not node_id:
                continue
            node_type = node.get("type") or node.get("label_type") or node.get("tag")
            label = node.get("label") or node.get("name") or node_id
            items.append(
                self._continuous_normalize_frontier_item(
                    {
                        "key": f"graph-coverage:{node_id}",
                        "name": label,
                        "artifact_type": "graph_node_coverage",
                        "kind": "graph_node_coverage",
                        "source": "approved_graph",
                        "source_kind": "graph_coverage",
                        "priority": 20 + min(degree.get(node_id, 0), 50) / 10,
                        "reason": "approved graph node selected by degree-based coverage fallback",
                        "depth": 0,
                        "ontology_type": node_type,
                        "payload": {
                            "label": label,
                            "ontology_type": node_type,
                            "identity_key": node_id,
                            "degree": degree.get(node_id, 0),
                            "selection_policy": "degree_coverage",
                        },
                    },
                    source_kind="graph_coverage",
                )
            )
            if len(items) >= limit:
                break
        remaining = max(0, int(limit) - len(items))
        if remaining <= 0:
            return items
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence,
                               e.status, e.iteration, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        LEFT JOIN aletheia_iterative_graph_enrichment_runs r
                          ON e.run_id = r.id AND e.project_id = r.project_id
                        WHERE e.project_id = :tenant_id
                          AND e.element_type = 'node'
                          AND e.status IN ('draft', 'needs_more_evidence')
                        ORDER BY e.updated_at DESC, e.created_at DESC, e.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": remaining},
                ).mappings().all()
        except Exception:
            rows = []
        for row in rows:
            item = self._continuous_row_to_frontier_item(row, "graph_coverage")
            item["source"] = "proposed_graph_recent_node"
            item["reason"] = "recent graph node selected by coverage fallback"
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            payload["selection_policy"] = "recent_node_coverage"
            item["payload"] = payload
            items.append(item)
        return items[:limit]

    def _continuous_question_scope_frontier(self, tenant, limit=10):
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT canonical_key, question, scope_json, status, updated_at
                        FROM aletheia_reasoning_tasks
                        WHERE project_id = :tenant_id
                          AND status NOT IN ('closed', 'deleted')
                        ORDER BY updated_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            return []
        items = []
        for row in rows:
            scope = _load_json(row["scope_json"], {})
            graph_node = scope.get("graph_node") if isinstance(scope.get("graph_node"), dict) else {}
            graph_edge = scope.get("graph_edge") if isinstance(scope.get("graph_edge"), dict) else {}
            center_node = scope.get("center_node")
            center_type = scope.get("center_type") or scope.get("type") or graph_node.get("type")
            center_id = scope.get("center_id") or scope.get("id") or graph_node.get("id")
            if center_node and ":" in str(center_node) and not (center_type and center_id):
                center_type, center_id = str(center_node).split(":", 1)
            source_label = graph_edge.get("source_label") or graph_edge.get("source")
            target_label = graph_edge.get("target_label") or graph_edge.get("target")
            relation = graph_edge.get("relation")
            name = graph_edge.get("name") or graph_node.get("label") or (f"{center_type}:{center_id}" if center_type and center_id else row["question"])
            payload = {
                "label": graph_node.get("label") or name,
                "source_label": source_label,
                "target_label": target_label,
                "relation": relation,
                "ontology_type": center_type,
                "question": row["question"],
            }
            item = {
                "key": f"user-question:{row['canonical_key']}",
                "name": name,
                "artifact_type": "user_question_scope",
                "kind": "question_scope",
                "source": "reasoning_task",
                "source_kind": "user_question_scope",
                "related_question_key": row["canonical_key"],
                "payload": {key: value for key, value in payload.items() if value},
                "path": graph_edge.get("path_label") or scope.get("path_label"),
                "ontology_type": center_type,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="user_question_scope"))
        return items

    def _continuous_reasoning_finding_frontier(self, tenant, limit=20):
        items = []
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT f.canonical_key, f.title, f.conclusion, f.confidence,
                               f.supporting_evidence_json, f.recommended_action_json,
                               f.status, t.canonical_key AS task_key
                        FROM aletheia_reasoning_findings f
                        JOIN aletheia_reasoning_runs r ON f.run_id = r.id
                        JOIN aletheia_reasoning_tasks t ON r.task_id = t.id
                        WHERE f.project_id = :tenant_id
                          AND f.status IN ('draft', 'approved', 'needs_more_evidence', 'reaffirmed')
                        ORDER BY f.confidence DESC, f.updated_at DESC, f.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            rows = []
        for row in rows:
            evidence = _load_json(row["supporting_evidence_json"], [])
            action = _load_json(row["recommended_action_json"], {})
            first_path = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            item = {
                "key": f"reasoning-finding:{row['canonical_key']}",
                "name": row["title"],
                "artifact_type": "reasoning_finding_seed",
                "kind": "finding_seed",
                "source": "reasoning_finding",
                "source_kind": "reasoning_finding_seed",
                "related_finding_key": row["canonical_key"],
                "related_question_key": row.get("task_key"),
                "confidence": row.get("confidence"),
                "payload": {
                    "label": row["title"],
                    "summary": row["conclusion"],
                    "metrics": first_path.get("metrics") or first_path.get("metric"),
                    "source_label": first_path.get("source_label"),
                    "target_label": first_path.get("target_label"),
                    "relation": first_path.get("relation"),
                    "recommended_action": action,
                },
                "path": first_path.get("path_label") or first_path.get("path"),
                "evidence_refs": evidence,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="reasoning_finding_seed"))
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT c.canonical_key, c.title, c.summary, c.value_score,
                               c.evidence_chain_json, c.status, s.session_key
                        FROM aletheia_autopilot_candidate_findings c
                        JOIN aletheia_autopilot_sessions s ON c.session_id = s.id
                        WHERE c.project_id = :tenant_id
                          AND c.status IN ('draft', 'needs_more_evidence')
                        ORDER BY c.value_score DESC, c.updated_at DESC, c.id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": int(limit)},
                ).mappings().all()
        except Exception:
            rows = []
        for row in rows:
            evidence = _load_json(row["evidence_chain_json"], [])
            first_path = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            item = {
                "key": f"reasoning-finding:{row['canonical_key']}",
                "name": row["title"],
                "artifact_type": "reasoning_finding_seed",
                "kind": "candidate_finding_seed",
                "source": "autopilot_candidate_finding",
                "source_kind": "reasoning_finding_seed",
                "related_finding_key": row["canonical_key"],
                "related_run": row.get("session_key"),
                "confidence": row.get("value_score"),
                "payload": {
                    "label": row["title"],
                    "summary": row["summary"],
                    "metrics": first_path.get("metrics") or first_path.get("metric"),
                    "source_label": first_path.get("source_label"),
                    "target_label": first_path.get("target_label"),
                    "relation": first_path.get("relation"),
                },
                "path": first_path.get("path_label") or first_path.get("path"),
                "evidence_refs": evidence,
            }
            items.append(self._continuous_normalize_frontier_item(item, source_kind="reasoning_finding_seed"))
        return items

    def _continuous_frontier_available(self, item, config, now_ts):
        key = self._continuous_frontier_key(item)
        if not key:
            return False
        state = self._continuous_frontier_state(config)
        last_ts = self._continuous_parse_iso_ts((state.get("last_enriched_at") or {}).get(key))
        if last_ts is None:
            return True
        return now_ts - last_ts >= self._continuous_frontier_cooldown_seconds(config)

    def _continuous_frontier_candidates(self, tenant, stored_frontier, config):
        candidates = []
        if self._continuous_research_mode(config) == "deep_research":
            candidates = self._continuous_research_frontier_items(
                tenant,
                (config or {}).get("_frontier_selection_objective") or "",
                config,
                max_frontier=max(1, min(int((config or {}).get("max_frontier") or 4), 20)),
            )
            return candidates
        for item in stored_frontier or []:
            hydrated = self._hydrate_continuous_frontier_item(tenant, item)
            if hydrated:
                candidates.append(hydrated)
        candidates.extend(self._continuous_proposed_graph_frontier(tenant, config, limit=75))
        candidates.extend(self._continuous_question_scope_frontier(tenant, limit=10))
        candidates.extend(self._continuous_reasoning_finding_frontier(tenant, limit=20))
        candidates.extend(self._continuous_instance_coverage_frontier(tenant, config=config, limit=75))
        candidates.extend(self._continuous_graph_coverage_frontier(tenant, config=config, limit=50))
        deduped = {}
        identities = {}
        for item in candidates:
            if not item:
                continue
            normalized = self._continuous_normalize_frontier_item(item)
            key = self._continuous_frontier_key(normalized)
            if not key:
                continue
            identity = self._continuous_frontier_identity(normalized) or key
            existing_key = identities.get(identity)
            existing = deduped.get(existing_key or key)
            if existing is None or normalized.get("priority", 0) > existing.get("priority", 0):
                if existing_key and existing_key != key:
                    deduped.pop(existing_key, None)
                deduped[key] = normalized
                identities[identity] = key
        return list(deduped.values())

    def _continuous_frontier_searchability_score(self, item):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        labels = [
            item.get("name"),
            payload.get("label"),
            payload.get("source_label"),
            payload.get("target_label"),
            payload.get("summary"),
            item.get("path"),
        ]
        text = " ".join(str(value or "") for value in labels if value).strip()
        if not text:
            return 0.0, ["missing_label"]
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", text)
        proper_phrases = re.findall(r"\b[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){1,4}\b", text)
        score = min(len(set(token.lower() for token in tokens)), 8) * 1.5
        score += min(len(proper_phrases), 3) * 4
        if payload.get("source_label") and payload.get("target_label"):
            score += 8
        if payload.get("relation") or item.get("relation"):
            score += 2
        penalties = []
        lowered = text.lower()
        weak_terms = {"unknown", "entity", "evidenceentity", "ordinal reference", "proposed graph", "proposed node", "proposed edge"}
        for term in weak_terms:
            if term in lowered:
                score -= 8
                penalties.append(f"weak_label:{term}")
        if len(tokens) <= 2 and not proper_phrases:
            score -= 5
            penalties.append("too_few_search_terms")
        return max(0.0, score), penalties

    def _continuous_frontier_score(self, item, config):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        source_kind = item.get("source_kind")
        base = float(item.get("priority") or self._continuous_source_priority(source_kind))
        score = base
        reasons = [{"feature": "source_priority", "value": base}]
        if source_kind == "new_graph_edge":
            score += 2
            reasons.append({"feature": "edge_path_bonus", "value": 2})
        status = str(item.get("status") or payload.get("status") or "").replace("-", "_").lower()
        if status == "needs_more_evidence":
            score += 12
            reasons.append({"feature": "needs_more_evidence", "value": 12})
        confidence = item.get("confidence")
        try:
            if confidence is not None and float(confidence) < 0.75:
                score += 4
                reasons.append({"feature": "low_confidence_evidence_gap", "value": 4})
        except (TypeError, ValueError):
            pass
        searchability, penalties = self._continuous_frontier_searchability_score(item)
        score += searchability
        reasons.append({"feature": "searchability", "value": round(searchability, 3)})
        objective_text = str((config or {}).get("_frontier_selection_objective") or "").lower()
        if objective_text:
            affinity_terms = [
                item.get("name"),
                item.get("key"),
                payload.get("label"),
                payload.get("identity_key"),
                payload.get("node_id"),
                payload.get("source_label"),
                payload.get("target_label"),
            ]
            affinity_terms = [
                str(value or "").strip().lower()
                for value in affinity_terms
                if str(value or "").strip()
            ]
            if any(term and len(term) >= 3 and term in objective_text for term in affinity_terms):
                score += 30
                reasons.append({"feature": "objective_affinity", "value": 30})
        ladder_state = (config or {}).get("query_ladder_state") or {}
        key = self._continuous_frontier_key(item)
        state = ladder_state.get(key) if isinstance(ladder_state.get(key), dict) else {}
        if state.get("last_novelty_result") in {"no_new_proposals", "duplicate_only"}:
            score -= 15
            reasons.append({"feature": "recent_low_novelty_penalty", "value": -15})
        for penalty in penalties:
            reasons.append({"feature": penalty, "value": "penalty_applied"})
        return {"score": round(score, 3), "reasons": reasons, "searchability_penalties": penalties}

    def _continuous_frontier_cluster_key(self, item):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        endpoints = [
            str(payload.get("source_label") or "").strip().lower(),
            str(payload.get("target_label") or "").strip().lower(),
        ]
        endpoints = sorted(value for value in endpoints if value)
        relation = str(payload.get("relation") or item.get("relation") or "").strip().lower()
        if endpoints:
            return "|".join([*endpoints, relation])
        return str(item.get("source_kind") or item.get("kind") or "frontier")

    def _continuous_llm_rerank_frontier(self, tenant, objective, candidates, max_frontier, config):
        mode = (config or {}).get("frontier_selector") or "deterministic"
        if mode in {"deterministic", "score", "off", "disabled"}:
            return None, {"planner": "deterministic", "reason": "disabled"}
        api_key = _configured_api_key("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            return None, {"planner": "deterministic", "reason": "missing_api_key"}
        try:
            from google import genai
        except Exception as exc:
            return None, {"planner": "deterministic", "reason": f"google_genai_unavailable: {_safe_error_message(exc)}"}
        prompt_candidates = []
        for item in candidates:
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            score = item.get("_frontier_score") or {}
            prompt_candidates.append(
                {
                    "key": self._continuous_frontier_key(item),
                    "name": self._continuous_frontier_name(item),
                    "source_kind": item.get("source_kind"),
                    "priority": item.get("priority"),
                    "score": score.get("score"),
                    "score_reasons": score.get("reasons"),
                    "cluster": item.get("_frontier_cluster"),
                    "source_label": payload.get("source_label"),
                    "target_label": payload.get("target_label"),
                    "relation": payload.get("relation") or item.get("relation"),
                    "ontology_type": item.get("ontology_type") or payload.get("ontology_type"),
                    "confidence": item.get("confidence"),
                    "path": item.get("path"),
                    "reason": item.get("reason"),
                }
            )
        prompt = {
            "task": "Select the best frontier items for the next enrichment cycle.",
            "objective": objective,
            "max_frontier": int(max_frontier),
            "rules": [
                "Return strict JSON only: {\"selected\":[{\"key\":\"...\",\"reason\":\"...\"}],\"skipped\":[{\"key\":\"...\",\"reason\":\"...\"}]}",
                "Choose items that are likely to produce new, externally searchable evidence.",
                "Prefer clear real-world entities, endpoints, paths, and evidence gaps.",
                "Penalize vague labels, duplicate-looking items, weak searchability, and items likely to repeat prior results.",
                "Maintain diversity across clusters, endpoints, and relation types.",
                "Never invent keys. Select only keys present in candidates.",
                "Hard filters such as cooldown, visited state, tenant, and budget have already been applied.",
            ],
            "candidates": prompt_candidates,
        }
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=(config or {}).get("frontier_selector_model") or DEFAULT_LLM_MODEL,
                contents=json.dumps(prompt, ensure_ascii=False),
            )
            raw_text = (getattr(response, "text", "") or "").strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            parsed = json.loads(raw_text)
        except Exception as exc:
            return None, {"planner": "deterministic", "reason": f"llm_error: {_safe_error_message(exc)}"}
        selected = parsed.get("selected") if isinstance(parsed, dict) else None
        if not isinstance(selected, list):
            return None, {"planner": "deterministic", "reason": "invalid_llm_selection_shape"}
        valid = {self._continuous_frontier_key(item): item for item in candidates}
        ordered = []
        seen = set()
        selection_reasons = {}
        for entry in selected:
            key = entry.get("key") if isinstance(entry, dict) else entry
            key = str(key or "").strip()
            if key and key in valid and key not in seen:
                ordered.append(valid[key])
                seen.add(key)
                if isinstance(entry, dict):
                    selection_reasons[key] = entry.get("reason")
        if not ordered:
            return None, {"planner": "deterministic", "reason": "empty_valid_llm_selection"}
        return ordered, {
            "planner": "llm",
            "selected_keys": [self._continuous_frontier_key(item) for item in ordered],
            "selection_reasons": selection_reasons,
            "raw_selected_count": len(selected),
        }

    def _continuous_frontier_for_cycle(self, tenant, stored_frontier, config, max_frontier):
        now_ts = time.time()
        selected = []
        visited = set((config or {}).get("visited_frontier_keys") or [])
        objective_text = str((config or {}).get("_frontier_selection_objective") or "").strip()
        stored_candidates = []
        stored_seen = set()
        for item in stored_frontier or []:
            if self._continuous_frontier_item_is_storage_node(item):
                continue
            hydrated = self._hydrate_continuous_frontier_item(tenant, item)
            if not hydrated:
                continue
            normalized = self._continuous_normalize_frontier_item(hydrated)
            if self._continuous_frontier_item_is_storage_node(normalized):
                continue
            key = self._continuous_frontier_key(normalized)
            identity = self._continuous_frontier_identity(normalized) or key
            if not key or key in stored_seen or identity in stored_seen or key in visited or identity in visited:
                continue
            stored_seen.add(key)
            stored_seen.add(identity)
            if self._continuous_frontier_available(normalized, config, now_ts):
                stored_candidates.append(normalized)
        dynamic_candidates = [
            item
            for item in (self._continuous_normalize_frontier_item(item) for item in self._continuous_frontier_candidates(tenant, [], config))
            if not self._continuous_frontier_item_is_storage_node(item)
            and self._continuous_frontier_key(item) not in visited
            and self._continuous_frontier_identity(item) not in visited
            and self._continuous_frontier_key(item) not in stored_seen
            and self._continuous_frontier_identity(item) not in stored_seen
        ]
        if stored_frontier:
            candidates = stored_candidates
            if len(stored_candidates) < max_frontier:
                candidates = [*stored_candidates, *dynamic_candidates]
        else:
            candidates = dynamic_candidates
        available_candidates = [item for item in candidates if self._continuous_frontier_available(item, config, now_ts)]
        if not available_candidates:
            available_candidates = [
                item
                for item in candidates
                if item.get("source_kind") in {"instance_coverage", "graph_coverage"}
            ]
        for item in available_candidates:
            item["_frontier_score"] = self._continuous_frontier_score(item, config)
            item["_frontier_cluster"] = self._continuous_frontier_cluster_key(item)
        score_ranked_candidates = sorted(
            available_candidates,
            key=lambda item: (
                -float((item.get("_frontier_score") or {}).get("score") or 0),
                int(item.get("depth") or 0),
                self._continuous_frontier_key(item),
            ),
        )
        available_candidates = score_ranked_candidates
        shortlist_size = max(int(max_frontier), min(int((config or {}).get("frontier_selector_shortlist") or 20), 100))
        shortlist = score_ranked_candidates[:shortlist_size]
        reranked, selector_trace = self._continuous_llm_rerank_frontier(tenant, (config or {}).get("_frontier_selection_objective") or "", shortlist, max_frontier, config or {})
        if reranked:
            selected_keys = {self._continuous_frontier_key(item) for item in reranked}
            available_candidates = reranked + [item for item in available_candidates if self._continuous_frontier_key(item) not in selected_keys]
        config["_frontier_selection_trace"] = {
            **(selector_trace or {"planner": "deterministic", "reason": "not_run"}),
            "candidate_source": (
                "stored_frontier_with_dynamic_backfill"
                if stored_frontier and len(stored_candidates) < max_frontier
                else "stored_frontier"
                if stored_frontier
                else "dynamic_frontier"
            ),
            "stored_candidate_count": len(stored_candidates),
            "dynamic_candidate_count": len(dynamic_candidates),
            "shortlist_count": len(shortlist),
            "candidate_count": len(available_candidates),
            "top_scores": [
                {
                    "key": self._continuous_frontier_key(item),
                    "name": self._continuous_frontier_name(item),
                    "score": (item.get("_frontier_score") or {}).get("score"),
                    "cluster": item.get("_frontier_cluster"),
                }
                for item in shortlist[:10]
            ],
        }
        max_per_cluster = max(1, int((config or {}).get("frontier_max_per_cluster") or 2))
        cluster_counts = {}
        for hydrated in available_candidates:
            key = self._continuous_frontier_key(hydrated)
            if not key:
                continue
            identity = self._continuous_frontier_identity(hydrated) or key
            if identity in {self._continuous_frontier_identity(item) or self._continuous_frontier_key(item) for item in selected}:
                continue
            cluster_key = hydrated.get("_frontier_cluster") or self._continuous_frontier_cluster_key(hydrated)
            if not stored_frontier and cluster_counts.get(cluster_key, 0) >= max_per_cluster and len(available_candidates) - len(selected) > max_frontier:
                continue
            selected_item = {
                "key": key,
                "frontier_identity": identity,
                "name": self._continuous_frontier_name(hydrated),
                "artifact_type": hydrated.get("artifact_type") or hydrated.get("kind") or "frontier_item",
                "source": hydrated.get("source") or "continuous_frontier",
                "source_kind": hydrated.get("source_kind") or "graph_coverage",
                "priority": float(hydrated.get("priority") or self._continuous_source_priority(hydrated.get("source_kind"))),
                "reason": hydrated.get("reason") or self._continuous_source_reason(hydrated.get("source_kind")),
                "depth": int(hydrated.get("depth") or 0),
                "selection_score": (hydrated.get("_frontier_score") or {}).get("score"),
                "selection_reasons": (hydrated.get("_frontier_score") or {}).get("reasons"),
                "selection_cluster": cluster_key,
            }
            for field in (
                "kind",
                "payload",
                "path",
                "relation",
                "ontology_type",
                "evidence_refs",
                "source_run_key",
                "source_url",
                "confidence",
                "related_finding_key",
                "related_question_key",
                "related_run",
            ):
                if hydrated.get(field) is not None:
                    selected_item[field] = hydrated.get(field)
            selected.append(selected_item)
            cluster_counts[cluster_key] = cluster_counts.get(cluster_key, 0) + 1
            if len(selected) >= max_frontier:
                break
        return selected

    def _continuous_next_frontier(self, previous_frontier, result, config, consumed_frontier=None):
        visited = set(config.get("visited_frontier_keys") or [])
        next_frontier = []
        additions = []
        existing = set()
        consumed = {
            value
            for item in (consumed_frontier or [])
            for value in (self._continuous_frontier_key(item), self._continuous_frontier_identity(item))
            if value
        }
        # Preserve the existing queue order and remove frontier items consumed
        # by this cycle. Newly discovered items are appended below, so repeated
        # runs walk the queue instead of repeatedly selecting the same high
        # priority seed.
        for item in previous_frontier or []:
            if self._continuous_frontier_item_is_storage_node(item):
                continue
            key = self._continuous_frontier_key(item)
            identity = self._continuous_frontier_identity(item) or key
            if key and key not in visited and identity not in visited and key not in consumed and identity not in consumed and key not in existing and identity not in existing:
                next_frontier.append(item)
                existing.add(key)
                existing.add(identity)
        for element in result.get("proposed_graph") or []:
            if element.get("element_type") != "edge":
                continue
            key = element.get("element_key")
            if not key or key in existing or key in visited or key in consumed:
                continue
            payload = element.get("payload") or {}
            deep_profile = payload.get("deep_graph_profile") if isinstance(payload.get("deep_graph_profile"), dict) else {}
            item = {
                "kind": f"proposed_{element.get('element_type')}",
                "key": key,
                "name": element.get("name") or key,
                "artifact_type": f"proposed_{element.get('element_type')}",
                "source": "proposed_graph",
                "source_kind": "new_graph_edge",
                "source_run_key": result.get("run", {}).get("run_key"),
                "confidence": element.get("confidence"),
                "depth": int(element.get("iteration") or 1),
                "evidence_refs": element.get("evidence_refs") or [],
                "source_url": element.get("source_url"),
                "ontology_type": payload.get("ontology_type") or payload.get("source_type") or payload.get("relation"),
                "payload": payload,
                "path": deep_profile.get("path_label") or payload.get("path_label"),
                "relation": payload.get("relation"),
            }
            item = self._continuous_normalize_frontier_item(item, source_kind=item["source_kind"])
            identity = self._continuous_frontier_identity(item) or key
            if key in existing or identity in existing or identity in visited or identity in consumed:
                continue
            item["frontier_identity"] = identity
            next_frontier.append(item)
            additions.append(item)
            existing.add(key)
            existing.add(identity)
        return next_frontier[:100], additions

    def _continuous_mark_frontier_visited(self, config, frontier_items):
        visited = list(config.get("visited_frontier_keys") or [])
        for item in frontier_items or []:
            for value in (self._continuous_frontier_key(item), self._continuous_frontier_identity(item)):
                if value and value not in visited:
                    visited.append(value)
        config["visited_frontier_keys"] = visited[-500:]
        frontier_state = self._continuous_frontier_state(config)
        now_iso = datetime.utcnow().isoformat()
        for item in frontier_items or []:
            key = self._continuous_frontier_key(item)
            if not key:
                continue
            frontier_state["last_enriched_at"][key] = now_iso
            frontier_state["selected_count"][key] = int(frontier_state["selected_count"].get(key) or 0) + 1
            identity = self._continuous_frontier_identity(item)
            if identity:
                frontier_state["last_enriched_at"][identity] = now_iso
        frontier_state["coverage_cursor"] = int(frontier_state.get("coverage_cursor") or 0) + len(frontier_items or [])
        config["frontier_state"] = frontier_state
        return config

    def _continuous_append_events(self, config, events):
        merged = list(config.get("latest_events") or [])
        merged.extend(events)
        config["latest_events"] = merged[-50:]
        return config

    def _continuous_session_runtime_state(self, status, config, frontier, now_ts=None):
        config = config or {}
        frontier = frontier or []
        due, due_reason = self._continuous_session_auto_due(status, config, now_ts=now_ts)
        budget = self._continuous_budget(config)
        max_cycles = budget.get("max_cycles")
        backoff = self._continuous_backoff_state(config)
        active_backoff = self._continuous_backoff_active(config, now_ts=now_ts)
        frontier_state = self._continuous_frontier_state(config)
        source_kind_counts = {}
        for item in frontier:
            if not isinstance(item, dict):
                continue
            source_kind = item.get("source_kind") or item.get("source") or item.get("kind") or "frontier"
            source_kind_counts[source_kind] = source_kind_counts.get(source_kind, 0) + 1
        return {
            "persistent": True,
            "queue_mode": "fifo_frontier_queue",
            "frontier_queue_count": len(frontier),
            "next_frontier_keys": [self._continuous_frontier_key(item) for item in frontier[:10]],
            "frontier_queue": {
                "total_count": len(frontier),
                "source_kind_counts": source_kind_counts,
                "preview": [
                    {
                        "key": self._continuous_frontier_key(item),
                        "name": item.get("name") or item.get("target_key") or item.get("key"),
                        "source_kind": item.get("source_kind") or item.get("source") or item.get("kind"),
                        "priority": item.get("priority"),
                        "depth": item.get("depth"),
                        "reason": item.get("reason"),
                    }
                    for item in frontier[:10]
                    if isinstance(item, dict)
                ],
            },
            "cadence": config.get("cadence") or "manual",
            "custom_interval_minutes": config.get("custom_interval_minutes"),
            "next_run_at": config.get("next_run_at"),
            "auto_due": due,
            "auto_due_reason": due_reason,
            "budget": {
                **budget,
                "completed_cycles": int(config.get("completed_cycles") or 0),
                "remaining_cycles": None if max_cycles is None else max(0, int(max_cycles) - int(config.get("completed_cycles") or 0)),
            },
            "backoff": {
                "active": active_backoff is not None,
                "backoff_until": backoff.get("backoff_until"),
                "remaining_seconds": (active_backoff or {}).get("remaining_seconds"),
                "failure_count": backoff.get("failure_count"),
                "last_error": backoff.get("last_error"),
            },
            "stop_reason": config.get("stop_reason"),
            "last_started_at": config.get("last_started_at"),
            "last_finished_at": config.get("last_finished_at"),
            "frontier_state": {
                "visited_count": len(config.get("visited_frontier_keys") or []),
                "cooldown_minutes": config.get("frontier_cooldown_minutes"),
                "coverage_cursor": frontier_state.get("coverage_cursor"),
                "tracked_frontier_count": len((frontier_state.get("last_enriched_at") or {})),
            },
        }

    def configure_continuous_enrichment_session(self, tenant, session_key, body=None):
        body = body or {}
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        config = self._continuous_update_config(_load_json(row["config_json"], {}), body)
        config["next_run_at"] = self._continuous_next_run_at(config)
        objective = row["objective"]
        if "objective" in body:
            objective = str(body.get("objective") or "").strip()
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET objective = :objective, config_json = :config_json, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "objective": objective,
                    "config_json": _json_dump(config),
                },
            )
        return self.continuous_enrichment_session(tenant, session_key)

    def _continuous_session_row(self, tenant, session_key):
        self._ensure_continuous_enrichment_schema(tenant)
        self._default_continuous_session(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            return conn.execute(
                text(
                    """
                    SELECT id, project_id, session_key, objective, status, config_json, frontier_json,
                           last_run_key, cycle_count, created_at, updated_at
                    FROM aletheia_continuous_enrichment_sessions
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key},
            ).mappings().first()

    def _continuous_session_to_dict(self, tenant, row):
        if row is None:
            return None
        latest = None
        if row["last_run_key"]:
            run_summary = self._continuous_iterative_run_summary(tenant, row["last_run_key"])
            latest_data = self.proposed_graph_elements(tenant, run_key=row["last_run_key"], limit=120, status_filter="all")
            latest = {
                "run": run_summary or (latest_data.get("runs") or [None])[0],
                "element_count": len(latest_data.get("elements") or []),
                "finding_count": len([e for e in latest_data.get("elements") or [] if e.get("element_type") == "finding"]),
                "findings": [
                    {
                        "name": e.get("name"),
                        "element_key": e.get("element_key"),
                        "status": e.get("status"),
                        "confidence": e.get("confidence"),
                        "path": ((e.get("payload") or {}).get("deep_graph_profile") or {}).get("path_label"),
                        "source_url": e.get("source_url"),
                    }
                    for e in latest_data.get("elements") or []
                    if e.get("element_type") == "finding"
                ],
            }
        config = _load_json(row["config_json"], {})
        config.setdefault("node_similarity_dedup_threshold", 0.6)
        config.setdefault("auto_review_similar_proposals", False)
        config.setdefault("auto_review_llm_verifier", True)
        config.setdefault("auto_review_model", DEFAULT_LLM_MODEL)
        config.setdefault("auto_reject_similarity_threshold", 0.92)
        config.setdefault("auto_approve_low_duplicate_proposals", False)
        config.setdefault("auto_approve_min_confidence", 0.8)
        config.setdefault("auto_approve_max_duplicate_score", 0.5)
        config.setdefault("auto_review_reviewer", "Continuous Enrichment Agent")
        frontier = _load_json(row["frontier_json"], [])
        return {
            "session_key": row["session_key"],
            "tenant_id": row["project_id"],
            "objective": row["objective"],
            "status": row["status"],
            "config": config,
            "frontier": frontier,
            "latest_events": config.get("latest_events") or [],
            "last_run_key": row["last_run_key"],
            "cycle_count": row["cycle_count"],
            "created_at": _jsonable(row["created_at"]),
            "updated_at": _jsonable(row["updated_at"]),
            "runtime_state": self._continuous_session_runtime_state(
                row["status"],
                {**config, "completed_cycles": int(row["cycle_count"] or 0)},
                frontier,
            ),
            "latest": latest,
            "write_boundary": {
                "ontology_candidates_require_review": True,
                "graph_fact_target": "proposed_graph_space",
                "candidate_findings_only": True,
                "canonical_write": False,
                "formal_graph_write": False,
            },
        }

    def _continuous_iterative_run_summary(self, tenant, run_key):
        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT run_key, status, objective, frontier_json, expansion_trace_json,
                               safety_profile_json, budget_json, skipped_sources_json,
                               proposed_count, pruned_count, finding_count, error,
                               started_at, finished_at
                        FROM aletheia_iterative_graph_enrichment_runs
                        WHERE project_id = :tenant_id AND run_key = :run_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "run_key": run_key},
                ).mappings().first()
        except Exception:
            return None
        if row is None:
            return None
        expansion_trace = _load_json(row["expansion_trace_json"], [])
        return {
            "run_key": row["run_key"],
            "objective": row["objective"],
            "status": row["status"],
            "proposed_count": row["proposed_count"],
            "finding_count": row["finding_count"],
            "pruned_count": row["pruned_count"],
            "frontier": _load_json(row["frontier_json"], []),
            "safety_profile": _load_json(row["safety_profile_json"], {}),
            "budget": _load_json(row["budget_json"], {}),
            "skipped_sources": _load_json(row["skipped_sources_json"], []),
            "extraction_blockers": self._continuous_no_proposal_summary({"expansion_trace": expansion_trace}),
            "error": row["error"],
            "started_at": _jsonable(row["started_at"]),
            "finished_at": _jsonable(row["finished_at"]),
        }

    def _continuous_no_proposal_summary(self, run_payload):
        trace = (run_payload or {}).get("expansion_trace") or []
        reason_counts = {}
        engine_status_counts = {}
        rejected_reason_counts = {}
        frontier_keys = []
        source_urls = []
        for step in trace:
            frontier = step.get("frontier") if isinstance(step, dict) else {}
            frontier_key = (frontier or {}).get("key")
            if frontier_key and frontier_key not in frontier_keys:
                frontier_keys.append(frontier_key)
            extraction = step.get("last_extraction_profile") if isinstance(step, dict) else {}
            if isinstance(extraction, dict):
                status = extraction.get("extraction_engine_status")
                if status:
                    engine_status_counts[status] = engine_status_counts.get(status, 0) + 1
                source = extraction.get("source") if isinstance(extraction.get("source"), dict) else {}
                source_url = source.get("url")
                if source_url and source_url not in source_urls:
                    source_urls.append(source_url)
                for item in extraction.get("rejected_or_ambiguous_candidates") or []:
                    reason = item.get("reason") if isinstance(item, dict) else None
                    if reason:
                        rejected_reason_counts[reason] = rejected_reason_counts.get(reason, 0) + 1
            for item in step.get("pruned") or []:
                reason = item.get("reason") if isinstance(item, dict) else None
                if reason:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1
        return {
            "pruned_reason_counts": reason_counts,
            "extraction_engine_status_counts": engine_status_counts,
            "rejected_candidate_reason_counts": rejected_reason_counts,
            "frontier_keys": frontier_keys[:10],
            "source_urls": source_urls[:10],
        }

    def continuous_enrichment_sessions(self, tenant):
        self._ensure_continuous_enrichment_schema(tenant)
        self._default_continuous_session(tenant)
        with self.metadata_engine_for(tenant).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, project_id, session_key, objective, status, config_json, frontier_json,
                           last_run_key, cycle_count, created_at, updated_at
                    FROM aletheia_continuous_enrichment_sessions
                    WHERE project_id = :tenant_id
                    ORDER BY updated_at DESC, session_key ASC
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().all()
        rows = [self._recover_stale_continuous_session_row(tenant, row) for row in rows]
        return {"tenant": tenant.public_dict(), "sessions": [self._continuous_session_to_dict(tenant, row) for row in rows]}

    def continuous_enrichment_session(self, tenant, session_key):
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        row = self._recover_stale_continuous_session_row(tenant, row)
        return {"tenant": tenant.public_dict(), "session": self._continuous_session_to_dict(tenant, row)}

    def run_due_continuous_enrichment_sessions(self, limit=10):
        if not self._continuous_scheduler_lock.acquire(blocking=False):
            return {"status": "busy", "ran": [], "skipped": []}
        ran = []
        skipped = []
        try:
            tenants = [
                tenant
                for tenant in getattr(self.tenant_registry, "tenants", {}).values()
                if getattr(tenant, "status", "active") == "active"
            ]
            for tenant in tenants:
                for session in self.continuous_enrichment_sessions(tenant).get("sessions", []):
                    config = session.get("config") or {}
                    due, reason = self._continuous_session_auto_due(session.get("status"), config)
                    if not due:
                        skipped.append(
                            {
                                "tenant_id": tenant.tenant_id,
                                "session_key": session.get("session_key"),
                                "reason": reason,
                            }
                        )
                        continue
                    try:
                        result = self.run_continuous_enrichment_cycle(
                            tenant,
                            session["session_key"],
                            {"scheduler_tick": True},
                        )
                        ran.append(
                            {
                                "tenant_id": tenant.tenant_id,
                                "session_key": session.get("session_key"),
                                "status": (result.get("cycle") or {}).get("status"),
                                "run_key": (result.get("cycle") or {}).get("run_key"),
                            }
                        )
                    except Exception as exc:
                        ran.append(
                            {
                                "tenant_id": tenant.tenant_id,
                                "session_key": session.get("session_key"),
                                "status": "failed",
                                "error": _safe_error_message(exc),
                            }
                        )
                    if limit and len(ran) >= int(limit):
                        return {"status": "ok", "ran": ran, "skipped": skipped}
            return {"status": "ok", "ran": ran, "skipped": skipped}
        finally:
            self._continuous_scheduler_lock.release()

    def start_continuous_enrichment_scheduler(self, interval_seconds=60):
        if self._continuous_scheduler_thread and self._continuous_scheduler_thread.is_alive():
            return self._continuous_scheduler_thread
        interval_seconds = max(1, int(interval_seconds or 60))
        stop_event = threading.Event()
        self._continuous_scheduler_stop = stop_event

        def _loop():
            while not stop_event.is_set():
                try:
                    self.run_due_continuous_enrichment_sessions()
                except Exception as exc:  # pragma: no cover - background observability only
                    print(f"continuous enrichment scheduler tick failed: {_safe_error_message(exc)}", file=sys.stderr, flush=True)
                stop_event.wait(interval_seconds)

        thread = threading.Thread(target=_loop, name="continuous-enrichment-scheduler", daemon=True)
        thread.start()
        self._continuous_scheduler_thread = thread
        return thread

    def stop_continuous_enrichment_scheduler(self):
        if self._continuous_scheduler_stop is not None:
            self._continuous_scheduler_stop.set()

    def update_continuous_enrichment_session_status(self, tenant, session_key, status):
        if status not in {"idle", "paused", "stopped"}:
            raise ValueError("Unsupported continuous enrichment session status")
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        config = _load_json(row["config_json"], {})
        now_iso = datetime.utcnow().isoformat()
        if status == "idle":
            config["stop_reason"] = None
            backoff = self._continuous_backoff_active(config)
            if backoff:
                config["next_run_at"] = backoff["backoff_until"]
            elif self._continuous_cadence_seconds(config):
                config["next_run_at"] = now_iso
            else:
                config["next_run_at"] = None
            event_type = "session_resumed"
        elif status == "paused":
            config["next_run_at"] = None
            event_type = "session_paused"
        else:
            config["next_run_at"] = None
            config["stop_reason"] = "session stopped by operator"
            event_type = "session_stopped"
        self._continuous_append_events(
            config,
            [
                {
                    "type": event_type,
                    "status": status,
                    "created_at": now_iso,
                    "persistent_session": True,
                    "frontier_queue_count": len(_load_json(row["frontier_json"], [])),
                }
            ],
        )
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = :status,
                        config_json = :config_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "status": status,
                    "config_json": _json_dump(config),
                },
            )
        return self.continuous_enrichment_session(tenant, session_key)

    def run_continuous_enrichment_cycle(self, tenant, session_key, body=None):
        body = body or {}
        row = self._continuous_session_row(tenant, session_key)
        if row is None:
            return None
        if row["status"] == "stopped":
            raise ValueError("Continuous enrichment session is stopped")
        if row["status"] == "paused" and not body.get("force"):
            raise ValueError("Continuous enrichment session is paused")
        config = self._continuous_update_config(_load_json(row["config_json"], {}), body)
        events = []
        active_backoff = self._continuous_backoff_active(config)
        if active_backoff and not body.get("force"):
            events.append(
                {
                    "type": "backoff_active",
                    "backoff_until": active_backoff["backoff_until"],
                    "remaining_seconds": active_backoff["remaining_seconds"],
                    "reason": self._continuous_backoff_state(config).get("last_error"),
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            self._continuous_append_events(config, events)
            with self.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = 'idle',
                            config_json = :config_json,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "session_key": session_key, "config_json": _json_dump(config)},
                )
            raise ValueError(f"Continuous enrichment session is in backoff until {active_backoff['backoff_until']}")
        budget = self._continuous_budget(config)
        if budget["max_cycles"] is not None and int(row["cycle_count"] or 0) >= budget["max_cycles"]:
            event = {
                "type": "budget_exhausted",
                "reason": "max_cycles reached",
                "max_cycles": budget["max_cycles"],
                "cycle_count": int(row["cycle_count"] or 0),
                "created_at": datetime.utcnow().isoformat(),
            }
            events.append(event)
            config["stop_reason"] = event["reason"]
            config["next_run_at"] = None
            self._continuous_append_events(config, events)
            next_status = "paused" if (config.get("stop_policy") or {}).get("pause_on_budget_exhausted", True) else "idle"
            with self.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = :status,
                            config_json = :config_json,
                            frontier_json = :frontier_json,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {
                        "tenant_id": tenant.tenant_id,
                        "session_key": session_key,
                        "status": next_status,
                        "config_json": _json_dump(config),
                        "frontier_json": row["frontier_json"] or "[]",
                    },
                )
            return {
                "tenant": tenant.public_dict(),
                "session": self.continuous_enrichment_session(tenant, session_key)["session"],
                "cycle": {"status": "budget_exhausted", "events": events, "budget": budget},
                "write_boundary": {
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                    "findings": "candidate_only",
                    "autopilot_auto_approve": False,
                },
            }
        max_frontier = min(
            int(config.get("max_frontier") or config.get("rate_limit_per_cycle") or 4),
            int(budget["max_frontier_per_cycle"] or 0),
        )
        max_results_per_query = min(
            int(config.get("max_results_per_query") or 4),
            int(budget["max_results_per_query"] or 0),
        )
        max_iterations = min(
            int(config.get("max_iterations") or 1),
            int(budget["max_iterations_per_cycle"] or 0),
        )
        if max_frontier <= 0 or max_results_per_query <= 0 or max_iterations <= 0:
            events.append(
                {
                    "type": "budget_exhausted",
                    "reason": "per-cycle budget is zero",
                    "budget": budget,
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            config["stop_reason"] = "per-cycle budget is zero"
            config["next_run_at"] = None
            self._continuous_append_events(config, events)
            next_status = "paused" if (config.get("stop_policy") or {}).get("pause_on_budget_exhausted", True) else "idle"
            with self.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = :status,
                            config_json = :config_json,
                            frontier_json = :frontier_json,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {
                        "tenant_id": tenant.tenant_id,
                        "session_key": session_key,
                        "status": next_status,
                        "config_json": _json_dump(config),
                    },
                )
            return {
                "tenant": tenant.public_dict(),
                "session": self.continuous_enrichment_session(tenant, session_key)["session"],
                "cycle": {"status": "budget_exhausted", "events": events, "budget": budget},
                "write_boundary": {
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                    "findings": "candidate_only",
                    "autopilot_auto_approve": False,
                },
            }
        retrieval_objective, execution_goal = self._continuous_retrieval_objective(row["objective"], config, body)
        config["_frontier_selection_objective"] = execution_goal or retrieval_objective
        config["last_research_topic"] = retrieval_objective
        config["last_execution_goal"] = execution_goal
        stored_frontier = _load_json(row["frontier_json"], [])
        if self._continuous_research_mode(config) == "deep_research":
            stored_frontier = []
        frontier_items = self._continuous_frontier_for_cycle(tenant, stored_frontier, config, max_frontier)
        events.append(
            {
                "type": "budget_applied",
                "budget": {
                    **budget,
                    "effective_max_frontier": max_frontier,
                    "effective_max_results_per_query": max_results_per_query,
                    "effective_max_iterations": max_iterations,
                },
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        events.append(
            {
                "type": "frontier_selected",
                "selected_count": len(frontier_items),
                "selected_keys": [self._continuous_frontier_key(item) for item in frontier_items],
                "selection_trace": config.pop("_frontier_selection_trace", None),
                "research_topic": retrieval_objective,
                "execution_goal": execution_goal,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        config.pop("_frontier_selection_objective", None)
        if not frontier_items:
            event = {
                "type": "no_frontier_stop",
                "reason": "no available frontier after cooldown and coverage fallback",
                "created_at": datetime.utcnow().isoformat(),
            }
            events.append(event)
            config["stop_reason"] = event["reason"]
            config["next_run_at"] = None
            self._continuous_append_events(config, events)
            next_status = "paused" if (config.get("stop_policy") or {}).get("pause_on_no_frontier", True) else "idle"
            with self.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = :status,
                            config_json = :config_json,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {
                        "tenant_id": tenant.tenant_id,
                        "session_key": session_key,
                        "status": next_status,
                        "config_json": _json_dump(config),
                    },
                )
            return {
                "tenant": tenant.public_dict(),
                "session": self.continuous_enrichment_session(tenant, session_key)["session"],
                "cycle": {"status": "stopped", "stop_reason": event["reason"], "events": events, "frontier_used": []},
                "write_boundary": {
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                    "findings": "candidate_only",
                    "autopilot_auto_approve": False,
                },
            }
        config["research_provider"] = "gpt_researcher"
        events.append(
            {
                "type": "research_provider_selected",
                "provider": "gpt_researcher",
                "reason": "GPT Researcher is the only enrichment retrieval provider",
                "research_topic": retrieval_objective,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        config["last_started_at"] = datetime.utcnow().isoformat()
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                        """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = 'running',
                        config_json = :config_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "session_key": session_key, "config_json": _json_dump(config)},
            )
        try:
            result = IterativeGraphEnrichmentAgent(
                tenant.metadata_db_url,
                tenant=tenant.tenant_id,
                max_iterations=max_iterations,
                max_frontier=max_frontier,
                max_results_per_query=max_results_per_query,
                node_similarity_dedup_threshold=float(config.get("node_similarity_dedup_threshold", 0.6)),
                research_provider="gpt_researcher",
                gpt_researcher_report_type=str(config.get("gpt_researcher_report_type") or "research_report"),
                gpt_researcher_report_source=config.get("gpt_researcher_report_source") or None,
                gpt_researcher_max_report_chars=int(config.get("gpt_researcher_max_report_chars") or 24000),
            ).run(retrieval_objective, artifact_keys=body.get("artifact_keys") or None, frontier_items=frontier_items or None)
            config = self._continuous_clear_backoff(config)
        except Exception as exc:
            config, backoff = self._continuous_schedule_backoff(config, exc)
            events.append(
                {
                    "type": "cycle_failed",
                    "reason": _safe_error_message(exc),
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            events.append(
                {
                    "type": "backoff_scheduled",
                    "failure_count": backoff.get("failure_count"),
                    "delay_seconds": backoff.get("delay_seconds"),
                    "backoff_until": backoff.get("backoff_until"),
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            config["next_run_at"] = backoff.get("backoff_until")
            config["last_finished_at"] = datetime.utcnow().isoformat()
            self._continuous_append_events(config, events)
            with self.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = 'idle',
                            config_json = :config_json,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "session_key": session_key, "config_json": _json_dump(config)},
                )
            raise
        run_key = result["run"]["run_key"]
        proposed_graph = result.get("proposed_graph") or []
        auto_review_result = self._continuous_auto_review_similar_proposals(tenant, proposed_graph, config)
        if auto_review_result.get("enabled"):
            events.append(
                {
                    "type": "auto_review_similar_proposals",
                    "run_key": run_key,
                    "reviewed_count": len(auto_review_result.get("reviewed") or []),
                    "skipped_count": len(auto_review_result.get("skipped") or []),
                    "reviewed": (auto_review_result.get("reviewed") or [])[:20],
                    "skipped": (auto_review_result.get("skipped") or [])[:20],
                    "threshold": float(config.get("auto_reject_similarity_threshold") or 0.92),
                    "created_at": datetime.utcnow().isoformat(),
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_review_gate",
                }
            )
        auto_approve_result = self._continuous_auto_approve_low_duplicate_proposals(tenant, proposed_graph, config)
        if auto_approve_result.get("enabled"):
            events.append(
                {
                    "type": "auto_approve_low_duplicate_proposals",
                    "run_key": run_key,
                    "reviewed_count": len(auto_approve_result.get("reviewed") or []),
                    "skipped_count": len(auto_approve_result.get("skipped") or []),
                    "reviewed": (auto_approve_result.get("reviewed") or [])[:20],
                    "skipped": (auto_approve_result.get("skipped") or [])[:20],
                    "min_confidence": float(config.get("auto_approve_min_confidence") or 0.8),
                    "max_duplicate_score": float(config.get("auto_approve_max_duplicate_score") or 0.5),
                    "created_at": datetime.utcnow().isoformat(),
                    "canonical_write": False,
                    "graph_space_write": True,
                    "formal_graph_write": False,
                    "target": "proposed_graph_review_gate",
                }
            )
        next_frontier, frontier_additions = self._continuous_next_frontier(stored_frontier, result, config, consumed_frontier=frontier_items)
        config = self._continuous_mark_frontier_visited(config, frontier_items)
        frontier_state = self._continuous_frontier_state(config)
        graph_changed = bool(proposed_graph)
        ladder_state = config.setdefault("query_ladder_state", {})
        for item in frontier_items or []:
            frontier_key = self._continuous_frontier_key(item)
            state = ladder_state.setdefault(frontier_key, {})
            last_index = int(state.get("last_attempted_plan_index") or state.get("next_plan_index") or 0)
            if graph_changed:
                state["last_novelty_result"] = "new_reviewable_candidate"
                state["next_plan_index"] = max(0, min(last_index, 4))
            else:
                state["last_novelty_result"] = "no_new_proposals"
                state["next_plan_index"] = min(last_index + 1, 4)
        if graph_changed:
            config["last_graph_changed_at"] = datetime.utcnow().isoformat()
            events.append(
                {
                    "type": "graph_changed",
                    "run_key": run_key,
                    "proposed_count": result["run"].get("proposed_count"),
                    "returned_element_count": len(proposed_graph),
                    "new_frontier_count": len(frontier_additions),
                    "created_at": config["last_graph_changed_at"],
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                }
            )
            events.append(
                {
                    "type": "new_evidence_available",
                    "run_key": run_key,
                    "frontier_keys": [item.get("key") for item in frontier_additions[:10]],
                    "created_at": datetime.utcnow().isoformat(),
                    "review_boundary": "proposed_graph_review_gate",
                }
            )
        else:
            events.append(
                {
                    "type": "no_new_proposals",
                    "run_key": run_key,
                    "reason": "all trusted sources produced no new reviewable graph proposals",
                    "extraction_blockers": self._continuous_no_proposal_summary(result.get("run") or {}),
                    "created_at": datetime.utcnow().isoformat(),
                    "canonical_write": False,
                    "formal_graph_write": False,
                    "target": "proposed_graph_space",
                }
            )
        events.append(
            {
                "type": "cycle_completed",
                "run_key": run_key,
                "status": result["run"]["status"],
                "proposed_count": result["run"].get("proposed_count"),
                "extraction_blockers": self._continuous_no_proposal_summary(result.get("run") or {}) if not graph_changed else {},
                "frontier_used_count": len(frontier_items),
                "retrieval_provider": "gpt_researcher",
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        autopilot_result = None
        if graph_changed and body.get("trigger_autopilot", True) and self.reasoning_repository is not None:
            autopilot_payload = {
                "session_key": f"autopilot:{tenant.tenant_id}:continuous-enrichment:{_slug(run_key)}",
                "objective": (
                    body.get("autopilot_objective")
                    or f"Re-run deep reasoning after new graph evidence from {run_key}; generate candidate findings only."
                ),
                "budget": body.get("autopilot_budget") or {
                    "max_hypotheses": 6,
                    "max_reasoning_tasks": 4,
                    "max_tool_calls": 20,
                    "max_runtime_seconds": 120,
                },
                "created_by": "Continuous Enrichment Agent",
                "scope": {
                    "tenant": tenant.tenant_id,
                    "source_run_key": run_key,
                    "event": "new_evidence_available",
                    "candidate_findings_only": True,
                    "canonical_writes": "disabled",
                    "formal_graph_writes": "disabled",
                },
            }
            try:
                autopilot_result = self.reasoning_repository.create_autopilot_session(tenant, autopilot_payload)
                config["last_autopilot_session_key"] = (autopilot_result.get("session") or {}).get("session_key") or autopilot_payload["session_key"]
                events.append(
                    {
                        "type": "autopilot_triggered",
                        "source_event": "new_evidence_available",
                        "source_run_key": run_key,
                        "autopilot_session_key": config["last_autopilot_session_key"],
                        "candidate_findings": len(autopilot_result.get("candidate_findings") or []),
                        "auto_approve_findings": False,
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
            except Exception as exc:
                events.append(
                    {
                        "type": "autopilot_trigger_failed",
                        "source_event": "new_evidence_available",
                        "source_run_key": run_key,
                        "reason": _safe_error_message(exc),
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
        config["last_finished_at"] = datetime.utcnow().isoformat()
        config["next_run_at"] = self._continuous_next_run_at(config)
        self._continuous_append_events(config, events)
        with self.metadata_engine_for(tenant).begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_continuous_enrichment_sessions
                    SET status = 'idle',
                        last_run_key = :run_key,
                        cycle_count = cycle_count + 1,
                        config_json = :config_json,
                        frontier_json = :frontier_json,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = :tenant_id AND session_key = :session_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "session_key": session_key,
                    "run_key": run_key,
                    "config_json": _json_dump(config),
                    "frontier_json": _json_dump(next_frontier),
                },
            )
        return {
            "tenant": tenant.public_dict(),
            "session": self.continuous_enrichment_session(tenant, session_key)["session"],
            "cycle": {
                "run_key": run_key,
                "status": result["run"]["status"],
                "proposed_count": result["run"]["proposed_count"],
                "returned_element_count": len(result.get("proposed_graph") or []),
                "finding_count": len([e for e in result.get("proposed_graph") or [] if e.get("element_type") == "finding"]),
                "skipped_sources": result["run"].get("skipped_sources") or [],
                "frontier_used": frontier_items,
                "next_frontier_count": len(next_frontier),
                "new_frontier": frontier_additions,
                "budget": {
                    **budget,
                    "effective_max_frontier": max_frontier,
                    "effective_max_results_per_query": max_results_per_query,
                    "effective_max_iterations": max_iterations,
                },
                "retrieval": {"provider": "gpt_researcher"},
                "events": events,
                "autopilot_session_key": config.get("last_autopilot_session_key"),
                "frontier_priority_summary": {
                    "selected": [
                        {
                            "key": item.get("key"),
                            "name": item.get("name"),
                            "source_kind": item.get("source_kind"),
                            "priority": item.get("priority"),
                            "reason": item.get("reason"),
                            "related_finding_key": item.get("related_finding_key"),
                            "related_question_key": item.get("related_question_key"),
                        }
                        for item in frontier_items
                    ],
                    "coverage_cursor": frontier_state.get("coverage_cursor"),
                    "cooldown_minutes": config.get("frontier_cooldown_minutes"),
                },
                "findings": [
                    {
                        "name": e.get("name"),
                        "confidence": e.get("confidence"),
                        "source_url": e.get("source_url"),
                        "path": ((e.get("payload") or {}).get("deep_graph_profile") or {}).get("path_label"),
                    }
                    for e in result.get("proposed_graph") or []
                    if e.get("element_type") == "finding"
                ],
            },
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "target": "proposed_graph_space",
                "findings": "candidate_only",
                "autopilot_auto_approve": False,
            },
        }

    def agent_runs_console(self, tenant, limit=20):
        limit = max(1, min(int(limit or 20), 100))

        sessions = self.continuous_enrichment_sessions(tenant).get("sessions", [])
        runs = []
        degraded = []

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, run_key, source_agent, status, objective, frontier_json,
                               expansion_trace_json, safety_profile_json, budget_json,
                               skipped_sources_json, proposed_count, pruned_count,
                               finding_count, error, started_at, finished_at
                        FROM aletheia_iterative_graph_enrichment_runs
                        WHERE project_id = :tenant_id
                        ORDER BY started_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    elements = conn.execute(
                        text(
                            """
                            SELECT element_key, element_type, name, payload_json,
                                   evidence_refs_json, source_url, confidence,
                                   status, iteration, created_at
                            FROM aletheia_proposed_graph_elements
                            WHERE project_id = :tenant_id AND run_id = :run_id
                            ORDER BY iteration ASC, element_type ASC, name ASC
                            LIMIT 200
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "run_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "iterative_graph_enrichment",
                            "run_key": row["run_key"],
                            "agent": row["source_agent"],
                            "status": row["status"],
                            "objective": row["objective"],
                            "frontier": _load_json(row["frontier_json"], []),
                            "trace": _load_json(row["expansion_trace_json"], []),
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": _load_json(row["skipped_sources_json"], []),
                            "counts": {
                                "proposed": row["proposed_count"],
                                "pruned": row["pruned_count"],
                                "findings": row["finding_count"],
                                "returned": len(elements),
                            },
                            "elements": [
                                {
                                    "element_key": e["element_key"],
                                    "element_type": e["element_type"],
                                    "name": e["name"],
                                    "payload": _load_json(e["payload_json"], {}),
                                    "evidence_refs": _load_json(e["evidence_refs_json"], []),
                                    "source_url": e["source_url"],
                                    "confidence": e["confidence"],
                                    "status": e["status"],
                                    "iteration": e["iteration"],
                                    "created_at": _jsonable(e["created_at"]),
                                }
                                for e in elements
                            ],
                            "started_at": _jsonable(row["started_at"]),
                            "finished_at": _jsonable(row["finished_at"]),
                            "error": row["error"],
                            "write_boundary": {
                                "target": "proposed_graph_space",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "findings": "candidate_only",
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "iterative_graph_enrichment", "reason": _safe_error_message(exc)})

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, run_key, source_agent, search_provider, status,
                               target_artifacts_json, safety_profile_json, budget_json,
                               skipped_sources_json, query_count, result_count,
                               proposal_count, error, started_at, finished_at
                        FROM aletheia_web_enrichment_runs
                        WHERE project_id = :tenant_id
                        ORDER BY started_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    proposals = conn.execute(
                        text(
                            """
                            SELECT proposal_key, target_artifact_key, source_url,
                                   source_title, summary, raw_payload_json,
                                   confidence, status, created_at
                            FROM aletheia_web_enrichment_proposals
                            WHERE project_id = :tenant_id AND run_id = :run_id
                            ORDER BY created_at DESC, id DESC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "run_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "web_enrichment_crawl",
                            "run_key": row["run_key"],
                            "agent": row["source_agent"],
                            "status": row["status"],
                            "objective": f"Enrich ontology artifacts via {row['search_provider']} search/crawl",
                            "frontier": [
                                {"kind": "target_artifact", "target_key": key}
                                for key in _load_json(row["target_artifacts_json"], [])
                            ],
                            "trace": [
                                {
                                    "query": _web_enrichment_query(
                                        _load_json(p["raw_payload_json"], {}),
                                        p["target_artifact_key"],
                                    ),
                                    "result_count": 1,
                                    "source_url": p["source_url"],
                                    "target": p["target_artifact_key"],
                                    "extracted_candidates": [p["proposal_key"]],
                                }
                                for p in proposals
                            ],
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": _load_json(row["skipped_sources_json"], []),
                            "counts": {
                                "queries": row["query_count"],
                                "results": row["result_count"],
                                "proposals": row["proposal_count"],
                                "returned": len(proposals),
                            },
                            "elements": [
                                {
                                    "element_key": p["proposal_key"],
                                    "element_type": "ontology_enrichment_proposal",
                                    "name": p["source_title"] or p["target_artifact_key"],
                                    "target_artifact_key": p["target_artifact_key"],
                                    "payload": _load_json(p["raw_payload_json"], {}),
                                    "evidence_refs": [p["source_url"]] if p["source_url"] else [],
                                    "source_url": p["source_url"],
                                    "confidence": p["confidence"],
                                    "status": p["status"],
                                    "summary": p["summary"],
                                    "created_at": _jsonable(p["created_at"]),
                                }
                                for p in proposals
                            ],
                            "started_at": _jsonable(row["started_at"]),
                            "finished_at": _jsonable(row["finished_at"]),
                            "error": row["error"],
                            "write_boundary": {
                                "target": "ontology_review_queue",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "ontology_review_required": True,
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "web_enrichment_crawl", "reason": _safe_error_message(exc)})

        try:
            with self.metadata_engine_for(tenant).connect() as conn:
                rows = conn.execute(
                    text(
                        """
                        SELECT id, session_key, objective, scope_json, budget_json,
                               safety_profile_json, status, created_by,
                               created_at, updated_at
                        FROM aletheia_autopilot_sessions
                        WHERE project_id = :tenant_id
                        ORDER BY updated_at DESC, id DESC
                        LIMIT :limit
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "limit": limit},
                ).mappings().all()
                for row in rows:
                    hypotheses = conn.execute(
                        text(
                            """
                            SELECT hypothesis_key, title, rationale, status, priority,
                                   evidence_plan_json, reasoning_task_keys_json, pruned_reason,
                                   created_at, updated_at
                            FROM aletheia_autopilot_hypotheses
                            WHERE project_id = :tenant_id AND session_id = :session_id
                            ORDER BY priority ASC, id ASC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "session_id": row["id"]},
                    ).mappings().all()
                    candidates = conn.execute(
                        text(
                            """
                            SELECT canonical_key, title, conclusion, value_score,
                                   confidence, novelty_score, impact_score,
                                   evidence_chain_json, evidence_limits_json,
                                   suggested_action_json, status, created_at, updated_at
                            FROM aletheia_autopilot_candidate_findings
                            WHERE project_id = :tenant_id AND session_id = :session_id
                            ORDER BY value_score DESC, confidence DESC, id ASC
                            LIMIT 100
                            """
                        ),
                        {"tenant_id": tenant.tenant_id, "session_id": row["id"]},
                    ).mappings().all()
                    runs.append(
                        {
                            "kind": "autopilot_deep_reasoning",
                            "run_key": row["session_key"],
                            "agent": row["created_by"],
                            "status": row["status"],
                            "objective": row["objective"],
                            "frontier": [_load_json(row["scope_json"], {})],
                            "trace": [
                                {
                                    "hypothesis_key": h["hypothesis_key"],
                                    "title": h["title"],
                                    "status": h["status"],
                                    "priority": h["priority"],
                                    "evidence_plan": _load_json(h["evidence_plan_json"], []),
                                    "reasoning_task_keys": _load_json(h["reasoning_task_keys_json"], []),
                                    "pruned_reason": h["pruned_reason"],
                                }
                                for h in hypotheses
                            ],
                            "safety_profile": _load_json(row["safety_profile_json"], {}),
                            "budget": _load_json(row["budget_json"], {}),
                            "skipped_sources": [
                                {"reason": h["pruned_reason"], "hypothesis": h["hypothesis_key"]}
                                for h in hypotheses
                                if h["pruned_reason"]
                            ],
                            "counts": {
                                "hypotheses": len(hypotheses),
                                "candidate_findings": len(candidates),
                                "pruned": sum(1 for h in hypotheses if h["status"] == "pruned"),
                            },
                            "elements": [
                                {
                                    "element_key": c["canonical_key"],
                                    "element_type": "candidate_finding",
                                    "name": c["title"],
                                    "payload": {
                                        "conclusion": c["conclusion"],
                                        "value_score": c["value_score"],
                                        "novelty_score": c["novelty_score"],
                                        "impact_score": c["impact_score"],
                                        "suggested_action": _load_json(c["suggested_action_json"], {}),
                                    },
                                    "evidence_refs": [
                                        item.get("source_ref") or item.get("source") or item.get("metric") or item.get("kind")
                                        for item in _load_json(c["evidence_chain_json"], [])
                                    ],
                                    "evidence_chain": _load_json(c["evidence_chain_json"], []),
                                    "confidence": c["confidence"],
                                    "status": c["status"],
                                    "created_at": _jsonable(c["created_at"]),
                                }
                                for c in candidates
                            ],
                            "started_at": _jsonable(row["created_at"]),
                            "finished_at": _jsonable(row["updated_at"]),
                            "error": None,
                            "write_boundary": {
                                "target": "candidate_findings",
                                "canonical_write": False,
                                "formal_graph_write": False,
                                "auto_approve_findings": False,
                            },
                        }
                    )
        except Exception as exc:
            degraded.append({"kind": "autopilot_deep_reasoning", "reason": _safe_error_message(exc)})

        runs.sort(key=lambda item: item.get("started_at") or item.get("finished_at") or "", reverse=True)
        return {
            "tenant": tenant.public_dict(),
            "sessions": sessions,
            "runs": runs[: limit * 3],
            "degraded": degraded,
            "write_boundary": {
                "ontology_candidates_require_review": True,
                "graph_fact_target": "proposed_graph_space",
                "candidate_findings_only": True,
                "canonical_write": False,
                "formal_graph_write": False,
            },
        }

    def proposed_graph_elements(self, tenant, run_key=None, limit=None, status_filter="pending", element_type=None, compact=False):
        limit = max(1, min(int(limit), 500)) if limit is not None else 250
        where = "e.project_id = :tenant_id"
        params = {"tenant_id": tenant.tenant_id}
        params["limit"] = limit
        if run_key:
            where += " AND r.run_key = :run_key"
            params["run_key"] = run_key
        if element_type:
            where += " AND e.element_type = :element_type"
            params["element_type"] = str(element_type).strip()
        raw_where = where
        raw_params = dict(params)
        status_filter = (status_filter or "pending").replace("-", "_").lower()
        if status_filter in {"pending", "active", "draft"}:
            where += " AND e.status IN ('draft', 'needs_more_evidence')"
        elif status_filter in {"reviewed", "closed"}:
            where += " AND e.status IN ('approved', 'rejected')"
        elif status_filter in {"approved", "rejected", "needs_more_evidence"}:
            where += " AND e.status = :status_filter"
            params["status_filter"] = status_filter
        elif status_filter in {"all", "*"}:
            pass
        else:
            raise ValueError("Unsupported proposed graph status filter")
        pending_like_filter = status_filter in {"pending", "active", "draft"}
        with self.metadata_engine_for(tenant).connect() as conn:
            approved_edge_fact_keys = set()
            if pending_like_filter:
                existing_edge_rows = conn.execute(
                    text(
                        """
                        SELECT payload_json
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id
                          AND element_type = 'edge'
                          AND status = 'approved'
                        """
                    ),
                    {"tenant_id": tenant.tenant_id},
                ).mappings().all()
                for existing_row in existing_edge_rows:
                    fact_key = _graph_edge_fact_key(_load_json(existing_row["payload_json"], {}))
                    if fact_key:
                        approved_edge_fact_keys.add(fact_key)
            summary = conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS total_count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    """
                ),
                params,
            ).mappings().first()
            raw_summary = conn.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS total_count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {raw_where}
                    """
                ),
                raw_params,
            ).mappings().first()
            type_rows = conn.execute(
                text(
                    f"""
                    SELECT e.element_type, COUNT(*) AS count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    GROUP BY e.element_type
                    """
                ),
                params,
            ).mappings().all()
            raw_type_rows = conn.execute(
                text(
                    f"""
                    SELECT e.element_type, COUNT(*) AS count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {raw_where}
                    GROUP BY e.element_type
                    """
                ),
                raw_params,
            ).mappings().all()
            raw_status_rows = conn.execute(
                text(
                    f"""
                    SELECT e.status, COUNT(*) AS count
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {raw_where}
                    GROUP BY e.status
                    """
                ),
                raw_params,
            ).mappings().all()
            rows = conn.execute(
                text(
                    f"""
                    SELECT e.element_key, e.element_type, e.name, e.payload_json,
                           e.evidence_refs_json, e.source_url, e.confidence, e.status,
                           e.iteration, e.created_at, r.run_key, r.objective,
                           r.status AS run_status, r.proposed_count, r.finding_count,
                           r.pruned_count, r.expansion_trace_json, r.safety_profile_json,
                           r.skipped_sources_json, r.started_at, r.finished_at
                    FROM aletheia_proposed_graph_elements e
                    JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                    WHERE {where}
                    ORDER BY r.started_at DESC, e.iteration ASC, e.element_type ASC, e.name ASC
                    LIMIT :limit
                    """
                ),
                params,
            ).mappings().all()
            identity_rows = []
            if not compact:
                identity_rows = [
                    {
                        "source_space": row["source_space"],
                        "source_key": row["source_key"],
                        "source_status": row["source_status"],
                        "identity_key": row["identity_key"],
                        "identity": _load_json(row["identity_json"], {}),
                        "dedup_text": row["dedup_text"],
                    }
                    for row in conn.execute(
                        text(
                            """
                            SELECT source_space, source_key, source_status, identity_key,
                                   identity_json, dedup_text
                            FROM aletheia_graph_identity_index
                            WHERE project_id = :tenant_id
                              AND element_kind = 'node'
                            """
                        ),
                        {"tenant_id": tenant.tenant_id},
                    ).mappings().all()
                ]
            proposal_match_keys = set()
            if not compact:
                for row in rows:
                    proposal_match_keys.update(_proposal_match_keys_from_payload(_load_json(row["payload_json"], {})))
            proposal_match_lookup = {}
            if proposal_match_keys:
                match_query = text(
                        """
                        SELECT e.element_key, e.element_type, e.name, e.payload_json,
                               e.evidence_refs_json, e.source_url, e.confidence, e.status,
                               e.created_at, r.run_key
                        FROM aletheia_proposed_graph_elements e
                        JOIN aletheia_iterative_graph_enrichment_runs r ON r.id = e.run_id
                        WHERE e.project_id = :tenant_id
                          AND e.element_key IN :element_keys
                        """
                ).bindparams(bindparam("element_keys", expanding=True))
                match_rows = conn.execute(
                    match_query,
                    {"tenant_id": tenant.tenant_id, "element_keys": list(proposal_match_keys)},
                ).mappings().all()
                proposal_match_lookup = {
                    match_row["element_key"]: _proposal_match_summary(match_row)
                    for match_row in match_rows
                }
        include_projection_identity = str(os.environ.get("ALETHEIA_PROPOSED_GRAPH_INCLUDE_FULL_GRAPH_IDENTITY") or "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        if include_projection_identity:
            try:
                approved_graph = self.full_graph(tenant, limit=1000) or {}
                for node in approved_graph.get("nodes", []):
                    if not isinstance(node, dict):
                        continue
                    node_id = node.get("id") or node.get("key")
                    if not node_id:
                        continue
                    identity_rows.append(
                        {
                            "source_space": "approved_graph_projection",
                            "source_key": node_id,
                            "source_status": node.get("status") or "approved",
                            "identity_key": f"approved-graph-node:{tenant.tenant_id}:{node_id}",
                            "identity": {
                                "kind": "node",
                                "entity_type": node.get("type") or node.get("ontology_type"),
                                "label": node.get("label") or node.get("name") or node_id,
                                "normalized_label": _graph_identity_text(node.get("label") or node.get("name") or node_id),
                                "aliases": node.get("aliases") if isinstance(node.get("aliases"), list) else [],
                                "source_identity": node_id or node.get("source_pk"),
                            },
                            "dedup_text": " | ".join(
                                str(value)
                                for value in [
                                    "node",
                                    node.get("type") or node.get("ontology_type"),
                                    node.get("label") or node.get("name") or node_id,
                                    node_id,
                                    node.get("source_pk"),
                                ]
                                if value
                            ),
                        }
                    )
            except Exception:
                pass
        elements = []
        runs = {}
        filtered_type_counts = {}
        pending_edge_fact_keys = set()
        for row in rows:
            payload = _load_json(row["payload_json"], {})
            payload = _attach_proposal_match_summaries(payload, proposal_match_lookup)
            row_status = row["status"]
            if pending_like_filter:
                guarded_for_filter = _apply_edge_source_identity_presentation_guard(
                    {
                        "element_key": row["element_key"],
                        "element_type": row["element_type"],
                        "name": row["name"],
                        "payload": payload,
                        "status": row_status,
                    }
                )
                payload = guarded_for_filter.get("payload") or payload
                row_status = guarded_for_filter.get("status") or row_status
            if pending_like_filter and not _is_current_graph_proposal(row_status, payload):
                continue
            if pending_like_filter and row["element_type"] == "edge":
                edge_fact_key = _graph_edge_fact_key(payload)
                if edge_fact_key:
                    if edge_fact_key in approved_edge_fact_keys or edge_fact_key in pending_edge_fact_keys:
                        continue
                    pending_edge_fact_keys.add(edge_fact_key)
            run = runs.setdefault(
                row["run_key"],
                {
                    "run_key": row["run_key"],
                    "objective": row["objective"],
                    "status": row["run_status"],
                    "proposed_count": row["proposed_count"],
                    "finding_count": row["finding_count"],
                    "pruned_count": row["pruned_count"],
                    "expansion_trace": _load_json(row["expansion_trace_json"], []),
                    "safety_profile": _load_json(row["safety_profile_json"], {}),
                    "skipped_sources": _load_json(row["skipped_sources_json"], []),
                    "started_at": _jsonable(row["started_at"]),
                    "finished_at": _jsonable(row["finished_at"]),
                },
            )
            filtered_type_counts[row["element_type"]] = filtered_type_counts.get(row["element_type"], 0) + 1
            if len(elements) < limit:
                response_payload = _compact_candidate_payload(payload) if compact else payload
                element = {
                    "element_key": row["element_key"],
                    "element_type": row["element_type"],
                    "name": row["name"],
                    "payload": response_payload,
                    "dedup_audit": _dedup_audit_from_payload(payload),
                    "evidence_refs": _load_json(row["evidence_refs_json"], []),
                    "source_url": row["source_url"],
                    "confidence": row["confidence"],
                    "status": row_status,
                    "iteration": row["iteration"],
                    "created_at": _jsonable(row["created_at"]),
                    "run_key": run["run_key"],
                }
                element.update(_knowledge_candidate_profile(row["element_type"], payload))
                element = _apply_edge_source_identity_presentation_guard(element)
                if not compact:
                    element = _apply_possible_duplicate_presentation_guard(element, identity_rows)
                elements.append(element)
        return {
            "tenant": tenant.public_dict(),
            "runs": list(runs.values()),
            "elements": elements,
            "total_count": sum(filtered_type_counts.values()) if pending_like_filter else int(summary["total_count"] or 0) if summary else len(elements),
            "element_type_counts": filtered_type_counts if pending_like_filter else {row["element_type"]: int(row["count"] or 0) for row in type_rows},
            "raw_total_count": int(raw_summary["total_count"] or 0) if raw_summary else len(elements),
            "raw_element_type_counts": {row["element_type"]: int(row["count"] or 0) for row in raw_type_rows},
            "raw_status_counts": {row["status"]: int(row["count"] or 0) for row in raw_status_rows},
            "status_filter": status_filter,
        }

    def review_proposed_graph_element(self, tenant, element_key, action, body=None):
        action = (action or "").replace("_", "-").lower()
        body = body or {}
        status_by_action = {
            "approve": "approved",
            "reject": "rejected",
            "needs-evidence": "needs_more_evidence",
            "comment": None,
        }
        if action not in status_by_action:
            raise ValueError("Unsupported graph proposal review action")
        reason = (body.get("reason") or body.get("note") or "").strip()
        if action in {"reject", "needs-evidence"} and not reason:
            raise ValueError("Review reason is required for reject or needs evidence")
        reviewer = (body.get("reviewer") or "Saskue").strip() or "Saskue"
        reviewed_at = datetime.utcnow().isoformat()
        engine = self.metadata_engine_for(tenant)
        Session = sessionmaker(bind=engine)
        with Session.begin() as session:
            row = session.execute(
                text(
                    """
                    SELECT id, run_id, element_key, element_type, name, payload_json, evidence_refs_json,
                           source_url, confidence, status, iteration, created_at
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {"tenant_id": tenant.tenant_id, "element_key": element_key},
            ).mappings().first()
            if row is None:
                return None
            payload = _load_json(row["payload_json"], {})
            before_status = row["status"]
            after_status = status_by_action[action] or before_status
            quality_gate = None
            if action == "approve":
                quality_gate = self._ontology_candidate_quality_gate(row, payload)
                if quality_gate:
                    after_status = "needs_more_evidence"
                    payload["quality_gate"] = {
                        **quality_gate,
                        "requested_action": action,
                        "reviewed_at": reviewed_at,
                    }
            promoted_artifact = None
            should_promote_ontology = (
                action == "approve"
                and not quality_gate
                and str(row["element_type"] or "").lower() == "ontology_concept"
            )
            if should_promote_ontology:
                promoted_artifact = self._promote_ontology_candidate_to_catalog(tenant, session, row, payload, reviewer, reason, reviewed_at)
            catalog_write = bool(promoted_artifact and promoted_artifact.get("artifact_type") != "object_instance" and promoted_artifact.get("catalog_write", True))
            graph_space_write = bool(catalog_write and promoted_artifact and promoted_artifact.get("graph_space_element_key"))
            review_event = {
                "decision": action,
                "reviewer": reviewer,
                "reason": reason,
                "before_status": before_status,
                "after_status": after_status,
                "created_at": reviewed_at,
                "canonical_write": catalog_write,
                "graph_space_write": graph_space_write,
                "formal_graph_write": False,
                "promoted_artifact": promoted_artifact,
                "quality_gate": quality_gate,
            }
            for audit_key in (
                "review_actor",
                "machine_approval",
                "approval_policy",
                "approval_policy_version",
                "approval_thresholds",
            ):
                if audit_key in body:
                    review_event[audit_key] = body[audit_key]
            payload.setdefault("review_events", []).append(review_event)
            payload["review_boundary"] = {
                "writes_canonical": catalog_write,
                "writes_graph_space": graph_space_write,
                "writes_formal_graph": False,
                "status_scope": (
                    "ontology_candidate_and_catalog"
                    if catalog_write
                    else "ontology_candidate_and_existing_instance"
                    if promoted_artifact and promoted_artifact.get("artifact_type") == "object_instance"
                    else "ontology_candidate_only"
                ),
                "promoted_artifact": promoted_artifact,
            }
            session.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET status = :status, payload_json = :payload_json
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                {
                    "tenant_id": tenant.tenant_id,
                    "element_key": element_key,
                    "status": after_status,
                    "payload_json": _json_dump(payload),
                },
            )
            if (
                str(row["element_type"] or "").lower() == "ontology_concept"
                and after_status in {"approved", "rejected", "needs_more_evidence"}
                and self._ontology_candidate_is_concrete_object(payload, payload.get("artifact_type"))
            ):
                session.execute(
                    text(
                        """
                        DELETE FROM aletheia_graph_identity_index
                        WHERE project_id = :tenant_id
                          AND source_space = 'proposed_graph'
                          AND source_key = :element_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "element_key": element_key},
                )
        if after_status == "approved" and str(row["element_type"] or "").lower() == "ontology_concept":
            if self._ontology_candidate_is_concrete_object(payload, payload.get("artifact_type")):
                self._materialize_ontology_concept_vertex(tenant, row, payload)
            elif (
                str(payload.get("artifact_type") or "").lower() in {"link", "relation"}
                or str(payload.get("ontology_part") or "").lower() == "relation"
            ):
                self._materialize_ontology_concept_edge(tenant, row, payload)
        element = {
            "element_key": row["element_key"],
            "element_type": row["element_type"],
            "name": row["name"],
            "payload": payload,
            "dedup_audit": _dedup_audit_from_payload(payload),
            "evidence_refs": _load_json(row["evidence_refs_json"], []),
            "source_url": row["source_url"],
            "confidence": row["confidence"],
            "status": after_status,
            "iteration": row["iteration"],
            "created_at": _jsonable(row["created_at"]),
        }
        element.update(_knowledge_candidate_profile(row["element_type"], payload))
        return {
            "tenant": tenant.public_dict(),
            "element": element,
            "review": review_event,
            "promoted_artifact": promoted_artifact,
            "write_boundary": {
                "canonical_write": catalog_write,
                "graph_space_write": graph_space_write,
                "formal_graph_write": False,
                "target": (
                    "ontology_catalog_and_graph_space"
                    if catalog_write
                    else "existing_graph_instance_match"
                    if promoted_artifact and promoted_artifact.get("artifact_type") == "object_instance"
                    else "ontology_candidate_review"
                ),
            },
        }

    def _proposed_graph_element_requires_ontology_review(self, payload):
        payload = payload or {}
        boundary = payload.get("review_boundary") or payload.get("write_boundary") or payload.get("governance") or {}
        return any(
            bool(value)
            for value in (
                payload.get("requires_ontology_proposal"),
                payload.get("ontology_proposal_required"),
                payload.get("requires_ontology_review"),
                boundary.get("requires_ontology_proposal"),
                boundary.get("ontology_proposal_required"),
                boundary.get("requires_ontology_review"),
            )
        )

    def _ontology_catalog_type_for_candidate(self, artifact_type):
        raw = str(artifact_type or "object").strip().lower()
        if raw in {"class", "object"}:
            return "object"
        if raw in {"relation", "link"}:
            return "link"
        if raw == "property":
            return "property"
        if raw == "event":
            return "event"
        if raw in {"action", "function", "policy"}:
            return "action"
        return "object"

    def _ontology_candidate_natural_key(self, payload):
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        artifact_type = str(payload.get("artifact_type") or identity.get("artifact_type") or "object").strip().lower()
        label = str(payload.get("label") or identity.get("label") or identity.get("normalized_label") or "ontology-concept").strip()
        domain = str(payload.get("domain") or identity.get("domain") or "").strip()
        range_type = str(payload.get("range") or identity.get("range") or "").strip()
        property_of = str(payload.get("property_of") or identity.get("property_of") or "").strip()
        source_identity = str(payload.get("source_identity") or identity.get("source_identity") or identity.get("property_fingerprint") or "").strip()
        key_material = {
            "artifact_type": artifact_type,
            "label": label,
            "domain": domain,
            "range": range_type,
            "property_of": property_of,
            "source_identity": source_identity,
        }
        digest = hashlib.sha1(_json_dump(key_material).encode("utf-8")).hexdigest()[:12]
        return f"{_slug(artifact_type)}-{_slug(label)[:90]}-{digest}"

    def _ontology_candidate_is_concrete_object(self, payload, artifact_type):
        ontology_part = str(payload.get("ontology_part") or "").strip().lower()
        source_type = str(payload.get("source_artifact_type") or artifact_type or "").strip().lower()
        raw_type = str(artifact_type or payload.get("artifact_type") or "").strip().lower()
        return raw_type in {"object", "entity", "instance"} and (
            ontology_part in {"concrete_object", "object_instance", "instance"}
            or source_type in {"entity", "instance"}
        )

    def _ontology_candidate_concrete_object_quality(self, row, payload):
        payload = payload or {}
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        ontology_candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        source_url = row.get("source_url") if hasattr(row, "get") else None
        confidence = row.get("confidence") if hasattr(row, "get") else None
        label = (
            payload.get("label")
            or identity.get("label")
            or identity.get("normalized_label")
            or ontology_candidate.get("label")
            or (row.get("name") if hasattr(row, "get") else None)
        )
        class_label = _first_nonempty(
            payload.get("class_label"),
            payload.get("object_type"),
            payload.get("entity_type"),
            payload.get("ontology_type"),
            payload.get("type"),
            payload.get("type_label"),
            identity.get("class_label"),
            identity.get("object_type"),
            identity.get("entity_type"),
            identity.get("ontology_type"),
            identity.get("type"),
            ontology_candidate.get("class_label"),
            ontology_candidate.get("object_type"),
            ontology_candidate.get("entity_type"),
            ontology_candidate.get("ontology_type"),
            ontology_candidate.get("type"),
        )
        return concrete_object_quality(
            {"label": label, "class_label": class_label},
            {"source_url": source_url, "confidence": confidence},
        )

    def _ontology_candidate_quality_gate(self, row, payload):
        artifact_type = str(payload.get("artifact_type") or "").strip().lower()
        if str(row["element_type"] or "").lower() != "ontology_concept":
            return None
        if not self._ontology_candidate_is_concrete_object(payload, artifact_type):
            return None
        quality = self._ontology_candidate_concrete_object_quality(row, payload)
        if not quality.get("issues"):
            return None
        return {
            "decision": "needs_more_evidence",
            "reason": "approved_concrete_object_failed_generic_quality_gate",
            "score": quality.get("score"),
            "issues": quality.get("issues") or [],
        }

    def _ontology_candidate_instance_queries(self, payload, label):
        identity = payload.get("identity") if isinstance(payload.get("identity"), dict) else {}
        values = [
            payload.get("source_identity"),
            payload.get("instance_id"),
            identity.get("source_identity"),
            identity.get("instance_id"),
            identity.get("primary_key"),
            identity.get("id"),
            label,
        ]
        queries = []
        for value in values:
            text_value = str(value or "").strip()
            if text_value and text_value not in queries:
                queries.append(text_value)
            if ":" in text_value:
                suffix = text_value.split(":", 1)[1].strip()
                if suffix and suffix not in queries:
                    queries.append(suffix)
        return queries

    def _resolve_ontology_candidate_existing_instance(self, tenant, payload, *, catalog_type, artifact_type, label):
        ontology_part = str(payload.get("ontology_part") or artifact_type or "").strip().lower()
        raw_artifact_type = str(artifact_type or payload.get("source_artifact_type") or "").strip().lower()
        if catalog_type != "object" or raw_artifact_type not in {"object", "entity", "instance"}:
            return None
        if ontology_part in {"class", "abstract_class"}:
            return None
        try:
            object_types = self.types(tenant, include_draft=False).get("types") or []
        except Exception:
            object_types = []
        for item in object_types:
            candidate_type = item.get("type")
            if not candidate_type:
                continue
            for query in self._ontology_candidate_instance_queries(payload, label):
                try:
                    matches = self.search(tenant, candidate_type, query, limit=5, include_draft=False).get("instances") or []
                except Exception:
                    matches = []
                match = next((row for row in matches if str(row.get("id") or "").lower() == f"{candidate_type}:{query}".lower()), None)
                match = match or (matches[0] if matches else None)
                if not match:
                    continue
                instance_id = str(match.get("id") or f"{candidate_type}:{query}").split(":", 1)[1] if ":" in str(match.get("id") or "") else query
                return {
                    "id": None,
                    "canonical_key": match.get("id") or f"{candidate_type}:{instance_id}",
                    "artifact_type": "object_instance",
                    "name": f"{label} ({instance_id})" if instance_id not in str(label) else label,
                    "status": match.get("status") or "approved",
                    "version": None,
                    "graph_space_element_key": None,
                    "ontology_artifact": match.get("ontology_artifact") or item.get("ontology_artifact"),
                    "instance_type": candidate_type,
                    "instance_id": instance_id,
                    "matched_existing_instance": True,
                    "match_method": "schema_exact_instance_query",
                    "aliases": list(dict.fromkeys([label, query, match.get("label")])),
                }
        return None

    def _ontology_graph_space_element_key(self, tenant_id, canonical_key):
        digest = hashlib.sha1(str(canonical_key or "").encode("utf-8")).hexdigest()[:16]
        return f"ontology-model:{tenant_id}:node:{digest}"

    def _upsert_ontology_model_graph_projection(self, tenant, session, row, payload, artifact, source_refs):
        graph_key = self._ontology_graph_space_element_key(tenant.tenant_id, artifact.canonical_key)
        graph_payload = {
            "label": artifact.name,
            "ontology_artifact": artifact.canonical_key,
            "artifact_type": artifact.artifact_type,
            "ontology_part": payload.get("ontology_part"),
            "source_artifact_type": payload.get("source_artifact_type"),
            "source_proposed_graph_element_key": payload.get("source_proposed_graph_element_key"),
            "source_url": row["source_url"],
            "source_refs": source_refs,
            "evidence_quote": payload.get("evidence_quote"),
            "description": artifact.description,
            "projection_source": "OntologyModelGraph",
            "graph_space": {
                "space": "ontology_model",
                "node_id": f"ontology:{artifact.canonical_key}",
                "materialized_from": "approved_ontology_artifact",
                "canonical_key": artifact.canonical_key,
                "writes_formal_graph": False,
            },
            "review_boundary": {
                "writes_canonical": False,
                "writes_graph_space": True,
                "writes_formal_graph": False,
                "status_scope": "ontology_model_graph_space_projection",
            },
        }
        existing = session.execute(
            text(
                """
                SELECT id
                FROM aletheia_proposed_graph_elements
                WHERE project_id = :tenant_id AND element_key = :element_key
                """
            ),
            {"tenant_id": tenant.tenant_id, "element_key": graph_key},
        ).mappings().first()
        params = {
            "run_id": row["run_id"],
            "tenant_id": tenant.tenant_id,
            "element_key": graph_key,
            "name": artifact.name,
            "payload_json": _json_dump(graph_payload),
            "evidence_refs_json": _json_dump(source_refs),
            "source_url": row["source_url"],
            "confidence": float(row["confidence"] or artifact.confidence or 0.0),
        }
        if existing:
            session.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET run_id = :run_id,
                        element_type = 'ontology_model_projection',
                        name = :name,
                        payload_json = :payload_json,
                        evidence_refs_json = :evidence_refs_json,
                        source_url = :source_url,
                        confidence = :confidence,
                        status = 'approved'
                    WHERE project_id = :tenant_id AND element_key = :element_key
                    """
                ),
                params,
            )
        else:
            session.execute(
                text(
                    """
                    INSERT INTO aletheia_proposed_graph_elements
                        (run_id, project_id, element_key, element_type, name,
                         payload_json, evidence_refs_json, source_url,
                         confidence, status, iteration, created_at)
                    VALUES
                        (:run_id, :tenant_id, :element_key, 'ontology_model_projection', :name,
                         :payload_json, :evidence_refs_json, :source_url,
                         :confidence, 'approved', 1, CURRENT_TIMESTAMP)
                    """
                ),
                params,
            )
        return graph_key

    def _promote_ontology_candidate_to_catalog(self, tenant, session, row, payload, reviewer, reason, reviewed_at):
        if str(row["element_type"] or "").lower() != "ontology_concept":
            return None
        ontology_candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
        artifact_type = str(payload.get("artifact_type") or ontology_candidate.get("artifact_type") or "object").strip().lower()
        catalog_type = self._ontology_catalog_type_for_candidate(artifact_type)
        label = str(payload.get("label") or row["name"] or "Ontology concept").strip()
        description = str(payload.get("description") or ontology_candidate.get("description") or "").strip()
        source_refs = list(dict.fromkeys([*(_load_json(row["evidence_refs_json"], []) or []), row["source_url"] or ""]))
        source_refs = [ref for ref in source_refs if ref]
        existing_instance = self._resolve_ontology_candidate_existing_instance(
            tenant,
            payload,
            catalog_type=catalog_type,
            artifact_type=artifact_type,
            label=label,
        )
        if existing_instance:
            payload["instance_resolution"] = {
                "decision": "merge_existing_instance",
                "canonical_key": existing_instance["canonical_key"],
                "ontology_artifact": existing_instance.get("ontology_artifact"),
                "match_method": existing_instance.get("match_method"),
                "aliases": existing_instance.get("aliases") or [],
                "resolved_at": reviewed_at,
            }
            return existing_instance
        if catalog_type == "object" and self._ontology_candidate_is_concrete_object(payload, artifact_type):
            payload["catalog_resolution"] = {
                "decision": "not_promoted_to_ontology_catalog",
                "reason": "concrete_object_is_instance_knowledge_not_schema",
                "reviewed_at": reviewed_at,
            }
            return None
        reviewed_payload = {
            **payload,
            "artifact_type": catalog_type,
            "ontology_part": payload.get("ontology_part") or artifact_type,
            "source_artifact_type": artifact_type,
            "source_proposed_graph_element_key": row["element_key"],
            "promotion": {
                "promoted_from": "proposed_graph_elements",
                "source_element_key": row["element_key"],
                "reviewer": reviewer,
                "reason": reason,
                "promoted_at": reviewed_at,
                "canonical_write": True,
                "formal_graph_write": False,
            },
        }
        artifact = upsert_artifact(
            session,
            artifact_type=catalog_type,
            natural_key=self._ontology_candidate_natural_key(payload),
            name=label,
            description=description or f"Approved ontology {artifact_type}: {label}",
            payload=reviewed_payload,
            source_refs=source_refs,
            source_agent="DeepResearchOntologyExpansion",
            project_id=tenant.tenant_id,
            confidence=float(row["confidence"] or 0.0),
            status="approved",
        )
        session.flush()
        if catalog_type in ("object", "link"):
            self._materialize_ontology_concept_type(tenant, session, catalog_type, label, description, payload, source_refs, float(row["confidence"] or 0.0))
        graph_space_element_key = self._upsert_ontology_model_graph_projection(tenant, session, row, reviewed_payload, artifact, source_refs)
        return {
            "id": artifact.id,
            "canonical_key": artifact.canonical_key,
            "artifact_type": artifact.artifact_type,
            "name": artifact.name,
            "status": artifact.status,
            "version": artifact.version,
            "graph_space_element_key": graph_space_element_key,
        }

    def _materialize_ontology_concept_type(self, tenant, session, catalog_type, label, description, payload, source_refs, confidence):
        """Register the approved class/relation concept as a real graph-native
        node/edge TYPE (agents/graph_ontology_registry.py) and sync it to a
        Nebula TAG/EDGE TYPE, so the concrete object/relation instances that
        reference it (approved separately, see
        `_materialize_ontology_concept_vertex`/`_edge`) have somewhere to
        write to. Best-effort: DeepResearchOntologyExpansion approvals must
        not fail because Nebula is briefly unreachable -- the Postgres
        catalog write above already committed; this can retry on the next
        approval."""
        try:
            if catalog_type == "object":
                ontology_registry.propose_node_type(
                    session,
                    tenant_id=tenant.tenant_id,
                    name=label,
                    description=description,
                    properties=_ONTOLOGY_CONCEPT_VERTEX_PROPERTIES,
                    confidence=confidence,
                    evidence=source_refs,
                    status="approved",
                )
            else:
                domain = [str(payload.get("source_object_type") or payload.get("domain") or "*")]
                range_ = [str(payload.get("target_object_type") or payload.get("range") or "*")]
                ontology_registry.propose_edge_type(
                    session,
                    tenant_id=tenant.tenant_id,
                    name=label,
                    domain=domain,
                    range=range_,
                    description=description,
                    properties=_ONTOLOGY_CONCEPT_EDGE_PROPERTIES,
                    confidence=confidence,
                    evidence=source_refs,
                    status="approved",
                )
            session.flush()
        except Exception as exc:
            print(f"[InstanceRepository] Failed to register graph-native type for {label!r}: {_safe_error_message(exc)}")
            return
        self._sync_graph_native_schema(tenant)

    def _materialize_ontology_concept_vertex(self, tenant, row, payload):
        """Approved concrete-object DeepResearchOntologyExpansion proposal ->
        a real Nebula vertex. Reuses the exact same class/id computation the
        virtual-graph read path (`_ontology_concrete_object_node`) already
        uses, so the vertex id here matches `node["id"]`/`instance_id`
        computed there -- the merge logic in `full_graph`/`types`/`search`
        naturally dedupes the two once this succeeds, no separate cleanup
        needed. Best-effort: failure (e.g. the TYPE hasn't synced to Nebula
        yet) must not fail the approval -- the Postgres row already committed
        and the virtual-graph read path still serves it either way."""
        try:
            class_catalog = self._ontology_class_catalog(tenant)
            label = self._ontology_concrete_object_label(row, payload)
            object_type = self._ontology_concrete_object_class(tenant, payload, label, class_catalog=class_catalog)
            node_id = self._ontology_concrete_object_node_id(object_type, label, payload, row)
            instance_id = node_id.split(":", 1)[1] if ":" in node_id else node_id
            candidate = payload.get("ontology_candidate") if isinstance(payload.get("ontology_candidate"), dict) else {}
            props = {
                "label": label,
                "description": str(payload.get("description") or candidate.get("description") or ""),
                "evidence_quote": str(payload.get("evidence_quote") or ""),
                "source_url": str(row["source_url"] or ""),
            }
            repo = self._graph_repo_for(tenant)
            repo._ensure_connected()
            insert_with_schema_retry(lambda: repo._client.insert_vertices(object_type, [{"id": instance_id, **props}]))
        except Exception as exc:
            print(f"[InstanceRepository] Failed to materialize vertex for element {row['element_key']!r}: {_safe_error_message(exc)}")

    def _materialize_ontology_concept_edge(self, tenant, row, payload):
        """Approved relation-instance DeepResearchOntologyExpansion proposal
        -> a real Nebula edge. Computes source/target vertex ids the same
        deterministic way `_materialize_ontology_concept_vertex` would for
        those labels/types -- best-effort, same as the existing read-time
        fuzzy label match `_ontology_relation_instance_edges` already does;
        if the underlying object was dedup-merged into a different id via
        its own `matched_node_key`, this can miss, same limitation the
        virtual-graph projection already has today."""
        try:
            source_type = str(payload.get("source_object_type") or payload.get("source_type") or payload.get("domain") or "").strip()
            target_type = str(payload.get("target_object_type") or payload.get("target_type") or payload.get("range") or "").strip()
            source_label = str(payload.get("source_label") or "").strip()
            target_label = str(payload.get("target_label") or "").strip()
            if not source_type or not target_type or not source_label or not target_label:
                return
            source_node_id = self._ontology_concrete_object_node_id(source_type, source_label, {}, row)
            target_node_id = self._ontology_concrete_object_node_id(target_type, target_label, {}, row)
            source_instance_id = source_node_id.split(":", 1)[1] if ":" in source_node_id else source_node_id
            target_instance_id = target_node_id.split(":", 1)[1] if ":" in target_node_id else target_node_id
            relation = str(payload.get("relation") or payload.get("label") or row["name"] or "relation").strip()
            props = {
                "evidence_quote": str(payload.get("evidence_quote") or ""),
                "source_url": str(row["source_url"] or ""),
            }
            repo = self._graph_repo_for(tenant)
            repo._ensure_connected()
            insert_with_schema_retry(
                lambda: repo._client.insert_edges(relation, [{"source_id": source_instance_id, "target_id": target_instance_id, **props}])
            )
        except Exception as exc:
            print(f"[InstanceRepository] Failed to materialize edge for element {row['element_key']!r}: {_safe_error_message(exc)}")

    def _auto_review_similarity_score(self, payload):
        payload = payload or {}
        for key in ("match_score", "score", "similarity_score"):
            try:
                value = payload.get(key)
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                pass
        try:
            distance = payload.get("vector_distance")
            if distance is not None:
                return max(0.0, min(1.0, 1.0 - float(distance)))
        except (TypeError, ValueError):
            pass
        return 0.0

    def _should_auto_reject_similar_proposal(self, element, config):
        if not (config or {}).get("auto_review_similar_proposals"):
            return False, "disabled", 0.0
        payload = element.get("payload") or {}
        status = str(element.get("status") or "").replace("-", "_").lower()
        if status not in {"draft", "needs_review", "needs_more_evidence"}:
            return False, f"status_not_pending:{status or 'unknown'}", 0.0
        if payload.get("decision_reason") == "structural_conflict" or payload.get("conflict_fields"):
            return False, "structural_conflict_requires_human_review", self._auto_review_similarity_score(payload)
        score = self._auto_review_similarity_score(payload)
        threshold = float((config or {}).get("auto_reject_similarity_threshold") or 0.92)
        if score < threshold:
            return False, "below_similarity_threshold", score
        decision = str(payload.get("dedup_decision") or element.get("dedup_decision") or "").replace("-", "_").lower()
        matched_key = payload.get("matched_node_key") or payload.get("matched_edge_key") or payload.get("matched_element_key")
        if decision in {"duplicate_existing_proposal", "duplicate_current_run", "merge_existing"} and matched_key:
            return True, "high_similarity_duplicate", score
        if decision == "needs_review" and matched_key and payload.get("match_method") in {"vector_embedding", "embedding_degraded_alias_scan"}:
            return True, "high_similarity_needs_review_duplicate", score
        return False, "not_duplicate_decision", score

    def _proposal_duplicate_score(self, element):
        payload = element.get("payload") or {}
        audit = _dedup_audit_from_payload(payload)
        score = self._auto_review_similarity_score({**payload, **audit})
        if score:
            return max(0.0, min(float(score), 1.0))
        nearest = payload.get("nearest_proposal_match") if isinstance(payload.get("nearest_proposal_match"), dict) else {}
        try:
            if nearest.get("score") is not None:
                return max(0.0, min(float(nearest.get("score")), 1.0))
            if nearest.get("distance") is not None:
                return max(0.0, min(1.0 - float(nearest.get("distance")), 1.0))
        except (TypeError, ValueError):
            pass
        decision = str(payload.get("dedup_decision") or audit.get("dedup_decision") or "").replace("-", "_").lower()
        if decision == "new_proposal":
            return 0.0
        if decision in {"duplicate_existing_proposal", "duplicate_current_run", "merge_existing", "needs_review"}:
            return 1.0
        return 0.0

    def _should_auto_approve_low_duplicate_proposal(self, element, config):
        if not (config or {}).get("auto_approve_low_duplicate_proposals", False):
            return False, "disabled", 0.0
        element_type = str(element.get("element_type") or "").lower()
        payload = element.get("payload") or {}
        if element_type in {"ontology_model_projection", "ontology_model_node"}:
            return False, "ontology_model_projection_not_auto_approved", 0.0
        if element_type == "ontology_concept":
            artifact_type = str(payload.get("artifact_type") or "").strip().lower()
            ontology_part = str(payload.get("ontology_part") or "").strip().lower()
            if artifact_type == "class" or ontology_part in {"class", "abstract_class"}:
                return False, "ontology_class_requires_human_review", 0.0
            if ontology_part not in {"concrete_object", "object_instance", "instance", "relation", "property", "event"}:
                return False, "ontology_model_concept_requires_human_review", 0.0
            quality_gate = self._ontology_candidate_quality_gate(element, payload)
            if quality_gate:
                return False, "concrete_object_quality_gate", 0.0
        status = str(element.get("status") or "").replace("-", "_").lower()
        if status not in {"draft", "needs_review", "needs_more_evidence", "proposed"}:
            return False, f"status_not_pending:{status or 'unknown'}", 0.0
        if payload.get("decision_reason") == "structural_conflict" or payload.get("conflict_fields"):
            return False, "structural_conflict_requires_human_review", self._proposal_duplicate_score(element)
        try:
            confidence = float(element.get("confidence") if element.get("confidence") is not None else payload.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        min_confidence = float((config or {}).get("auto_approve_min_confidence") or 0.8)
        if confidence < min_confidence:
            return False, "below_confidence_threshold", self._proposal_duplicate_score(element)
        duplicate_score = self._proposal_duplicate_score(element)
        max_duplicate_score = float((config or {}).get("auto_approve_max_duplicate_score") or 0.5)
        if duplicate_score > max_duplicate_score:
            return False, "above_duplicate_threshold", duplicate_score
        decision = str(payload.get("dedup_decision") or "").replace("-", "_").lower()
        if decision in {"duplicate_existing_proposal", "duplicate_current_run", "merge_existing"}:
            return False, "duplicate_decision_requires_human_or_reject_flow", duplicate_score
        return True, "high_confidence_low_duplicate", duplicate_score

    def _continuous_llm_verify_auto_reject(self, element, score, reason, config):
        if not (config or {}).get("auto_review_llm_verifier", True):
            return True, {"verifier": "deterministic", "decision": "reject_duplicate", "reason": "llm_verifier_disabled"}
        api_key = _configured_api_key("GEMINI_API_KEY", "GOOGLE_API_KEY")
        if not api_key:
            return False, {"verifier": "llm", "decision": "skip", "reason": "missing_api_key"}
        try:
            from google import genai
        except Exception as exc:
            return False, {"verifier": "llm", "decision": "skip", "reason": f"google_genai_unavailable: {_safe_error_message(exc)}"}
        payload = element.get("payload") or {}
        prompt = {
            "task": "Review whether a proposed graph element should be auto-rejected as a duplicate.",
            "rules": [
                "Return strict JSON only: {\"decision\":\"reject_duplicate|keep_for_human_review\",\"reason\":\"...\"}.",
                "Choose reject_duplicate only when the proposal is clearly the same entity/fact/finding as the matched existing item.",
                "Choose keep_for_human_review if source node, target node, relation, entity type, or source identity differ in a meaningful way.",
                "Choose keep_for_human_review for structural conflicts, ambiguous endpoints, or evidence that supports a different claim.",
                "This decision only updates proposed graph review status; it never writes canonical ontology or formal graph data.",
            ],
            "proposal": {
                "key": element.get("element_key") or element.get("key"),
                "type": element.get("element_type"),
                "name": element.get("name"),
                "status": element.get("status"),
                "confidence": element.get("confidence"),
                "source_url": element.get("source_url"),
                "dedup_decision": payload.get("dedup_decision"),
                "decision_reason": payload.get("decision_reason"),
                "conflict_fields": payload.get("conflict_fields") or [],
                "match_score": score,
                "auto_reason": reason,
                "matched_key": payload.get("matched_node_key") or payload.get("matched_edge_key") or payload.get("matched_element_key"),
                "matched_source": payload.get("matched_source"),
                "matched_status": payload.get("matched_status"),
                "identity": payload.get("identity"),
                "match_evidence": payload.get("match_evidence") or [],
                "vector_top_k": (payload.get("vector_top_k") or [])[:5],
            },
        }
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=(config or {}).get("auto_review_model") or DEFAULT_LLM_MODEL,
                contents=json.dumps(prompt, ensure_ascii=False),
            )
            raw_text = (getattr(response, "text", "") or "").strip()
            if raw_text.startswith("```"):
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
                raw_text = re.sub(r"\s*```$", "", raw_text)
            parsed = json.loads(raw_text)
        except Exception as exc:
            return False, {"verifier": "llm", "decision": "skip", "reason": f"llm_error: {_safe_error_message(exc)}"}
        decision = str(parsed.get("decision") if isinstance(parsed, dict) else "").strip()
        llm_reason = str(parsed.get("reason") if isinstance(parsed, dict) else "").strip()
        if decision == "reject_duplicate":
            return True, {"verifier": "llm", "decision": decision, "reason": llm_reason}
        return False, {"verifier": "llm", "decision": decision or "keep_for_human_review", "reason": llm_reason or "llm_kept_for_review"}

    def _continuous_auto_review_similar_proposals(self, tenant, proposed_graph, config):
        if not (config or {}).get("auto_review_similar_proposals"):
            return {"enabled": False, "reviewed": [], "skipped": []}
        reviewed = []
        skipped = []
        reviewer = str((config or {}).get("auto_review_reviewer") or "Continuous Enrichment Agent")
        threshold = float((config or {}).get("auto_reject_similarity_threshold") or 0.92)
        for element in proposed_graph or []:
            element_key = element.get("element_key") or element.get("key")
            if not element_key:
                continue
            should_reject, reason, score = self._should_auto_reject_similar_proposal(element, config)
            if not should_reject:
                skipped.append({"element_key": element_key, "reason": reason, "similarity_score": round(score, 4)})
                continue
            verified, verifier_trace = self._continuous_llm_verify_auto_reject(element, score, reason, config)
            if not verified:
                skipped.append({"element_key": element_key, "reason": "llm_kept_for_review", "similarity_score": round(score, 4), "verifier": verifier_trace})
                continue
            review_reason = (
                f"Auto-rejected by enrichment agent: similarity {score:.4f} >= {threshold:.4f}; "
                f"{reason}; matched existing proposal/object; verifier={verifier_trace.get('verifier')}: {verifier_trace.get('reason') or verifier_trace.get('decision')}."
            )
            try:
                result = self.review_proposed_graph_element(
                    tenant,
                    element_key,
                    "reject",
                    {"reviewer": reviewer, "reason": review_reason},
                )
            except Exception as exc:
                skipped.append({"element_key": element_key, "reason": f"review_failed: {_safe_error_message(exc)}", "similarity_score": round(score, 4)})
                continue
            if result:
                reviewed.append({"element_key": element_key, "reason": reason, "similarity_score": round(score, 4), "verifier": verifier_trace})
        return {"enabled": True, "reviewed": reviewed, "skipped": skipped}

    def _continuous_auto_approve_low_duplicate_proposals(self, tenant, proposed_graph, config):
        if not (config or {}).get("auto_approve_low_duplicate_proposals", False):
            return {"enabled": False, "reviewed": [], "skipped": []}
        reviewed = []
        skipped = []
        reviewer = str((config or {}).get("auto_review_reviewer") or "Continuous Enrichment Agent")
        min_confidence = float((config or {}).get("auto_approve_min_confidence") or 0.8)
        max_duplicate_score = float((config or {}).get("auto_approve_max_duplicate_score") or 0.5)
        for element in proposed_graph or []:
            element_key = element.get("element_key") or element.get("key")
            if not element_key:
                continue
            should_approve, reason, duplicate_score = self._should_auto_approve_low_duplicate_proposal(element, config)
            confidence = element.get("confidence")
            try:
                confidence = float(confidence if confidence is not None else 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if not should_approve:
                skipped.append(
                    {
                        "element_key": element_key,
                        "reason": reason,
                        "confidence": round(confidence, 4),
                        "duplicate_score": round(duplicate_score, 4),
                        "approval_policy": "auto_approve_low_duplicate_proposals",
                        "review_actor": "machine",
                    }
                )
                continue
            review_reason = (
                f"Auto-approved by review settings: confidence {confidence:.4f} >= {min_confidence:.4f}; "
                f"duplicate score {duplicate_score:.4f} <= {max_duplicate_score:.4f}; {reason}."
            )
            try:
                result = self.review_proposed_graph_element(
                    tenant,
                    element_key,
                    "approve",
                    {
                        "reviewer": reviewer,
                        "reason": review_reason,
                        "review_surface": "graph",
                        "review_actor": "machine",
                        "machine_approval": True,
                        "approval_policy": "auto_approve_low_duplicate_proposals",
                        "approval_policy_version": "v1",
                        "approval_thresholds": {
                            "min_confidence": min_confidence,
                            "max_duplicate_score": max_duplicate_score,
                        },
                    },
                )
            except Exception as exc:
                skipped.append(
                    {
                        "element_key": element_key,
                        "reason": f"review_failed: {_safe_error_message(exc)}",
                        "confidence": round(confidence, 4),
                        "duplicate_score": round(duplicate_score, 4),
                        "approval_policy": "auto_approve_low_duplicate_proposals",
                        "review_actor": "machine",
                    }
                )
                continue
            if result:
                reviewed.append(
                    {
                        "element_key": element_key,
                        "reason": reason,
                        "confidence": round(confidence, 4),
                        "duplicate_score": round(duplicate_score, 4),
                        "approval_policy": "auto_approve_low_duplicate_proposals",
                        "review_actor": "machine",
                    }
                )
        return {"enabled": True, "reviewed": reviewed, "skipped": skipped}

    def review_proposed_graph_elements_batch(self, tenant, element_keys, action, body=None):
        action = (action or "").replace("_", "-").lower()
        body = body or {}
        if action not in {"approve", "reject", "needs-evidence", "comment"}:
            raise ValueError("Unsupported graph proposal review action")
        if not isinstance(element_keys, list) or not element_keys:
            raise ValueError("Batch review requires element_keys")
        unique_keys = []
        seen = set()
        for key in element_keys:
            key = str(key or "").strip()
            if key and key not in seen:
                seen.add(key)
                unique_keys.append(key)
        if not unique_keys:
            raise ValueError("Batch review requires element_keys")
        if len(unique_keys) > 200:
            raise ValueError("Batch review is limited to 200 proposed graph elements")

        reason = (body.get("reason") or body.get("note") or "").strip()
        reviewer = (body.get("reviewer") or "Itachi").strip() or "Itachi"
        results = []
        for element_key in unique_keys:
            with self.metadata_engine_for(tenant).connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT element_key, element_type, payload_json
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :tenant_id AND element_key = :element_key
                        """
                    ),
                    {"tenant_id": tenant.tenant_id, "element_key": element_key},
                ).mappings().first()
            if row is None:
                results.append({"element_key": element_key, "ok": False, "error": "Proposed graph element not found"})
                continue
            payload = _load_json(row["payload_json"], {})
            review_surface = str(body.get("review_surface") or "").strip().lower()
            is_ontology_review = review_surface == "ontology" or str(row["element_type"] or "").lower() == "ontology_concept"
            if action == "approve" and not is_ontology_review and self._proposed_graph_element_requires_ontology_review(payload):
                results.append(
                    {
                        "element_key": element_key,
                        "element_type": row["element_type"],
                        "ok": False,
                        "error": "Requires ontology proposal review before graph approval",
                    }
                )
                continue
            try:
                result = self.review_proposed_graph_element(
                    tenant,
                    element_key,
                    action,
                    {"reason": reason, "reviewer": reviewer},
                )
            except ValueError as exc:
                results.append(
                    {
                        "element_key": element_key,
                        "element_type": row["element_type"],
                        "ok": False,
                        "error": str(exc),
                    }
                )
                continue
            if result is None:
                results.append({"element_key": element_key, "ok": False, "error": "Proposed graph element not found"})
                continue
            results.append(
                {
                    "element_key": element_key,
                    "element_type": result["element"]["element_type"],
                    "ok": True,
                    "status": result["element"]["status"],
                    "element": result["element"],
                    "review": result["review"],
                }
            )
        ok_count = sum(1 for item in results if item.get("ok"))
        return {
            "tenant": tenant.public_dict(),
            "action": action,
            "requested_count": len(unique_keys),
            "ok_count": ok_count,
            "failed_count": len(results) - ok_count,
            "results": results,
            "write_boundary": {
                "canonical_write": False,
                "formal_graph_write": False,
                "target": "knowledge_candidate_review",
                "scope": "selected_knowledge_candidates",
            },
        }
