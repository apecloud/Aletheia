import unittest

from agents.reasoning_finding_framework import (
    deep_graph_profile,
    entity_profile_aggregate_evidence,
    finding_canonical_boundary,
    paths_with_peer,
    plain_reasoning_conclusion,
    plain_reasoning_title,
    review_graph_scope_action,
    scoped_graph_finding,
    scope_limit_counter_evidence,
)


class ReasoningFindingFrameworkTest(unittest.TestCase):
    def test_deep_graph_profile_requires_full_source_relation_target_evidence_action_path(self):
        profile = deep_graph_profile(
            [
                {"kind": "source_entity", "value": "Entity A"},
                {"kind": "relation", "source_label": "Entity A", "target_label": "Entity B", "value": "depends_on"},
                {"kind": "target_entity", "value": "Entity B"},
                {"kind": "evidence", "metric": "risk_score", "value": 91},
                {"kind": "action", "value": "Review route"},
            ]
        )

        self.assertTrue(profile["multi_hop"])
        self.assertEqual(profile["reasoning_type"], "graph_multi_hop")
        self.assertEqual(profile["missing_steps"], [])

    def test_plain_reasoning_text_uses_paths_and_peers_without_metric_dump(self):
        question = "对象 A 的主要关联路径是什么"
        ranked_paths = [{"label": "Red Sea"}, {"label": "Gulf of Aden"}]
        second_hop_paths = [{"label": "Red Sea", "top_peers": [{"key": "CHN"}, {"key": "IRN"}]}]

        title = plain_reasoning_title(question, "Hormuz Crisis", ranked_paths)
        conclusion = plain_reasoning_conclusion(
            question,
            "Hormuz Crisis",
            "Metric dump: source_path(s) (#1/198)",
            ranked_paths,
            second_hop_paths,
            {"source_key_row_degree": 12},
        )

        self.assertIn("Hormuz Crisis", title)
        self.assertIn("Red Sea", conclusion)
        self.assertIn("CHN", conclusion)
        self.assertNotIn("source_path(s)", conclusion)
        self.assertNotIn("#1/198", conclusion)

    def test_review_scope_helpers_keep_reasoning_draft_only(self):
        action = review_graph_scope_action({"metrics": {"label": "A"}}, {"answer": {"title": "A"}})
        counter_evidence = scope_limit_counter_evidence(True)
        boundary = finding_canonical_boundary()

        self.assertEqual(action["type"], "review_graph_scope")
        self.assertEqual(action["execution_boundary"], "proposal_only")
        self.assertIn("structured_answer", action)
        self.assertEqual(counter_evidence[0]["kind"], "scope_limit")
        self.assertFalse(boundary["canonical_ontology_write"])
        self.assertFalse(boundary["graph_write"])

    def test_paths_with_peer_matches_case_insensitively(self):
        paths = [
            {"label": "Path A", "top_peers": [{"key": "chn"}]},
            {"label": "Path B", "top_peers": [{"key": "IRN"}]},
        ]

        self.assertEqual(paths_with_peer(paths, ["CHN"]), ["Path A"])

    def test_scoped_graph_finding_uses_shared_aggregate_and_review_boundary(self):
        evidence, summary = entity_profile_aggregate_evidence(
            "maritime-risk",
            "task:1",
            "Country:CHN",
            2,
            {
                "label": "China",
                "object_type": "Country",
                "source_key_profile": {
                    "related_tables": ["routes"],
                    "total_key_rows": 9,
                    "top_paths": [{"label": "Red Sea", "metric": "value", "metric_value": 1200}],
                },
            },
        )
        finding = scoped_graph_finding(
            "task:1",
            "China route profile",
            "China's exposure is concentrated in Red Sea.",
            [evidence],
            {"metrics": {"label": "China"}},
            {"answer": {"title": "China route profile"}},
            now_ms=123,
        )

        self.assertIn("Red Sea", summary)
        self.assertEqual(evidence["kind"], "controlled_aggregate")
        self.assertEqual(finding["canonical_key"], "finding:graph-scope:task:1:run-123")
        self.assertEqual(finding["recommended_action"]["execution_boundary"], "proposal_only")
        self.assertEqual(finding["counter_evidence"][0]["kind"], "scope_limit")


if __name__ == "__main__":
    unittest.main()
