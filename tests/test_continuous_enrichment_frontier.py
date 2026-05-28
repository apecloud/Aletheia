import unittest
from datetime import datetime

from server.workbench_server import InstanceRepository


class ContinuousEnrichmentFrontierTest(unittest.TestCase):
    def _repo_with_candidates(self, candidates):
        repo = object.__new__(InstanceRepository)
        repo._continuous_frontier_candidates = lambda tenant, stored, config: candidates
        return repo

    def test_priority_order_prefers_new_graph_then_question_then_finding_then_coverage(self):
        candidates = [
            {"key": "coverage:country:chn", "name": "CHN", "source_kind": "graph_coverage"},
            {"key": "finding:bab", "name": "Bab finding", "source_kind": "reasoning_finding_seed"},
            {"key": "question:hormuz", "name": "Hormuz question", "source_kind": "user_question_scope"},
            {"key": "proposed-graph:edge:chn-bab", "name": "CHN depends on Bab", "source_kind": "new_graph_edge"},
            {"key": "proposed-graph:node:hormuz", "name": "Hormuz Strait", "source_kind": "new_graph_node"},
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(None, [], {"frontier_state": {}, "frontier_cooldown_minutes": 360}, 5)
        self.assertEqual([item["source_kind"] for item in selected], [
            "new_graph_edge",
            "new_graph_node",
            "user_question_scope",
            "reasoning_finding_seed",
            "graph_coverage",
        ])
        self.assertTrue(all(item.get("reason") for item in selected))
        self.assertTrue(all("priority" in item for item in selected))

    def test_cooldown_falls_back_to_graph_coverage_without_restarting_static_template(self):
        now = datetime.utcnow().isoformat()
        candidates = [
            {"key": "proposed-graph:node:recent", "name": "Recent node", "source_kind": "new_graph_node"},
            {"key": "coverage:rotating", "name": "Coverage fallback", "source_kind": "graph_coverage"},
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(
            None,
            [],
            {
                "frontier_state": {
                    "last_enriched_at": {
                        "proposed-graph:node:recent": now,
                        "coverage:rotating": now,
                    }
                },
                "frontier_cooldown_minutes": 360,
            },
            2,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_kind"], "graph_coverage")
        self.assertEqual(selected[0]["key"], "coverage:rotating")

    def test_new_proposed_graph_outputs_become_priority_frontier_items(self):
        repo = object.__new__(InstanceRepository)
        result = {
            "run": {"run_key": "iterative-graph:task238"},
            "proposed_graph": [
                {
                    "element_type": "edge",
                    "element_key": "proposed-graph:edge:chn-bab",
                    "name": "CHN depends on Bab el-Mandeb Strait",
                    "confidence": 0.82,
                    "iteration": 1,
                    "payload": {
                        "source_label": "CHN",
                        "target_label": "Bab el-Mandeb Strait",
                        "relation": "depends_on",
                        "metrics": ["trade_at_risk_v"],
                    },
                    "evidence_refs": ["source:zenodo"],
                    "source_url": "https://zenodo.org/records/13841882",
                }
            ],
        }

        next_frontier, additions = repo._continuous_next_frontier([], result, {"visited_frontier_keys": []})

        self.assertEqual(additions[0]["source_kind"], "new_graph_edge")
        self.assertEqual(additions[0]["priority"], 100.0)
        self.assertIn("new proposed graph edge", additions[0]["reason"])
        self.assertEqual(next_frontier[0]["payload"]["relation"], "depends_on")


if __name__ == "__main__":
    unittest.main()
