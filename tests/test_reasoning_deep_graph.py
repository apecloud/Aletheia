import unittest

from server.workbench_server import ReasoningRepository


class DeepGraphReasoningTest(unittest.TestCase):
    def test_complete_hazard_to_action_chain_is_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "hazard", "source_ref": "risk_indicators", "metric": "likelihood_conflict", "value": 0.67},
                {"kind": "chokepoint", "source_ref": "risk_indicators", "metric": "canal", "value": "Bab el-Mandeb Strait"},
                {"kind": "dependent_country", "source_ref": "systemic_results", "metric": "iso3", "value": "CHN"},
                {"kind": "risk_metric", "source_ref": "systemic_results", "metric": "trade_at_risk_v", "value": 15110427387.67},
                {"kind": "recommended_action", "source_ref": "playbook", "metric": "country_priority_review", "value": "Assign analyst review"},
            ]
        )

        self.assertTrue(profile["multi_hop"])
        self.assertEqual(profile["reasoning_type"], "graph_multi_hop")
        self.assertEqual(profile["finding_emphasis"], "deep_graph_finding")
        self.assertEqual(profile["hop_count"], 4)
        self.assertEqual(profile["missing_steps"], [])

    def test_volume_only_chain_is_not_deep_graph_finding(self):
        repo = object.__new__(ReasoningRepository)
        profile = repo._deep_graph_profile(
            [
                {"kind": "aggregate", "source_ref": "country_dependencies", "metric": "sum_v_canal", "value": 123},
                {"kind": "recommended_action", "source_ref": "playbook", "metric": "review", "value": "Review ranking"},
            ]
        )

        self.assertFalse(profile["multi_hop"])
        self.assertEqual(profile["finding_emphasis"], "candidate_finding")
        self.assertIn("hazard", profile["missing_steps"])
        self.assertIn("chokepoint", profile["missing_steps"])
        self.assertIn("dependent_country", profile["missing_steps"])

    def test_plain_reasoning_conclusion_is_human_summary_not_metric_dump(self):
        repo = object.__new__(ReasoningRepository)
        question = "中国对哪些海峡的风险最为敏感，会因为哪些海峡和美国产生冲突 — China (CHN)"
        ranked_paths = [
            {"label": "Taiwan Strait", "metric": "v_canal", "metric_value": 1317},
            {"label": "Malacca Strait", "metric": "v_canal", "metric_value": 936},
            {"label": "Korea Strait", "metric": "v_canal", "metric_value": 609},
        ]
        second_hop_paths = [
            {"label": "Taiwan Strait", "top_peers": [{"key": "JPN"}, {"key": "USA"}]},
            {"label": "Malacca Strait", "top_peers": [{"key": "IND"}, {"key": "USA"}]},
            {"label": "Korea Strait", "top_peers": [{"key": "KOR"}, {"key": "USA"}]},
        ]

        title = repo._plain_reasoning_title(question, "CHN", ranked_paths, second_hop_paths)
        conclusion = repo._plain_reasoning_conclusion(
            question,
            "CHN",
            "CHN Business Profile: 25 maritime_chokepoint(s) (#1/198, high)",
            ranked_paths,
            second_hop_paths,
            {"source_key_row_degree": 49},
        )

        self.assertIn("China (CHN)", title)
        self.assertIn("Taiwan Strait", conclusion)
        self.assertIn("USA", conclusion)
        self.assertNotIn("maritime_chokepoint(s)", conclusion)
        self.assertNotIn("#1/198", conclusion)

    def test_reasoning_response_builds_ranked_paths_from_graph_context_fallback(self):
        repo = object.__new__(ReasoningRepository)
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        task = {"question": "中国对哪些海峡最敏感 — China (CHN)", "canonical_key": "task"}
        scope = {"center_node": "Country:CHN", "depth": 1, "node_limit": 200}
        structured_answer = {
            "title": "CHN Business Profile: 25 maritime_chokepoint(s) (#1/198, high)",
            "profile_summary": "CHN Business Profile: 25 maritime_chokepoint(s) (#1/198, high)",
            "metrics": {"label": "CHN"},
        }
        graph_context = {
            "degree": {"source_key_row_degree": 49},
            "source_backed_related_nodes": [
                {"id": "MaritimeChokepoint:Taiwan Strait", "label": "Taiwan Strait"},
                {"id": "MaritimeChokepoint:Malacca Strait", "label": "Malacca Strait"},
            ],
            "source_backed_related_edges": [
                {"target": "MaritimeChokepoint:Taiwan Strait", "metric": "v_canal", "metric_value": 1317, "row_count": 1, "source_table": "source"},
                {"target": "MaritimeChokepoint:Malacca Strait", "metric": "v_canal", "metric_value": 936, "row_count": 1, "source_table": "source"},
            ],
        }

        response = repo._reasoning_response_v1(tenant, task, scope, structured_answer, [], graph_context)

        self.assertEqual([p["label"] for p in response["ranked_paths"][:2]], ["Taiwan Strait", "Malacca Strait"])
        self.assertIn("Taiwan Strait", response["answer"]["conclusion"])
        self.assertNotIn("maritime_chokepoint(s)", response["answer"]["conclusion"])


if __name__ == "__main__":
    unittest.main()
