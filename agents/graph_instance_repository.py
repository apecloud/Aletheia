"""Graph-native InstanceRepository: backs ReasoningEngine.analyze() directly
against Nebula Graph, with no SQL and no SQLAlchemy engine ever handed to
reasoning_engine.py.

Implements exactly the repo surface reasoning_engine.py consumes --
``neighborhood``, ``_fetch_entity``, ``_entity_node``,
``reasoning_entity_config``, ``reasoning_link_config``,
``_approved_artifacts`` -- by porting the traversal/fetch logic already
validated (1000+ real HotpotQA questions, 90%+ hit rate) in
``scripts/run_hotpotqa_nebula_e2e_benchmark.py`` (``traverse``,
``gather_bidirect_edges``, ``fetch_label``) into a persistent, reusable
repository instead of a one-shot benchmark script.

Matches the single-TAG + single-EDGE-type model that pipeline validated:
one tenant's whole graph uses one vertex tag and one generic edge type
(with a ``relation_label`` property), so ``reasoning_entity_config`` only
ever has one entry -- unlike the old SQL side's "one table per entity
type, one join table per relation" explosion. Relation governance (WHICH
relations exist, deduplicated/described) lives in ``RelationCatalog``
(Postgres-backed), reused here for ``reasoning_link_config`` rather than
reinvented.
"""

from __future__ import annotations

from typing import Any

from graph_db_client import NebulaGraphClient
from relation_catalog import RelationCatalog


def _escape(value: str) -> str:
    return str(value).replace('"', "'")


def _value_as_str(value: Any) -> str:
    from nebula3.common.ttypes import Value

    if value.getType() != Value.SVAL:
        return ""
    return value.get_sVal().decode("utf-8")


