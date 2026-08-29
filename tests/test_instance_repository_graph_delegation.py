#!/usr/bin/env python3
"""InstanceRepository delegates to GraphInstanceRepository unconditionally --
the SQL retrieval core has been retired entirely, so every tenant's
`_fetch_entity`/`_entity_node`/`reasoning_entity_config`/`reasoning_link_config`/
`neighborhood` calls go straight to Nebula via `_graph_repo_for(tenant)`. One
live test round-trips real vertices/edges (across two Nebula TAGs, per the
strongly-typed multi-TAG/multi-EDGE-type model) through a local Nebula
cluster and is skipped (not failed) if it isn't reachable -- same convention
as LiveNebulaSmokeTest elsewhere in this repo.

Run: python -m unittest tests.test_instance_repository_graph_delegation
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from aletheia.core.tenant_registry import TenantConfig, TenantRegistry
from aletheia.interfaces.api.server import InstanceRepository


def _graph_tenant(space: str) -> TenantConfig:
    return TenantConfig(
        tenant_id="graph-delegation-unittest",
        namespace="graph_delegation_unittest",
        display_name="Graph Delegation Unittest",
        graph_database=space,
        metadata_db_url="postgresql+psycopg2://unused:unused@127.0.0.1:5432/unused",
        source_db_url="mysql+pymysql://unused:unused@127.0.0.1:3306/unused",
    )


class GovernanceDelegationTest(unittest.TestCase):
    """No live Nebula/Postgres needed -- graph_ontology_registry is mocked."""

    def test_reasoning_entity_config_delegates_to_graph_repo(self):
        tenant = _graph_tenant("unused_space")
        repo = InstanceRepository(TenantRegistry([tenant]))
        with patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_approved_node_types",
            return_value=[{"name": "Person"}],
        ), patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_all_node_types",
            return_value=[{"name": "Person"}],
        ):
            config = repo.reasoning_entity_config(tenant)
        self.assertEqual(set(config.keys()), {"person"})

    def test_every_tenant_delegates_and_caches_one_graph_repo(self):
        tenant = _graph_tenant("unused_space")
        repo = InstanceRepository(TenantRegistry([tenant]))
        self.assertNotIn(tenant.tenant_id, repo._graph_repos)
        with patch("aletheia.graph_store.instance_repository.ontology_registry.get_approved_node_types", return_value=[]), patch(
            "aletheia.graph_store.instance_repository.ontology_registry.get_all_node_types", return_value=[],
        ):
            repo.reasoning_entity_config(tenant)
        self.assertIn(tenant.tenant_id, repo._graph_repos)


class LiveNebulaDelegationTest(unittest.TestCase):
    """Skipped (not failed) if the local Nebula cluster isn't reachable."""

    def test_fetch_entity_and_neighborhood_delegate_through_instance_repository(self):
        try:
            from aletheia.graph_store.nebula_client import NebulaGraphClient
        except ImportError:
            self.skipTest("nebula3-python not installed")

        space = "instance_repo_delegation_unittest_typed"
        client = NebulaGraphClient(ip="127.0.0.1", port=9669, user="root", password="nebula", space=space)
        try:
            client.connect()
        except Exception as exc:
            self.skipTest(f"Nebula not reachable: {exc}")

        try:
            import time

            client.execute_query("CREATE TAG IF NOT EXISTS DelegationTestPerson(label string);")
            client.execute_query("CREATE EDGE IF NOT EXISTS DELEGATION_TEST_KNOWS();")
            time.sleep(11)
            client.insert_vertices("DelegationTestPerson", [
                {"id": "t:alice", "label": "Alice"},
                {"id": "t:bob", "label": "Bob"},
            ])
            client.insert_edges("DELEGATION_TEST_KNOWS", [{"source_id": "t:alice", "target_id": "t:bob"}])
            time.sleep(2)

            tenant = _graph_tenant(space)
            repo = InstanceRepository(TenantRegistry([tenant]))
            try:
                row = repo._fetch_entity(tenant, "entity", "t:alice")
                self.assertEqual(row, {"id": "t:alice", "label": "Alice", "type": "DelegationTestPerson"})

                graph = repo.neighborhood(tenant, "entity", "t:alice", depth=1, limit=20)
                self.assertTrue(graph["approved"])
                labels_by_id = {n["id"]: n["label"] for n in graph["nodes"]}
                self.assertEqual(labels_by_id.get("t:bob"), "Bob")
            finally:
                repo._graph_repos[tenant.tenant_id].close()
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
