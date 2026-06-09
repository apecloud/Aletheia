import unittest

from server.aletheia_server import InstanceRepository, ReasoningRepository


class DeepGraphReasoningTest(unittest.TestCase):
    def test_instance_repository_exposes_schema_reasoning_entity_adapters(self):
        self.assertTrue(callable(getattr(InstanceRepository, "_fetch_entity", None)))
        self.assertTrue(callable(getattr(InstanceRepository, "_entity_node", None)))

    def test_complete_source_relation_target_evidence_action_chain_is_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "source_entity", "source_ref": "source_table", "value": "Entity A"},
                {"kind": "relation", "source_label": "Entity A", "target_label": "Entity B", "value": "depends_on"},
                {"kind": "target_entity", "source_ref": "source_table", "value": "Entity B"},
                {"kind": "evidence", "source_ref": "source_table", "metric": "supporting_metric", "value": 123},
                {"kind": "action", "source_ref": "playbook", "metric": "review", "value": "Assign analyst review"},
            ]
        )

        self.assertTrue(profile["multi_hop"])
        self.assertEqual(profile["reasoning_type"], "graph_multi_hop")
        self.assertEqual(profile["finding_emphasis"], "deep_graph_finding")
        self.assertEqual(profile["hop_count"], 4)
        self.assertEqual(profile["missing_steps"], [])

    def test_metric_only_chain_is_not_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "aggregate", "source_ref": "source_table", "metric": "sum_value", "value": 123},
                {"kind": "action", "source_ref": "playbook", "metric": "review", "value": "Review ranking"},
            ]
        )

        self.assertFalse(profile["multi_hop"])
        self.assertEqual(profile["finding_emphasis"], "candidate_finding")
        self.assertIn("source_entity", profile["missing_steps"])
        self.assertIn("relation", profile["missing_steps"])
        self.assertIn("target_entity", profile["missing_steps"])

    def test_plain_reasoning_conclusion_is_human_summary_not_metric_dump(self):
        repo = object.__new__(ReasoningRepository)
        question = "对象 A 的主要关联路径是什么 — Entity A"
        ranked_paths = [
            {"label": "Path Alpha", "metric": "value", "metric_value": 1317},
            {"label": "Path Beta", "metric": "value", "metric_value": 936},
            {"label": "Path Gamma", "metric": "value", "metric_value": 609},
        ]
        second_hop_paths = [
            {"label": "Path Alpha", "top_peers": [{"key": "Peer A"}, {"key": "Peer B"}]},
            {"label": "Path Beta", "top_peers": [{"key": "Peer C"}, {"key": "Peer D"}]},
        ]

        title = repo._plain_reasoning_title(question, "Entity A", ranked_paths, second_hop_paths)
        conclusion = repo._plain_reasoning_conclusion(
            question,
            "Entity A",
            "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            ranked_paths,
            second_hop_paths,
            {"source_key_row_degree": 49},
        )

        self.assertIn("Entity A", title)
        self.assertIn("Path Alpha", conclusion)
        self.assertIn("Peer A", conclusion)
        self.assertNotIn("source_path(s)", conclusion)
        self.assertNotIn("#1/198", conclusion)

    def test_reasoning_response_builds_ranked_paths_from_graph_context_fallback(self):
        repo = object.__new__(ReasoningRepository)
        tenant = type("Tenant", (), {"tenant_id": "demo"})()
        task = {"question": "Entity A 的主要关联路径是什么 — Entity A", "canonical_key": "task"}
        scope = {"center_node": "Object:entity-a", "depth": 1, "node_limit": 200}
        structured_answer = {
            "title": "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            "profile_summary": "Entity A Business Profile: 25 source_path(s) (#1/198, high)",
            "metrics": {"label": "Entity A"},
        }
        graph_context = {
            "degree": {"source_key_row_degree": 49},
            "source_backed_related_nodes": [
                {"id": "SourcePath:Path Alpha", "label": "Path Alpha"},
                {"id": "SourcePath:Path Beta", "label": "Path Beta"},
            ],
            "source_backed_related_edges": [
                {"target": "SourcePath:Path Alpha", "metric": "value", "metric_value": 1317, "row_count": 1, "source_table": "source"},
                {"target": "SourcePath:Path Beta", "metric": "value", "metric_value": 936, "row_count": 1, "source_table": "source"},
            ],
        }

        response = repo._reasoning_response_v1(tenant, task, scope, structured_answer, [], graph_context)

        self.assertEqual([p["label"] for p in response["ranked_paths"][:2]], ["Path Alpha", "Path Beta"])
        self.assertIn("Path Alpha", response["answer"]["conclusion"])
        self.assertNotIn("source_path(s)", response["answer"]["conclusion"])


if __name__ == "__main__":
    unittest.main()
