"""Graph-native InstanceRepository: backs ReasoningEngine.analyze() directly
against Nebula Graph, with no SQL and no SQLAlchemy engine ever handed to
reasoning_engine.py.

Implements exactly the repo surface reasoning_engine.py consumes --
``neighborhood``, ``_fetch_entity``, ``_entity_node``,
``reasoning_entity_config``, ``reasoning_link_config``,
``_approved_artifacts``.

Strongly-typed multi-TAG/multi-EDGE-type model: each tenant's approved
ontology (``agents/graph_ontology_registry.py``, backed by
``OntologyArtifact`` rows with the same draft->review->approved lifecycle
used elsewhere in this codebase) determines which Nebula TAGs/EDGE TYPEs
exist (synced via ``agents/graph_schema_sync.py``). A vertex's real type(s)
and an edge's real relation type come directly from Nebula itself
(``FETCH PROP ON *`` / ``GO ... OVER * YIELD edge``), not from a single
fixed tag/edge-type name configured per tenant -- replaces the earlier
single-TAG (``HotpotEntity``) + single-EDGE-type (``RELATION``) model.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from graph_db_client import NebulaGraphClient, dump_all_thread_stacks

logger = logging.getLogger("GraphInstanceRepository")

try:
    import graph_ontology_registry as ontology_registry
except ModuleNotFoundError:
    import agents.graph_ontology_registry as ontology_registry


def _escape(value: str) -> str:
    return str(value).replace('"', "'")


def _bytes_to_str(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _value_to_python(value: Any) -> Any:
    from nebula3.common.ttypes import Value

    vtype = value.getType()
    if vtype == Value.SVAL:
        return value.get_sVal().decode("utf-8")
    if vtype == Value.IVAL:
        return value.get_iVal()
    if vtype == Value.FVAL:
        return value.get_fVal()
    if vtype == Value.BVAL:
        return value.get_bVal()
    return None


def _vertex_from_value(value: Any) -> dict[str, Any] | None:
    from nebula3.common.ttypes import Value

    if value.getType() != Value.VVAL:
        return None
    vval = value.get_vVal()
    vid = _value_to_python(vval.vid)
    types: list[str] = []
    properties: dict[str, Any] = {}
    for tag in vval.tags:
        types.append(_bytes_to_str(tag.name))
        for prop_name, prop_value in (tag.props or {}).items():
            properties[_bytes_to_str(prop_name)] = _value_to_python(prop_value)
    label = properties.get("label") or (str(vid) if vid is not None else "")
    return {"id": str(vid) if vid is not None else "", "types": types, "label": str(label), "properties": properties}


def _edge_from_value(value: Any) -> dict[str, Any] | None:
    from nebula3.common.ttypes import Value

    if value.getType() != Value.EVAL:
        return None
    eval_ = value.get_eVal()
    properties: dict[str, Any] = {}
    for prop_name, prop_value in (eval_.props or {}).items():
        properties[_bytes_to_str(prop_name)] = _value_to_python(prop_value)
    return {
        "type": _bytes_to_str(eval_.name),
        "source": str(_value_to_python(eval_.src)),
        "target": str(_value_to_python(eval_.dst)),
        "properties": properties,
    }


class GraphInstanceRepository:
    """One instance per tenant's graph space. Node/edge types are discovered
    from the graph itself (via Nebula's cross-tag ``FETCH PROP ON *`` and
    ``GO ... OVER *``), not fixed per tenant -- a tenant can have any number
    of approved node/edge types, synced from ``graph_ontology_registry``."""

    def __init__(
        self,
        *,
        space: str,
        nebula_ip: str = "127.0.0.1",
        nebula_port: int = 9669,
        nebula_user: str = "root",
        nebula_password: str = "nebula",
        relation_catalog_db_url: str | None = None,
        relation_catalog_scope: str | None = None,
        artifact_lookup: dict[str, dict[str, str]] | None = None,
    ):
        self.space = space
        self._ontology_db_url = relation_catalog_db_url
        self._tenant_id = relation_catalog_scope or space
        self._artifact_lookup = artifact_lookup or {}
        self._client = NebulaGraphClient(
            ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_password, space=space,
        )
        self._connected = False
        self._connect_lock = threading.Lock()
        self._pg_session = None
        # Guards both lazy construction of self._pg_session AND every use of
        # it (query + rollback) in reasoning_entity_config/reasoning_link_config
        # -- like _connect_lock/NebulaGraphClient._lock, a single ORM Session
        # is not safe for concurrent use from multiple request threads
        # (observed directly: concurrent rollback() calls raised
        # sqlalchemy.exc.IllegalStateChangeError and crashed the request).
        self._pg_lock = threading.Lock()

    def _ensure_connected(self) -> None:
        # Guards the check-then-act race: two concurrent requests for a
        # tenant's first-ever query could otherwise both see _connected as
        # False and both call connect(), leaving two live pools/sessions
        # with the second silently replacing self._client.session out from
        # under the first request's in-flight query. Bounded acquire (see
        # NebulaGraphClient.execute_query's comment) so a stuck connect()
        # attempt can't freeze every other caller waiting on this lock too.
        if self._connected:
            return
        if not self._connect_lock.acquire(timeout=20):
            logger.error(dump_all_thread_stacks(f"connect() lock timeout on space {self.space!r}"))
            raise Exception(f"Timed out waiting for an in-progress connect() to {self.space!r}.")
        try:
            if not self._connected:
                self._client.connect()
                self._connected = True
        finally:
            self._connect_lock.release()

    def close(self) -> None:
        if self._connected:
            self._client.close()
            self._connected = False

    def _ensure_pg_session(self):
        if self._pg_session is not None:
            return self._pg_session
        if not self._ontology_db_url:
            return None
        if not self._pg_lock.acquire(timeout=20):
            logger.error(dump_all_thread_stacks("pg_lock timeout in _ensure_pg_session"))
            raise Exception("Timed out waiting for an in-progress Postgres session setup.")
        try:
            if self._pg_session is None:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker

                engine = create_engine(self._ontology_db_url)
                self._pg_session = sessionmaker(bind=engine)()
        finally:
            self._pg_lock.release()
        return self._pg_session

    # ------------------------------------------------------------------
    # Governance: entity/link config, artifact descriptions
    # ------------------------------------------------------------------

    def reasoning_entity_config(self, tenant: str) -> dict[str, dict[str, Any]]:
        session = self._ensure_pg_session()
        if session is None:
            return {}
        if not self._pg_lock.acquire(timeout=20):
            logger.error(dump_all_thread_stacks("pg_lock timeout in reasoning_entity_config"))
            raise Exception("Timed out waiting for the shared Postgres session (a prior query hasn't released it).")
        try:
            approved_types = ontology_registry.get_approved_node_types(session, self._tenant_id)
            approved_names = {node_type["name"] for node_type in approved_types}

            # subclass_of-aware: a type that isn't itself approved yet is still
            # reasoning-eligible if ANY ancestor along its subclass_of chain is
            # approved (e.g. draft "GuideDog" with subclass_of=["Dog"], "Dog"
            # already approved -- a GuideDog instance IS a Dog instance, so it
            # inherits Dog's approved-for-querying status). Needs every type
            # (draft + approved), not just the approved ones, to walk chains
            # that pass through not-yet-approved intermediate types.
            by_name = {node_type["name"]: node_type for node_type in ontology_registry.get_all_node_types(session, self._tenant_id)}
        finally:
            # This session is cached on self._pg_session for the tenant's
            # entire process lifetime (see _ensure_pg_session), not opened
            # fresh per call -- without an explicit rollback here, the ORM's
            # implicit per-query transaction is left open ("idle in
            # transaction" in pg_stat_activity) for as long as the server
            # runs. Observed directly: this leaked transaction eventually
            # got killed externally (a stale-connection sweep) and broke
            # every subsequent graph-page request for the tenant. Read-only
            # here, so rollback (not commit) correctly closes the
            # transaction without touching anything. self._pg_lock (held
            # for this whole block) is required too -- rollback() itself
            # isn't safe to call concurrently on one shared Session.
            session.rollback()
            self._pg_lock.release()

        def resolves_via(name: str, seen: frozenset[str] = frozenset()) -> str | None:
            """Name of the approved type `name` inherits eligibility from
            (itself, if it's directly approved), or None if neither `name`
            nor any subclass_of ancestor is approved. `seen` guards against
            a subclass_of cycle (an ontology-consistency bug elsewhere)
            turning into infinite recursion here."""
            if name in approved_names:
                return name
            if name in seen:
                return None
            node = by_name.get(name)
            for parent in (node or {}).get("subclass_of") or []:
                via = resolves_via(parent, seen | {name})
                if via:
                    return via
            return None

        # Keyed lowercase to match reasoning_engine._gather_center_data's
        # `entity_config.get(object_type.lower())` lookup convention (a
        # holdover from the old single-fixed-lowercase-type model) -- the
        # real, case-preserved type name lives in "artifact"/is what callers
        # should use as the actual Nebula TAG name.
        config: dict[str, dict[str, Any]] = {}
        for node_type in by_name.values():
            name = node_type["name"]
            via = resolves_via(name)
            if via is None:
                continue
            entry = {"artifact": f"object:{name}", "type_name": name}
            if via != name:
                entry["resolves_via"] = via
            config[name.lower()] = entry
        return config

    def search_instances(self, object_type: str, query: str, limit: int = 25) -> list[dict[str, Any]]:
        """Instances of `object_type` whose label/vid matches `query` (or the
        first `limit` when `query` is empty). `object_type` must already be
        governance-approved by the caller -- this doesn't re-check approval,
        it just samples/filters. Every item carries `instance_id == id` (the
        raw Nebula VID, verbatim, possibly containing colons of its own) so
        callers never need to guess/split it -- that convention is what lets
        InstanceRepository.default_center() stay source-agnostic."""
        try:
            self._ensure_connected()
            # Over-fetch: Nebula has no free-text index here, so filtering
            # by `query` happens client-side below.
            fetch_n = max(int(limit) * 4, 100) if query else int(limit)
            result = self._client.execute_query(f'MATCH (v:`{object_type}`) RETURN id(v) AS vid LIMIT {fetch_n};')
        except Exception:
            return []
        needle = str(query or "").strip().lower()
        instances: list[dict[str, Any]] = []
        for row in result.rows():
            vid = _bytes_to_str(row.values[0].get_sVal())
            vertex = self._fetch_vertex(vid)
            if vertex is None:
                continue
            label = vertex["label"]
            if needle and needle not in label.lower() and needle not in vid.lower():
                continue
            instances.append({
                "id": vid,
                "instance_id": vid,
                "type": vertex["types"][0] if vertex["types"] else object_type,
                "label": label,
                "projection_source": "GraphNativeInstance",
            })
            if len(instances) >= int(limit):
                break
        return instances

    def reasoning_link_config(self, tenant: str) -> list[dict[str, str]]:
        """Real domain/range per relation, from the approved typed registry
        -- replaces the old flat RelationCatalog-plus-forced-single-type
        hack now that relations carry their own real endpoint types."""
        session = self._ensure_pg_session()
        if session is None:
            return []
        if not self._pg_lock.acquire(timeout=20):
            logger.error(dump_all_thread_stacks("pg_lock timeout in reasoning_link_config"))
            raise Exception("Timed out waiting for the shared Postgres session (a prior query hasn't released it).")
        try:
            edge_types = ontology_registry.get_approved_edge_types(session, self._tenant_id)
        finally:
            session.rollback()  # see reasoning_entity_config's comment on why
            self._pg_lock.release()
        configs = []
        for edge_type in edge_types:
            domain = edge_type.get("domain") or ["*"]
            range_ = edge_type.get("range") or ["*"]
            configs.append({
                "link": edge_type["name"],
                "description": edge_type.get("description", ""),
                # Lowercased to match reasoning_engine.py's mixed convention
                # -- most comparisons there call .lower() on both sides, but
                # a few (_gather_center_data's desc_keys, analyze()'s
                # selected_link_keys, _resolve_self_refs) compare
                # `lc["from"] == object_type.lower()` directly, assuming
                # "from"/"to" are already lowercase (a holdover from the
                # SQL repo's lowercase table-name convention).
                "from": domain[0].lower(),
                "to": range_[0].lower(),
            })
        return configs

    def _approved_artifacts(self, tenant: str, keys: list[str]) -> dict[str, dict[str, str]]:
        return {k: self._artifact_lookup[k] for k in keys if k in self._artifact_lookup}

    # ------------------------------------------------------------------
    # Vertex fetch
    # ------------------------------------------------------------------

    def _fetch_vertex(self, vid: str) -> dict[str, Any] | None:
        """Full typed record for a vertex (all tags + properties), or None
        on any Nebula-side failure (connection unreachable, tag/space not
        provisioned yet, or the vertex genuinely doesn't exist -- all look
        the same to a caller: "nothing to show here")."""
        try:
            self._ensure_connected()
            query = f'FETCH PROP ON * "{_escape(vid)}" YIELD vertex AS v;'
            result = self._client.execute_query(query)
        except Exception:
            return None
        rows = result.rows()
        if not rows or not rows[0].values:
            return None
        return _vertex_from_value(rows[0].values[0])

    def _fetch_label(self, vid: str) -> str:
        vertex = self._fetch_vertex(vid)
        return vertex["label"] if vertex else ""

    def _fetch_entity(self, tenant: str, object_type: str, instance_id: str) -> dict[str, Any] | None:
        vertex = self._fetch_vertex(instance_id)
        if vertex is None:
            return None
        return {
            "id": vertex["id"],
            "label": vertex["label"],
            "type": vertex["types"][0] if vertex["types"] else object_type,
        }

    def _entity_node(self, tenant: str, object_type: str, row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row.get("id", ""), "label": row.get("label", ""), "type": row.get("type", object_type)}

    # ------------------------------------------------------------------
    # Neighborhood: BIDIRECT multi-hop traversal over all edge types
    # ------------------------------------------------------------------

    def neighborhood(
        self, tenant: str, object_type: str, instance_id: str, *, depth: int, limit: int,
    ) -> dict[str, Any] | None:
        """Traverses every edge type (``OVER *``), reading each edge's real
        relation type off the edge value itself (``edge.name``) instead of a
        single hardcoded edge type + generic ``relation_label`` property --
        except for tenants still on the old flat model (one shared "RELATION"
        Nebula edge type for every relation, e.g. WebQSP, not yet migrated to
        the typed model), where ``edge.name`` is always that same literal
        string and the real semantic relation lives in the
        ``relation_label`` property instead. Preferring that property when
        present keeps both models correct without per-tenant config."""
        center = self._fetch_vertex(instance_id)
        if center is None:
            return None
        center_type = center["types"][0] if center["types"] else object_type

        query = (
            f'GO 1 TO {depth} STEPS FROM "{_escape(instance_id)}" OVER * BIDIRECT '
            f'YIELD DISTINCT edge AS e;'
        )
        try:
            result = self._client.execute_query(query)
        except Exception:
            return None

        edges: list[dict[str, Any]] = []
        node_ids: set[str] = {instance_id}
        for row in result.rows():
            edge = _edge_from_value(row.values[0])
            if edge is None or not edge["source"] or not edge["target"]:
                continue
            edges.append({
                "source": edge["source"],
                "target": edge["target"],
                "label": edge["properties"].get("relation_label") or edge["type"],
                "properties": edge["properties"],
            })
            node_ids.add(edge["source"])
            node_ids.add(edge["target"])
            if len(node_ids) >= limit:
                break

        nodes: list[dict[str, Any]] = []
        for nid in node_ids:
            if nid == instance_id:
                nodes.append({"id": nid, "type": center_type, "label": center["label"]})
                continue
            neighbor = self._fetch_vertex(nid)
            if neighbor is None:
                nodes.append({"id": nid, "type": object_type, "label": ""})
            else:
                nodes.append({
                    "id": nid,
                    "type": neighbor["types"][0] if neighbor["types"] else object_type,
                    "label": neighbor["label"],
                })

        return {
            "approved": True,
            "center": {"id": instance_id, "label": center["label"], "type": center_type},
            "nodes": nodes,
            "edges": edges,
        }

    # ------------------------------------------------------------------
    # Full graph: sampled projection across every approved type, for the
    # Graph Explorer screen's default "all approved nodes" view.
    # ------------------------------------------------------------------

    def full_graph(
        self, entity_config: dict[str, dict[str, Any]], *, node_limit: int = 200, edge_limit: int = 600,
    ) -> dict[str, Any] | None:
        """Sample up to `node_limit` vertices spread across every approved
        type, then pull the edges among that sampled set. Not a full graph
        dump (a real tenant can have thousands of nodes) -- a representative
        cross-section, same spirit as the SQL-schema pipeline's
        `full_graph`/`_ontology_concrete_object_nodes` this mirrors for
        graph-native tenants."""
        type_names: list[str] = []
        seen_types: set[str] = set()
        for entry in entity_config.values():
            name = entry.get("type_name")
            if name and name not in seen_types:
                seen_types.add(name)
                type_names.append(name)
        if not type_names:
            return None

        self._ensure_connected()
        per_type = max(1, node_limit // len(type_names))
        vids: list[str] = []
        seen_vids: set[str] = set()
        for type_name in type_names:
            if len(vids) >= node_limit:
                break
            try:
                result = self._client.execute_query(
                    f'MATCH (v:`{type_name}`) RETURN id(v) AS vid LIMIT {per_type};'
                )
            except Exception:
                continue
            for row in result.rows():
                vid = _bytes_to_str(row.values[0].get_sVal())
                if vid not in seen_vids:
                    seen_vids.add(vid)
                    vids.append(vid)
        if not vids:
            return None

        nodes: list[dict[str, Any]] = []
        for vid in vids:
            vertex = self._fetch_vertex(vid)
            if vertex is None:
                continue
            nodes.append({
                "id": vertex["id"],
                "type": vertex["types"][0] if vertex["types"] else "",
                "label": vertex["label"],
            })

        id_list = ",".join(f'"{_escape(vid)}"' for vid in vids)
        edges: list[dict[str, Any]] = []
        try:
            result = self._client.execute_query(
                f'GO 1 TO 1 STEPS FROM {id_list} OVER * BIDIRECT YIELD DISTINCT edge AS e;'
            )
            for row in result.rows():
                edge = _edge_from_value(row.values[0])
                if edge is None or not edge["source"] or not edge["target"]:
                    continue
                if edge["source"] not in seen_vids or edge["target"] not in seen_vids:
                    continue
                edges.append({
                    "source": edge["source"],
                    "target": edge["target"],
                    "label": edge["properties"].get("relation_label") or edge["type"],
                    "properties": edge["properties"],
                })
                if len(edges) >= edge_limit:
                    break
        except Exception:
            pass

        return {"approved": True, "center": None, "nodes": nodes, "edges": edges}
