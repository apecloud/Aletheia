import unittest
from datetime import datetime

from server.workbench_server import InstanceRepository, _dedup_audit_from_payload


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

    def test_runtime_reasoning_configs_do_not_fallback_to_demo_fixtures(self):
        repo = object.__new__(InstanceRepository)
        repo._schema_graph_reasoning_configs = lambda tenant: (None, None)
        for tenant_id in ("default", "northwind-sandbox", "creditcardfraud", "maritime-risk"):
            tenant = type("Tenant", (), {"tenant_id": tenant_id})()
            self.assertEqual(repo.reasoning_entity_config(tenant), {})
            self.assertEqual(repo.reasoning_link_config(tenant), [])

    def test_dedup_audit_preserves_merge_boundary_false(self):
        audit = _dedup_audit_from_payload(
            {
                "candidate_id": "candidate:country:chn",
                "task_id": "task-1",
                "run_id": "run-1",
                "frontier_id": "frontier-1",
                "dedup_decision": "needs_review",
                "matched_node_key": "Country:CHN",
                "match_score": 0.91,
                "match_evidence": [{"field": "label", "reason": "same normalized name"}],
                "conflict_fields": ["source_url"],
                "decision_reason": "High identity match with conflicting source evidence",
                "source_fingerprint": "source123",
                "evidence_fingerprint": "evidence123",
                "llm_merge_decision_allowed": False,
                "empty_field": "",
            }
        )

        self.assertEqual(audit["candidate_id"], "candidate:country:chn")
        self.assertEqual(audit["dedup_decision"], "needs_review")
        self.assertEqual(audit["matched_node_key"], "Country:CHN")
        self.assertEqual(audit["match_score"], 0.91)
        self.assertEqual(audit["conflict_fields"], ["source_url"])
        self.assertIs(audit["llm_merge_decision_allowed"], False)
        self.assertNotIn("empty_field", audit)

    def test_budget_config_caps_effective_cycle_limits(self):
        repo = object.__new__(InstanceRepository)
        config = repo._continuous_update_config(
            {
                "max_frontier": 10,
                "max_results_per_query": 10,
                "max_iterations": 3,
                "budget": {
                    "max_frontier_per_cycle": 4,
                    "max_results_per_query": 3,
                    "max_iterations_per_cycle": 2,
                    "max_cycles": 5,
                },
            },
            {"budget": {"max_frontier_per_cycle": 2, "max_results_per_query": 1, "max_iterations_per_cycle": 1}},
        )
        budget = repo._continuous_budget(config)

        self.assertEqual(budget["max_frontier_per_cycle"], 2)
        self.assertEqual(budget["max_results_per_query"], 1)
        self.assertEqual(budget["max_iterations_per_cycle"], 1)
        self.assertEqual(budget["max_cycles"], 5)

    def test_source_trust_rejects_unlisted_domains_with_event_payload(self):
        repo = object.__new__(InstanceRepository)
        config = {
            "source_trust": {
                "allowed_domains": ["zenodo.org"],
                "reject_unlisted_domains": True,
            }
        }
        trusted, skipped, events = repo._continuous_trusted_search_results(
            [
                {"title": "trusted", "url": "https://zenodo.org/records/1"},
                {"title": "untrusted", "url": "https://example.org/claim"},
            ],
            config,
        )

        self.assertEqual(len(trusted), 1)
        self.assertEqual(trusted[0]["source_trust"]["domain"], "zenodo.org")
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["type"], "source_trust_rejected")
        self.assertEqual(events[0]["reason"], "domain not in allowed source trust policy")

    def test_allowlist_updates_source_trust_policy(self):
        repo = object.__new__(InstanceRepository)
        config = repo._continuous_update_config(
            {"allowed_domains": ["zenodo.org"], "source_trust": {"allowed_domains": ["zenodo.org"]}},
            {"allowlist": "example.com"},
        )
        decision = repo._continuous_source_trust_decision({"url": "https://example.com/source"}, config)

        self.assertTrue(decision["trusted"])
        self.assertEqual(config["source_trust"]["allowed_domains"], ["example.com"])

    def test_backoff_schedules_exponential_delay_and_blocks_until_due(self):
        repo = object.__new__(InstanceRepository)
        config, first = repo._continuous_schedule_backoff(
            {"backoff": {"failure_count": 0, "base_seconds": 30, "max_seconds": 120}},
            RuntimeError("network timeout"),
        )
        self.assertEqual(first["failure_count"], 1)
        self.assertEqual(first["delay_seconds"], 30)
        self.assertIsNotNone(repo._continuous_backoff_active(config))

        config, second = repo._continuous_schedule_backoff(config, RuntimeError("network timeout again"))
        self.assertEqual(second["failure_count"], 2)
        self.assertEqual(second["delay_seconds"], 60)

        cleared = repo._continuous_clear_backoff(config)
        self.assertIsNone(repo._continuous_backoff_active(cleared))
        self.assertEqual(cleared["backoff"]["failure_count"], 0)

    def test_no_available_frontier_returns_empty_selection_for_stop_condition(self):
        now = datetime.utcnow().isoformat()
        candidates = [
            {"key": "proposed-graph:node:recent", "name": "Recent node", "source_kind": "new_graph_node"},
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(
            None,
            [],
            {
                "frontier_state": {"last_enriched_at": {"proposed-graph:node:recent": now}},
                "frontier_cooldown_minutes": 360,
            },
            2,
        )

        self.assertEqual(selected, [])

    def test_append_events_keeps_latest_observability_window(self):
        repo = object.__new__(InstanceRepository)
        config = {"latest_events": [{"type": f"old-{index}"} for index in range(48)]}
        repo._continuous_append_events(config, [{"type": "budget_applied"}, {"type": "frontier_selected"}, {"type": "cycle_completed"}])

        self.assertEqual(len(config["latest_events"]), 50)
        self.assertNotEqual(config["latest_events"][0]["type"], "old-0")
        self.assertEqual(config["latest_events"][-1]["type"], "cycle_completed")

    def test_no_proposal_summary_surfaces_langextract_blocker(self):
        repo = object.__new__(InstanceRepository)
        summary = repo._continuous_no_proposal_summary(
            {
                "expansion_trace": [
                    {
                        "frontier": {"key": "frontier:chn"},
                        "last_extraction_profile": {
                            "extraction_engine_status": "api_key_missing",
                            "source": {"url": "https://zenodo.org/records/13841882"},
                            "rejected_or_ambiguous_candidates": [
                                {"reason": "langextract_api_key_missing"},
                            ],
                        },
                        "pruned": [{"reason": "no_graph_candidate_extracted"}],
                    }
                ]
            }
        )

        self.assertEqual(summary["extraction_engine_status_counts"], {"api_key_missing": 1})
        self.assertEqual(summary["rejected_candidate_reason_counts"], {"langextract_api_key_missing": 1})
        self.assertEqual(summary["pruned_reason_counts"], {"no_graph_candidate_extracted": 1})
        self.assertEqual(summary["frontier_keys"], ["frontier:chn"])


if __name__ == "__main__":
    unittest.main()
