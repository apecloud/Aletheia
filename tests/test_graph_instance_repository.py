#!/usr/bin/env python3
"""GraphInstanceRepository: the repo surface reasoning_engine.py needs,
backed directly by Nebula Graph -- no SQL, no SQLAlchemy engine.

Deterministic tests exercise the governance methods (reasoning_entity_config/
reasoning_link_config/_approved_artifacts, which don't need a live Nebula
connection) and the pure node-shaping helper (_entity_node). One additional
test round-trips real vertices/edges through a live local Nebula cluster and
is skipped (not failed) if it isn't reachable -- same convention as
LiveNebulaSmokeTest in test_hotpotqa_nebula_benchmark.py.

Run: python -m unittest tests.test_graph_instance_repository
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "scripts"))

from graph_instance_repository import GraphInstanceRepository  # noqa: E402
from relation_catalog import RelationCatalog  # noqa: E402


class GovernanceConfigTest(unittest.TestCase):
    def test_reasoning_entity_config_has_single_object_type_entry(self):
        repo = GraphInstanceRepository(space="unittest_space", object_type="hotpotentity")
        cfg = repo.reasoning_entity_config("any-tenant")
        self.assertEqual(set(cfg.keys()), {"hotpotentity"})
        self.assertEqual(cfg["hotpotentity"]["artifact"], "object:hotpotentity")

    def test_reasoning_link_config_fills_in_object_type_for_from_to(self):
        repo = GraphInstanceRepository(space="unittest_space", object_type="hotpotentity")
        repo._relation_catalog = RelationCatalog(entries={
            "born_in": {"description": "birthplace", "aliases": ["born_at"]},
        })
        config = repo.reasoning_link_config("any-tenant")
        self.assertEqual(config, [
            {"link": "born_in", "description": "birthplace", "from": "hotpotentity", "to": "hotpotentity"},
        ])

    def test_reasoning_link_config_empty_catalog_returns_empty_list(self):
        repo = GraphInstanceRepository(space="unittest_space")
        repo._relation_catalog = RelationCatalog(entries={})
        self.assertEqual(repo.reasoning_link_config("any-tenant"), [])

    def test_approved_artifacts_filters_to_known_keys_only(self):
        repo = GraphInstanceRepository(
            space="unittest_space",
            artifact_lookup={"object:hotpotentity": {"description": "A HotpotQA entity."}},
        )
        result = repo._approved_artifacts("any-tenant", ["object:hotpotentity", "object:unknown"])
        self.assertEqual(result, {"object:hotpotentity": {"description": "A HotpotQA entity."}})


class EntityNodeShapeTest(unittest.TestCase):
    def test_entity_node_carries_id_label_and_given_type(self):
        repo = GraphInstanceRepository(space="unittest_space")
        node = repo._entity_node("any-tenant", "hotpotentity", {"id": "q1:aly_raisman", "label": "Aly Raisman"})
        self.assertEqual(node, {"id": "q1:aly_raisman", "label": "Aly Raisman", "type": "hotpotentity"})


class LiveNebulaSmokeTest(unittest.TestCase):
    """Skipped (not failed) if the local Nebula cluster isn't reachable --
    this repo's Nebula containers are optional local infra, not a CI
    dependency (same convention as test_hotpotqa_nebula_benchmark.py)."""

    def test_fetch_entity_and_neighborhood_round_trip(self):
        try:
            from graph_db_client import NebulaGraphClient
        except ImportError:
            self.skipTest("nebula3-python not installed")

        space = "graph_repo_unittest"
        tag_name = "GraphRepoTestEntity"
        edge_type = "GRAPH_REPO_TEST_RELATION"

        client = NebulaGraphClient(ip="127.0.0.1", port=9669, user="root", password="nebula", space=space)
        try:
            client.connect()
        except Exception as exc:
            self.skipTest(f"Nebula not reachable: {exc}")

        try:
            import time

            client.execute_query(f"CREATE TAG IF NOT EXISTS {tag_name}(label string);")
            client.execute_query(f"CREATE EDGE IF NOT EXISTS {edge_type}(relation_label string);")
            time.sleep(11)

            client.insert_vertices(tag_name, [
                {"id": "t:alice", "label": "Alice"},
                {"id": "t:bob", "label": "Bob"},
            ])
            client.insert_edges(edge_type, [
                {"source_id": "t:alice", "target_id": "t:bob", "relation_label": "knows"},
            ])
            time.sleep(2)

            repo = GraphInstanceRepository(space=space, tag_name=tag_name, edge_type=edge_type)
            try:
                row = repo._fetch_entity("any-tenant", "entity", "t:alice")
                self.assertEqual(row, {"id": "t:alice", "label": "Alice"})

                graph = repo.neighborhood("any-tenant", "entity", "t:alice", depth=1, limit=20)
                self.assertTrue(graph["approved"])
                self.assertEqual(graph["center"]["label"], "Alice")
                labels_by_id = {n["id"]: n["label"] for n in graph["nodes"]}
                self.assertEqual(labels_by_id.get("t:bob"), "Bob")
                self.assertTrue(any(e["source"] == "t:alice" and e["target"] == "t:bob" for e in graph["edges"]))
            finally:
                repo.close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
