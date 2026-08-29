#!/usr/bin/env python3
"""GraphInstanceRepository: the repo surface reasoning_engine.py needs,
backed directly by Nebula Graph -- no SQL, no SQLAlchemy engine.

Strongly-typed multi-TAG/multi-EDGE-type model: node/edge types come from
the tenant's approved ontology registry (agents/graph_ontology_registry.py)
and from Nebula itself (each vertex/edge's real tag/edge-type name), not a
single hardcoded tag/edge-type pair.

Deterministic tests exercise the governance methods (reasoning_entity_config/
reasoning_link_config/_approved_artifacts, mocking graph_ontology_registry so
no live Postgres is needed) and the pure node-shaping helper (_entity_node).
One additional test round-trips real vertices/edges through a live local
Nebula cluster and is skipped (not failed) if it isn't reachable -- same
convention as LiveNebulaSmokeTest in test_hotpotqa_nebula_benchmark.py.

Run: python -m unittest tests.test_graph_instance_repository
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from aletheia.graph_store.instance_repository import GraphInstanceRepository


class GovernanceConfigTest(unittest.TestCase):
    def test_reasoning_entity_config_returns_one_entry_per_approved_node_type(self):
        # Keys are lowercased to match reasoning_engine._gather_center_data's
        # `entity_config.get(object_type.lower())` lookup convention -- the
        # real, case-preserved type name is kept in "artifact"/"type_name".
        repo = GraphInstanceRepository(space="unittest_space", relation_catalog_db_url="sqlite:///:memory:")
        with patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_approved_node_types",
            return_value=[{"name": "Person"}, {"name": "Team"}],
        ), patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_all_node_types",
            return_value=[{"name": "Person"}, {"name": "Team"}],
        ):
            cfg = repo.reasoning_entity_config("any-tenant")
        self.assertEqual(set(cfg.keys()), {"person", "team"})
        self.assertEqual(cfg["person"]["artifact"], "object:Person")
        self.assertEqual(cfg["team"]["artifact"], "object:Team")
        self.assertEqual(cfg["person"]["type_name"], "Person")
        self.assertNotIn("resolves_via", cfg["person"])

    def test_reasoning_link_config_uses_real_domain_and_range(self):
        # "from"/"to" are lowercased to match reasoning_engine.py's mixed
        # convention (some comparisons there call .lower() on both sides;
        # others compare `lc["from"] == object_type.lower()` directly,
        # assuming "from"/"to" are already lowercase).
        repo = GraphInstanceRepository(space="unittest_space", relation_catalog_db_url="sqlite:///:memory:")
        with patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_approved_edge_types",
            return_value=[{"name": "HEAD_COACH", "description": "coach of", "domain": ["Team"], "range": ["Person"]}],
        ):
            config = repo.reasoning_link_config("any-tenant")
        self.assertEqual(config, [
            {"link": "HEAD_COACH", "description": "coach of", "from": "team", "to": "person"},
        ])

    def test_reasoning_entity_config_without_db_url_returns_empty(self):
        repo = GraphInstanceRepository(space="unittest_space")
        self.assertEqual(repo.reasoning_entity_config("any-tenant"), {})

    def test_reasoning_link_config_without_db_url_returns_empty(self):
        repo = GraphInstanceRepository(space="unittest_space")
        self.assertEqual(repo.reasoning_link_config("any-tenant"), [])

    def test_approved_artifacts_filters_to_known_keys_only(self):
        repo = GraphInstanceRepository(
            space="unittest_space",
            artifact_lookup={"object:hotpotentity": {"description": "A HotpotQA entity."}},
        )
        result = repo._approved_artifacts("any-tenant", ["object:hotpotentity", "object:unknown"])
        self.assertEqual(result, {"object:hotpotentity": {"description": "A HotpotQA entity."}})


class EntityNodeShapeTest(unittest.TestCase):
    def test_entity_node_falls_back_to_given_object_type(self):
        repo = GraphInstanceRepository(space="unittest_space")
        node = repo._entity_node("any-tenant", "hotpotentity", {"id": "q1:aly_raisman", "label": "Aly Raisman"})
        self.assertEqual(node, {"id": "q1:aly_raisman", "label": "Aly Raisman", "type": "hotpotentity"})

    def test_entity_node_prefers_real_type_from_row(self):
        repo = GraphInstanceRepository(space="unittest_space")
        node = repo._entity_node(
            "any-tenant", "entity", {"id": "q1:aly_raisman", "label": "Aly Raisman", "type": "Person"},
        )
        self.assertEqual(node, {"id": "q1:aly_raisman", "label": "Aly Raisman", "type": "Person"})


class LiveNebulaSmokeTest(unittest.TestCase):
    """Skipped (not failed) if the local Nebula cluster isn't reachable --
    this repo's Nebula containers are optional local infra, not a CI
    dependency (same convention as test_hotpotqa_nebula_benchmark.py)."""

    def test_fetch_entity_and_neighborhood_round_trip_across_two_tags(self):
        try:
            from aletheia.graph_store.nebula_client import NebulaGraphClient
        except ImportError:
            self.skipTest("nebula3-python not installed")

        space = "graph_repo_unittest_typed"
        client = NebulaGraphClient(ip="127.0.0.1", port=9669, user="root", password="nebula", space=space)
        try:
            client.connect()
        except Exception as exc:
            self.skipTest(f"Nebula not reachable: {exc}")

        try:
            import time

            client.execute_query("CREATE TAG IF NOT EXISTS GraphRepoTestPerson(label string);")
            client.execute_query("CREATE TAG IF NOT EXISTS GraphRepoTestTeam(label string);")
            client.execute_query("CREATE EDGE IF NOT EXISTS GRAPH_REPO_TEST_KNOWS();")
            time.sleep(11)

            client.insert_vertices("GraphRepoTestPerson", [{"id": "t:alice", "label": "Alice"}])
            client.insert_vertices("GraphRepoTestTeam", [{"id": "t:bob", "label": "Bob's Team"}])
            client.insert_edges("GRAPH_REPO_TEST_KNOWS", [{"source_id": "t:alice", "target_id": "t:bob"}])
            time.sleep(2)

            repo = GraphInstanceRepository(space=space)
            try:
                row = repo._fetch_entity("any-tenant", "entity", "t:alice")
                self.assertEqual(row, {"id": "t:alice", "label": "Alice", "type": "GraphRepoTestPerson"})

                graph = repo.neighborhood("any-tenant", "entity", "t:alice", depth=1, limit=20)
                self.assertTrue(graph["approved"])
                self.assertEqual(graph["center"]["label"], "Alice")
                self.assertEqual(graph["center"]["type"], "GraphRepoTestPerson")
                nodes_by_id = {n["id"]: n for n in graph["nodes"]}
                self.assertEqual(nodes_by_id["t:bob"]["label"], "Bob's Team")
                self.assertEqual(nodes_by_id["t:bob"]["type"], "GraphRepoTestTeam")
                self.assertTrue(any(
                    e["source"] == "t:alice" and e["target"] == "t:bob" and e["label"] == "GRAPH_REPO_TEST_KNOWS"
                    for e in graph["edges"]
                ))
            finally:
                repo.close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