class GraphInstanceRepository:
    """One instance per tenant's graph space.

    ``object_type`` is a single, fixed label for every vertex in this
    tenant's space (the model has no real per-vertex type distinction --
    that's what makes the single-TAG approach work at all). It becomes the
    ``object_type`` half of every ``center_node`` string
    (``f"{object_type}:{vertex_id}"``) and the ``type`` field on every node
    dict this repo returns.
    """

    def __init__(
        self,
        *,
        space: str,
        tag_name: str = "HotpotEntity",
        edge_type: str = "RELATION",
        object_type: str = "entity",
        nebula_ip: str = "127.0.0.1",
        nebula_port: int = 9669,
        nebula_user: str = "root",
        nebula_password: str = "nebula",
        relation_catalog_db_url: str | None = None,
        relation_catalog_scope: str | None = None,
        artifact_lookup: dict[str, dict[str, str]] | None = None,
    ):
        self.space = space
        self.tag_name = tag_name
        self.edge_type = edge_type
        self.object_type = object_type
        self._relation_catalog_db_url = relation_catalog_db_url
        self._relation_catalog_scope = relation_catalog_scope or space
        self._artifact_lookup = artifact_lookup or {}
        self._client = NebulaGraphClient(
            ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_password, space=space,
        )
        self._connected = False
        self._relation_catalog: RelationCatalog | None = None

    def _ensure_connected(self) -> None:
        if not self._connected:
            self._client.connect()
            self._connected = True

    def close(self) -> None:
        if self._connected:
            self._client.close()
            self._connected = False

    # ------------------------------------------------------------------
    # Governance: entity/link config, artifact descriptions
    # ------------------------------------------------------------------

    def reasoning_entity_config(self, tenant: str) -> dict[str, dict[str, Any]]:
        return {self.object_type: {"artifact": f"object:{self.object_type}"}}

    def reasoning_link_config(self, tenant: str) -> list[dict[str, str]]:
        """Governed relation vocabulary from ``RelationCatalog``, with
        ``from``/``to`` filled in as this tenant's one object type rather
        than the catalog's backend-agnostic ``"*"`` wildcard -- the catalog
        itself doesn't know entity types (it's shared vocabulary, not a
        schema), but ``reasoning_engine.py``'s own link-matching logic
        (``_plan_question_paths``, ``_gather_center_data``) needs concrete
        types to match against, and for a single-TAG-per-tenant graph that's
        always this one type."""
        if self._relation_catalog is None:
            self._relation_catalog = (
                RelationCatalog.load_from_postgres(self._relation_catalog_db_url, scope=self._relation_catalog_scope)
                if self._relation_catalog_db_url
                else RelationCatalog(entries={})
            )
        return [
            {**entry, "from": self.object_type, "to": self.object_type}
            for entry in self._relation_catalog.as_link_config()
        ]

    def _approved_artifacts(self, tenant: str, keys: list[str]) -> dict[str, dict[str, str]]:
        return {k: self._artifact_lookup[k] for k in keys if k in self._artifact_lookup}

    # ------------------------------------------------------------------
    # Vertex fetch
    # ------------------------------------------------------------------

    def _fetch_label(self, vid: str) -> str:
        """Empty string (not an exception) on any Nebula-side failure --
        connection unreachable, tag/space not provisioned yet, or the vertex
        genuinely doesn't exist all look the same to a caller: "nothing to
        show here." Callers already treat an empty label as "not found" and
        degrade accordingly (e.g. falling back to Postgres-backed ontology
        data), matching the defensive try/except pattern the retired SQL
        schema-introspection methods used."""
        try:
            self._ensure_connected()
            query = f'FETCH PROP ON `{self.tag_name}` "{_escape(vid)}" YIELD `{self.tag_name}`.label AS label;'
            result = self._client.execute_query(query)
        except Exception:
            return ""
        rows = result.rows()
        if not rows or not rows[0].values:
            return ""
        return _value_as_str(rows[0].values[0])

    def _fetch_entity(self, tenant: str, object_type: str, instance_id: str) -> dict[str, Any] | None:
        label = self._fetch_label(instance_id)
        if not label:
            return None
        return {"id": instance_id, "label": label}

    def _entity_node(self, tenant: str, object_type: str, row: dict[str, Any]) -> dict[str, Any]:
        return {"id": row.get("id", ""), "label": row.get("label", ""), "type": object_type}

    # ------------------------------------------------------------------
    # Neighborhood: BIDIRECT multi-hop traversal
    # ------------------------------------------------------------------

    def neighborhood(
        self, tenant: str, object_type: str, instance_id: str, *, depth: int, limit: int,
    ) -> dict[str, Any] | None:
        """Same BIDIRECT-plus-separate-fetch_label approach validated in
        ``run_hotpotqa_nebula_e2e_benchmark.traverse``/``gather_bidirect_edges``:
        a forward-only ``GO`` misses edges the extractor happened to anchor
        on the other entity as subject, and ``$$``/``$^`` inline properties
        are unreliable in BIDIRECT multi-step traversals (verified against
        the live cluster to bind to the query's traversal direction, not the
        edge's stored direction) -- so only ``src(edge)``/``dst(edge)`` are
        trusted, and each neighbor's real label is fetched separately."""
        center_label = self._fetch_label(instance_id)
        if not center_label:
            return None

        query = (
            f'GO 1 TO {depth} STEPS FROM "{_escape(instance_id)}" OVER `{self.edge_type}` BIDIRECT '
            f'YIELD DISTINCT src(edge) AS src, dst(edge) AS dst, `{self.edge_type}`.relation_label AS rel;'
        )
        try:
            result = self._client.execute_query(query)
        except Exception:
            return None

        edges: list[dict[str, str]] = []
        node_ids: set[str] = {instance_id}
        for row in result.rows():
            values = row.values
            src = _value_as_str(values[0])
            dst = _value_as_str(values[1])
            rel = _value_as_str(values[2])
            if not src or not dst:
                continue
            edges.append({"source": src, "target": dst, "label": rel})
            node_ids.add(src)
            node_ids.add(dst)
            if len(node_ids) >= limit:
                break

        nodes = [
            {"id": nid, "type": object_type, "label": center_label if nid == instance_id else self._fetch_label(nid)}
            for nid in node_ids
        ]

        return {
            "approved": True,
            "center": {"id": instance_id, "label": center_label, "type": object_type},
            "nodes": nodes,
            "edges": edges,
        }
