import unittest

from aletheia.interfaces.api.repositories.instance import InstanceRepository


class FakeTenant:
    tenant_id = "demo"
    graph_database = "demo_kg"

    def public_dict(self):
        return {"tenant_id": self.tenant_id}


def _repo_with_graph(graph):
    """InstanceRepository.graph_leiden_communities/graph_centrality_ranking
    both call self.full_graph(tenant, limit=...) -- monkeypatch that one
    method directly on the instance (same pattern tests/test_reasoning_deep_
    graph.py uses for other InstanceRepository methods), so no real
    tenant_registry/Postgres/Nebula connection is needed."""
    repo = InstanceRepository.__new__(InstanceRepository)
    repo.full_graph = lambda *args, **kwargs: graph
    return repo


# Two disconnected triangles (A-B-C and D-E-F) plus one bridge edge C-D --
# a deterministic, easy-to-reason-about fixture: Leiden should find (at
# least) two communities, and C/D (the bridge endpoints) should score
# highest on betweenness centrality since every A/B/D/E/F cross-cluster
# path runs through them.
TWO_TRIANGLES_GRAPH = {
    "approved": True,
    "nodes": [{"id": n} for n in ("A", "B", "C", "D", "E", "F")],
    "edges": [
        {"source": "A", "target": "B"}, {"source": "B", "target": "C"}, {"source": "A", "target": "C"},
        {"source": "D", "target": "E"}, {"source": "E", "target": "F"}, {"source": "D", "target": "F"},
        {"source": "C", "target": "D"},
    ],
}


class GraphLeidenCommunitiesTest(unittest.TestCase):
    def test_not_approved_graph_returns_empty_result(self):
        repo = _repo_with_graph({"approved": False})

        result = repo.graph_leiden_communities(FakeTenant())

        self.assertFalse(result["approved"])
        self.assertEqual(result["communities"], {})
        self.assertEqual(result["community_count"], 0)

    def test_two_triangles_bridged_by_one_edge_split_into_two_communities(self):
        repo = _repo_with_graph(TWO_TRIANGLES_GRAPH)

        result = repo.graph_leiden_communities(FakeTenant(), resolution=1.0)

        self.assertTrue(result["approved"])
        self.assertEqual(result["community_count"], 2)
        # each triangle's 3 nodes land in the same community as each other
        self.assertEqual(result["communities"]["A"], result["communities"]["B"])
        self.assertEqual(result["communities"]["B"], result["communities"]["C"])
        self.assertEqual(result["communities"]["D"], result["communities"]["E"])
        self.assertEqual(result["communities"]["E"], result["communities"]["F"])
        self.assertNotEqual(result["communities"]["A"], result["communities"]["D"])
        self.assertGreater(result["modularity"], 0)

    def test_same_graph_and_seed_partitions_deterministically(self):
        repo = _repo_with_graph(TWO_TRIANGLES_GRAPH)

        first = repo.graph_leiden_communities(FakeTenant())
        second = repo.graph_leiden_communities(FakeTenant())

        self.assertEqual(first["communities"], second["communities"])


class GraphCentralityRankingTest(unittest.TestCase):
    def test_not_approved_graph_returns_empty_ranking(self):
        repo = _repo_with_graph({"approved": False})

        result = repo.graph_centrality_ranking(FakeTenant())

        self.assertFalse(result["approved"])
        self.assertEqual(result["ranking"], [])

    def test_bridge_nodes_rank_highest_on_betweenness(self):
        repo = _repo_with_graph(TWO_TRIANGLES_GRAPH)

        result = repo.graph_centrality_ranking(FakeTenant(), method="betweenness", top_n=6)

        self.assertTrue(result["approved"])
        top_two_ids = {result["ranking"][0]["id"], result["ranking"][1]["id"]}
        self.assertEqual(top_two_ids, {"C", "D"})

    def test_top_n_truncates_ranking(self):
        repo = _repo_with_graph(TWO_TRIANGLES_GRAPH)

        result = repo.graph_centrality_ranking(FakeTenant(), method="degree", top_n=2)

        self.assertEqual(len(result["ranking"]), 2)

    def test_invalid_method_raises(self):
        repo = _repo_with_graph(TWO_TRIANGLES_GRAPH)

        with self.assertRaises(ValueError):
            repo.graph_centrality_ranking(FakeTenant(), method="not_a_real_method")


if __name__ == "__main__":
    unittest.main()
