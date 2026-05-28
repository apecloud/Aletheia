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


if __name__ == "__main__":
    unittest.main()
