#!/usr/bin/env python3
"""GraphInstanceRepository.reasoning_entity_config: subclass_of-aware type
resolution -- the "ontology-aware reasoning" pipeline stage.

A node type that isn't itself approved is still reasoning-eligible if some
ancestor along its subclass_of chain is approved (a GuideDog instance IS a
Dog instance, so it inherits Dog's approved-for-querying status even before
GuideDog itself gets reviewed). Mirrors test_graph_instance_repository.py's
own mocking pattern (mocks agents.graph_ontology_registry, no live Postgres).

Run: python -m unittest tests.test_reasoning_subclass_of
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from graph_instance_repository import GraphInstanceRepository

# Animal -> Dog -> GuideDog: only Animal and Dog are approved. GuideDog is
# still a draft (never independently reviewed).
_HIERARCHY = [
    {"name": "Animal", "subclass_of": []},
    {"name": "Dog", "subclass_of": ["Animal"]},
    {"name": "GuideDog", "subclass_of": ["Dog"]},
]
_APPROVED = [{"name": "Animal", "subclass_of": []}, {"name": "Dog", "subclass_of": ["Animal"]}]


def _repo() -> GraphInstanceRepository:
    return GraphInstanceRepository(space="unittest_space", relation_catalog_db_url="sqlite:///:memory:")


class SubclassResolutionTest(unittest.TestCase):
    def test_grandchild_of_approved_type_resolves_via_nearest_approved_ancestor(self):
        repo = _repo()
        with patch(
            "graph_instance_repository.ontology_registry.get_approved_node_types", return_value=_APPROVED,
        ), patch(
            "graph_instance_repository.ontology_registry.get_all_node_types", return_value=_HIERARCHY,
        ):
            cfg = repo.reasoning_entity_config("any-tenant")

        self.assertIn("guidedog", cfg)
        self.assertEqual(cfg["guidedog"]["type_name"], "GuideDog")
        self.assertEqual(cfg["guidedog"]["artifact"], "object:GuideDog")
        self.assertEqual(cfg["guidedog"]["resolves_via"], "Dog")

    def test_directly_approved_type_has_no_resolves_via(self):
        repo = _repo()
        with patch(
            "graph_instance_repository.ontology_registry.get_approved_node_types", return_value=_APPROVED,
        ), patch(
            "graph_instance_repository.ontology_registry.get_all_node_types", return_value=_HIERARCHY,
        ):
            cfg = repo.reasoning_entity_config("any-tenant")

        self.assertIn("dog", cfg)
        self.assertNotIn("resolves_via", cfg["dog"])

    def test_draft_type_with_no_approved_ancestor_is_excluded(self):
        """A draft type whose subclass_of chain never reaches an approved
        type must stay invisible -- same as today's plain "not approved"
        exclusion, just walked one level further."""
        repo = _repo()
        hierarchy = [
            {"name": "Animal", "subclass_of": []},
            {"name": "Rock", "subclass_of": []},  # unrelated, never approved
        ]
        with patch(
            "graph_instance_repository.ontology_registry.get_approved_node_types",
            return_value=[{"name": "Animal", "subclass_of": []}],
        ), patch(
            "graph_instance_repository.ontology_registry.get_all_node_types", return_value=hierarchy,
        ):
            cfg = repo.reasoning_entity_config("any-tenant")

        self.assertIn("animal", cfg)
        self.assertNotIn("rock", cfg)

    def test_subclass_of_cycle_does_not_infinite_loop(self):
        """An ontology-consistency bug elsewhere (a subclass_of cycle) must
        degrade to "not resolvable" here, not hang or crash."""
        repo = _repo()
        cyclic = [
            {"name": "A", "subclass_of": ["B"]},
            {"name": "B", "subclass_of": ["A"]},
        ]
        with patch(
            "graph_instance_repository.ontology_registry.get_approved_node_types", return_value=[],
        ), patch(
            "graph_instance_repository.ontology_registry.get_all_node_types", return_value=cyclic,
        ):
            cfg = repo.reasoning_entity_config("any-tenant")

        self.assertEqual(cfg, {})

    def test_missing_subclass_of_key_treated_as_no_parents(self):
        """Existing HotpotQA/WebQSP tenants never populate subclass_of at
        all -- a type dict with no "subclass_of" key must behave exactly
        like an empty list, not raise."""
        repo = _repo()
        with patch(
            "graph_instance_repository.ontology_registry.get_approved_node_types",
            return_value=[{"name": "Person"}],
        ), patch(
            "graph_instance_repository.ontology_registry.get_all_node_types",
            return_value=[{"name": "Person"}, {"name": "Organization"}],
        ):
            cfg = repo.reasoning_entity_config("any-tenant")

        self.assertEqual(set(cfg.keys()), {"person"})


if __name__ == "__main__":
    unittest.main()
