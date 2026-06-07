import json
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy.exc import OperationalError

from server.workbench_server import (
    InstanceRepository,
    _apply_edge_source_identity_presentation_guard,
    _apply_possible_duplicate_presentation_guard,
    _dedup_audit_from_payload,
    _is_current_graph_proposal,
)
from tenant_registry import TenantConfig, TenantRegistry


class ContinuousEnrichmentFrontierTest(unittest.TestCase):
    def _repo_with_candidates(self, candidates):
        repo = object.__new__(InstanceRepository)
        repo._continuous_frontier_candidates = lambda tenant, stored, config: candidates
        return repo

    def _sqlite_tenant_repo(self, tmpdir):
        tenant = TenantConfig(
            tenant_id="tenant-a",
            namespace="tenant-a",
            display_name="Tenant A",
            graph_database="tenant_a",
            metadata_db_url=f"sqlite:///{tmpdir}/metadata.db",
            source_db_url="sqlite:///:memory:",
        )
        repo = InstanceRepository(TenantRegistry([tenant], "tenant-a"))
        return repo, tenant

    def test_default_continuous_session_objective_is_optional_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)

            session_key = repo._default_continuous_session(tenant)
            session = repo.continuous_enrichment_session(tenant, session_key)

            self.assertEqual(session["session"]["objective"], "")
            self.assertEqual(session["session"]["frontier"], [])

    def test_configure_continuous_session_updates_optional_objective(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            session_key = repo._default_continuous_session(tenant)

            updated = repo.configure_continuous_enrichment_session(
                tenant,
                session_key,
                {"objective": "maritime chokepoint disruption", "cadence": "manual"},
            )
            cleared = repo.configure_continuous_enrichment_session(tenant, session_key, {"objective": ""})

            self.assertEqual(updated["session"]["objective"], "maritime chokepoint disruption")
            self.assertEqual(cleared["session"]["objective"], "")

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

    def test_full_graph_degrades_when_source_db_is_unavailable(self):
        repo = object.__new__(InstanceRepository)
        repo._schema_graph_artifacts = lambda tenant: (
            {
                "country": {
                    "canonical_key": "object:country",
                    "artifact_type": "object",
                    "name": "Country",
                    "payload": {
                        "mapped_table_names": ["countries"],
                        "primary_key": "iso3",
                    },
                }
            },
            [],
        )

        def unavailable_source(_tenant):
            raise OperationalError("SELECT 1", {}, Exception("source db down"))

        repo.source_engine_for = unavailable_source
        tenant = TenantConfig(
            tenant_id="maritime-risk",
            namespace="maritime-risk",
            display_name="Maritime Risk",
            graph_database="aletheia",
            metadata_db_url="sqlite:///:memory:",
            source_db_url="mysql+pymysql://127.0.0.1:3306/missing",
        )

        graph = repo.full_graph(tenant, object_type="", instance_id="", limit=200)

        self.assertFalse(graph["approved"])
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["scope"]["projection_source"], "SchemaGraphModelingAgent")
        self.assertEqual(graph["scope"]["source_db_status"], "unavailable")
        self.assertTrue(graph["scope"]["degraded"])
        self.assertIn("Source database unavailable", graph["scope"]["reason"])

    def test_schema_graph_type_helpers_do_not_raise_when_source_db_is_unavailable(self):
        repo = object.__new__(InstanceRepository)
        repo.source_engine_for = lambda _tenant: (_ for _ in ()).throw(
            OperationalError("SELECT 1", {}, Exception("source db down"))
        )
        tenant = TenantConfig(
            tenant_id="maritime-risk",
            namespace="maritime-risk",
            display_name="Maritime Risk",
            graph_database="aletheia",
            metadata_db_url="sqlite:///:memory:",
            source_db_url="mysql+pymysql://127.0.0.1:3306/missing",
        )
        artifact = {
            "payload": {
                "mapped_table_names": ["countries"],
                "primary_key": "iso3",
            }
        }

        self.assertEqual(repo._source_columns(tenant, "countries"), set())
        self.assertEqual(repo._schema_graph_table_and_pk(tenant, artifact), (None, None))
        self.assertIsNone(repo._schema_graph_safe_join_condition(tenant, "countries.iso3 = edges.country_iso3"))

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

    def test_continuous_config_accepts_node_similarity_dedup_threshold(self):
        repo = object.__new__(InstanceRepository)

        config = repo._continuous_update_config({}, {"node_similarity_dedup_threshold": "0.6"})
        self.assertEqual(config["node_similarity_dedup_threshold"], 0.6)

        clamped = repo._continuous_update_config({}, {"node_similarity_dedup_threshold": "1.5"})
        self.assertEqual(clamped["node_similarity_dedup_threshold"], 1.0)

        with self.assertRaises(ValueError):
            repo._continuous_update_config({}, {"node_similarity_dedup_threshold": "not-a-number"})

    def test_stored_frontier_queue_order_takes_precedence_over_dynamic_priority(self):
        candidates = [
            {"key": "proposed-graph:node:high", "name": "High priority node", "source_kind": "new_graph_node"},
        ]
        repo = self._repo_with_candidates(candidates)
        stored_frontier = [
            {"key": "queue:first", "name": "First queued", "source_kind": "graph_coverage", "priority": 10},
            {"key": "queue:second", "name": "Second queued", "source_kind": "graph_coverage", "priority": 10},
        ]

        selected = repo._continuous_frontier_for_cycle(None, stored_frontier, {"frontier_state": {}, "frontier_cooldown_minutes": 360}, 2)

        self.assertEqual([item["key"] for item in selected], ["queue:first", "queue:second"])

    def test_dynamic_frontier_does_not_reselect_visited_key_when_cooldown_is_zero(self):
        candidates = [
            {"key": "proposed-graph:node:first", "name": "First node", "source_kind": "new_graph_node"},
            {"key": "proposed-graph:node:second", "name": "Second node", "source_kind": "new_graph_node"},
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(
            None,
            [],
            {"visited_frontier_keys": ["proposed-graph:node:first"], "frontier_state": {}, "frontier_cooldown_minutes": 0},
            1,
        )

        self.assertEqual([item["key"] for item in selected], ["proposed-graph:node:second"])

    def test_dynamic_frontier_dedupes_repeated_edge_fact_identity(self):
        candidates = [
            {
                "key": "proposed-graph:maritime-risk:edge:first-source",
                "name": "KOR has country dependency Hormuz Strait",
                "source_kind": "new_graph_edge",
                "kind": "proposed_edge",
                "payload": {
                    "source_label": "KOR",
                    "relation": "has_country_dependency",
                    "target_label": "Hormuz Strait",
                    "source_url": "https://zenodo.org/source-a",
                },
            },
            {
                "key": "proposed-graph:maritime-risk:edge:second-source",
                "name": "KOR has country dependency Hormuz Strait",
                "source_kind": "new_graph_edge",
                "kind": "proposed_edge",
                "payload": {
                    "source_label": "KOR",
                    "relation": "has_country_dependency",
                    "target_label": "Hormuz Strait",
                    "source_url": "https://zenodo.org/source-b",
                },
            },
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(
            None,
            [],
            {"visited_frontier_keys": [], "frontier_state": {}, "frontier_cooldown_minutes": 0},
            10,
        )

        self.assertEqual([item["key"] for item in selected], ["proposed-graph:maritime-risk:edge:first-source"])
        self.assertTrue(selected[0]["frontier_identity"].startswith("edge-fact:maritime-risk:kor:has_country_dependency:hormuz strait"))

    def test_dynamic_frontier_skips_visited_edge_fact_identity(self):
        candidates = [
            {
                "key": "proposed-graph:maritime-risk:edge:source-b",
                "name": "KOR has country dependency Hormuz Strait",
                "source_kind": "new_graph_edge",
                "kind": "proposed_edge",
                "payload": {
                    "source_label": "KOR",
                    "relation": "has_country_dependency",
                    "target_label": "Hormuz Strait",
                    "source_url": "https://zenodo.org/source-b",
                },
            },
            {
                "key": "proposed-graph:maritime-risk:edge:jpn-source",
                "name": "JPN has country dependency Hormuz Strait",
                "source_kind": "new_graph_edge",
                "kind": "proposed_edge",
                "payload": {
                    "source_label": "JPN",
                    "relation": "has_country_dependency",
                    "target_label": "Hormuz Strait",
                },
            },
        ]
        repo = self._repo_with_candidates(candidates)

        selected = repo._continuous_frontier_for_cycle(
            None,
            [],
            {
                "visited_frontier_keys": [
                    "edge-fact:maritime-risk:kor:has_country_dependency:hormuz strait:",
                ],
                "frontier_state": {},
                "frontier_cooldown_minutes": 0,
            },
            10,
        )

        self.assertEqual([item["key"] for item in selected], ["proposed-graph:maritime-risk:edge:jpn-source"])

    def test_next_frontier_removes_consumed_items_and_appends_new_nodes_to_tail(self):
        repo = object.__new__(InstanceRepository)
        previous = [
            {"key": "queue:first", "name": "First queued", "source_kind": "graph_coverage"},
            {"key": "queue:second", "name": "Second queued", "source_kind": "graph_coverage"},
        ]
        result = {
            "run": {"run_key": "iterative-graph:task400"},
            "proposed_graph": [
                {
                    "element_type": "node",
                    "element_key": "proposed-graph:node:new-tail",
                    "name": "New tail node",
                    "confidence": 0.7,
                    "iteration": 1,
                    "payload": {"label": "New tail node", "ontology_type": "Country"},
                }
            ],
        }

        next_frontier, additions = repo._continuous_next_frontier(
            previous,
            result,
            {"visited_frontier_keys": []},
            consumed_frontier=[previous[0]],
        )

        self.assertEqual([item["key"] for item in next_frontier], ["queue:second", "proposed-graph:node:new-tail"])
        self.assertEqual([item["key"] for item in additions], ["proposed-graph:node:new-tail"])

    def test_next_frontier_does_not_append_repeated_edge_fact_from_new_source(self):
        repo = object.__new__(InstanceRepository)
        previous = [
            {
                "key": "proposed-graph:maritime-risk:edge:source-a",
                "name": "KOR has country dependency Hormuz Strait",
                "source_kind": "new_graph_edge",
                "kind": "proposed_edge",
                "payload": {
                    "source_label": "KOR",
                    "relation": "has_country_dependency",
                    "target_label": "Hormuz Strait",
                    "source_url": "https://zenodo.org/source-a",
                },
            }
        ]
        result = {
            "run": {"run_key": "iterative-graph:task400"},
            "proposed_graph": [
                {
                    "element_type": "edge",
                    "element_key": "proposed-graph:maritime-risk:edge:source-b",
                    "name": "KOR has country dependency Hormuz Strait",
                    "confidence": 0.7,
                    "iteration": 1,
                    "payload": {
                        "source_label": "KOR",
                        "relation": "has_country_dependency",
                        "target_label": "Hormuz Strait",
                        "source_url": "https://zenodo.org/source-b",
                    },
                }
            ],
        }

        next_frontier, additions = repo._continuous_next_frontier(previous, result, {"visited_frontier_keys": []})

        self.assertEqual([item["key"] for item in next_frontier], ["proposed-graph:maritime-risk:edge:source-a"])
        self.assertEqual(additions, [])

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

    def test_presentation_guard_marks_short_alias_degraded_candidate_possible_duplicate(self):
        element = {
            "element_key": "proposed-graph:maritime-risk:node:short-country",
            "element_type": "node",
            "name": "XY",
            "status": "draft",
            "payload": {
                "ontology_type": "Country",
                "label": "XY",
                "dedup_decision": "new_proposal",
                "match_method": "embedding_degraded",
                "embedding_degraded": True,
                "llm_merge_decision_allowed": False,
            },
            "dedup_audit": {
                "dedup_decision": "new_proposal",
                "llm_merge_decision_allowed": False,
            },
        }
        guarded = _apply_possible_duplicate_presentation_guard(
            element,
            [
                {
                    "source_space": "approved_graph",
                    "source_key": "Country:XYZ",
                    "source_status": "approved",
                    "identity_key": "node:tenant:country:example",
                    "identity": {
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "Example Country (XYZ)",
                        "normalized_label": "example country xyz",
                        "aliases": [],
                        "source_identity": "Country:XYZ",
                    },
                    "dedup_text": "node | Country | Example Country (XYZ) | Country:XYZ",
                }
            ],
        )

        self.assertEqual(guarded["status"], "needs_more_evidence")
        self.assertEqual(guarded["payload"]["dedup_decision"], "needs_review")
        self.assertTrue(guarded["payload"]["possible_duplicate"])
        self.assertEqual(guarded["payload"]["matched_node_key"], "Country:XYZ")
        self.assertTrue(guarded["payload"]["possible_duplicate_candidates"])
        self.assertEqual(guarded["dedup_audit"]["dedup_decision"], "needs_review")
        self.assertEqual(guarded["dedup_audit"]["match_method"], "embedding_degraded_alias_scan")
        self.assertFalse(guarded["payload"]["llm_merge_decision_allowed"])
        self.assertFalse(guarded["presentation_guard"]["writes_persisted"])

    def test_edge_source_identity_presentation_guard_ignores_fact_occurrence_drift(self):
        element = {
            "element_key": "proposed-graph:maritime-risk:edge:new",
            "element_type": "edge",
            "name": "JPN has_country_dependency Hormuz Strait",
            "status": "needs_more_evidence",
            "payload": {
                "dedup_decision": "needs_review",
                "decision_reason": "structural_conflict",
                "conflict_fields": ["source_identity"],
                "match_method": "vector_embedding",
                "match_score": 0.9915,
                "vector_distance": 0.008549,
                "vector_duplicate_distance_threshold": 0.12,
                "matched_node_key": "proposed-graph:maritime-risk:edge:existing",
                "matched_source": "current_run_candidate",
                "matched_status": "proposed",
                "identity": {
                    "kind": "edge",
                    "source_type": "EvidenceEntity",
                    "target_type": "EvidenceEntity",
                    "source_node": "jpn",
                    "target_node": "hormuz strait",
                    "relation": "has country dependency",
                    "source_identity": "fact:8cf64ca1d1e5aada",
                },
                "match_evidence": [
                    "vector nearest-neighbor search",
                    "same source_type",
                    "same target_type",
                    "same source_node",
                    "same target_node",
                    "same relation",
                ],
                "vector_top_k": [
                    {
                        "node_key": "proposed-graph:maritime-risk:edge:existing",
                        "identity_key": "edge:maritime-risk:jpn:has country dependency:hormuz strait:fact:3b755651c2118c7c",
                        "conflict_fields": ["source_identity"],
                        "structure_evidence": [
                            "same source_type",
                            "same target_type",
                            "same source_node",
                            "same target_node",
                            "same relation",
                        ],
                    }
                ],
                "llm_merge_decision_allowed": False,
            },
        }

        guarded = _apply_edge_source_identity_presentation_guard(element)

        self.assertEqual(guarded["payload"]["dedup_decision"], "duplicate_current_run")
        self.assertTrue(guarded["payload"]["structure_compatible"])
        self.assertEqual(guarded["payload"]["conflict_fields"], [])
        self.assertEqual(guarded["payload"]["decision_reason"], "source_identity_provenance_drift_ignored")
        self.assertFalse(_is_current_graph_proposal(guarded["status"], guarded["payload"]))
        self.assertEqual(guarded["dedup_audit"]["dedup_decision"], "duplicate_current_run")
        self.assertEqual(guarded["presentation_guard"]["reason"], "edge_source_identity_provenance_drift")
        self.assertFalse(guarded["presentation_guard"]["writes_persisted"])

    def test_edge_source_identity_presentation_guard_keeps_metric_conflict_in_review(self):
        element = {
            "element_key": "proposed-graph:maritime-risk:edge:new",
            "element_type": "edge",
            "name": "JPN has_country_dependency Hormuz Strait",
            "status": "needs_more_evidence",
            "payload": {
                "dedup_decision": "needs_review",
                "decision_reason": "structural_conflict",
                "conflict_fields": ["source_identity"],
                "match_method": "vector_embedding",
                "match_score": 0.9915,
                "vector_distance": 0.008549,
                "vector_duplicate_distance_threshold": 0.12,
                "matched_node_key": "proposed-graph:maritime-risk:edge:existing",
                "matched_source": "proposed_graph",
                "matched_status": "proposed",
                "identity": {
                    "kind": "edge",
                    "source_type": "Country",
                    "target_type": "Chokepoint",
                    "source_node": "jpn",
                    "target_node": "hormuz strait",
                    "relation": "has country dependency",
                    "source_identity": "trade_at_risk_v",
                },
                "vector_top_k": [
                    {
                        "node_key": "proposed-graph:maritime-risk:edge:existing",
                        "identity_key": "edge:maritime-risk:jpn:has country dependency:hormuz strait:military_presence_score",
                        "conflict_fields": ["source_identity"],
                        "structure_evidence": [
                            "same source_type",
                            "same target_type",
                            "same source_node",
                            "same target_node",
                            "same relation",
                        ],
                    }
                ],
                "llm_merge_decision_allowed": False,
            },
        }

        guarded = _apply_edge_source_identity_presentation_guard(element)

        self.assertEqual(guarded["payload"]["dedup_decision"], "needs_review")
        self.assertEqual(guarded["payload"]["decision_reason"], "structural_conflict")
        self.assertEqual(guarded["payload"]["conflict_fields"], ["source_identity"])
        self.assertNotIn("presentation_guard", guarded)

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

    def test_reset_frontier_visit_state_clears_query_ladder_cursor(self):
        repo = object.__new__(InstanceRepository)
        config = repo._continuous_update_config(
            {
                "visited_frontier_keys": ["frontier:old"],
                "query_ladder_state": {"frontier:old": {"next_plan_index": 3}},
                "frontier_state": {
                    "last_enriched_at": {"frontier:old": "2026-06-01T00:00:00"},
                    "selected_count": {"frontier:old": 2},
                    "coverage_cursor": 12,
                },
                "stop_reason": "no available frontier after cooldown and coverage fallback",
            },
            {"reset_frontier_visit_state": True},
        )

        self.assertEqual(config["visited_frontier_keys"], [])
        self.assertEqual(config["query_ladder_state"], {})
        self.assertEqual(config["frontier_state"]["last_enriched_at"], {})
        self.assertEqual(config["frontier_state"]["selected_count"], {})
        self.assertEqual(config["frontier_state"]["coverage_cursor"], 0)
        self.assertIsNone(config["stop_reason"])

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

    def test_crawl_policy_allows_discovered_domains_when_source_trust_is_not_strict_allowlist(self):
        repo = object.__new__(InstanceRepository)

        strict = repo._continuous_agent_crawl_policy(
            {"source_trust": {"allowed_domains": ["zenodo.org"], "reject_unlisted_domains": True}}
        )
        permissive = repo._continuous_agent_crawl_policy(
            {"source_trust": {"allowed_domains": ["zenodo.org"], "reject_unlisted_domains": False}}
        )

        self.assertEqual(strict["allowed_domains"], ["zenodo.org"])
        self.assertFalse(strict["allow_discovered_domains"])
        self.assertEqual(permissive["allowed_domains"], [])
        self.assertTrue(permissive["allow_discovered_domains"])

    def test_source_trust_star_allows_all_public_sources(self):
        repo = object.__new__(InstanceRepository)
        config = repo._continuous_update_config(
            {"source_trust": {"allowed_domains": ["zenodo.org"], "reject_unlisted_domains": True}},
            {"source_trust": "*"},
        )
        policy = repo._continuous_source_trust_policy(config)
        decision = repo._continuous_source_trust_decision({"url": "https://example.org/source"}, config)
        crawl_policy = repo._continuous_agent_crawl_policy(config)

        self.assertEqual(config["source_trust"]["allowed_domains"], ["*"])
        self.assertTrue(policy["allow_all_public_sources"])
        self.assertFalse(policy["reject_unlisted_domains"])
        self.assertTrue(decision["trusted"])
        self.assertEqual(crawl_policy["allowed_domains"], [])
        self.assertTrue(crawl_policy["allow_discovered_domains"])

    def test_continuous_source_fixture_does_not_inject_runtime_demo_sources(self):
        repo = object.__new__(InstanceRepository)

        self.assertEqual(repo._continuous_source_fixture("continuous:maritime-risk:us-iran-impact:mvp"), [])

    def test_continuous_cycle_uses_frontier_query_plan_for_search_when_no_results_provided(self):
        class FakeSearchProvider:
            def __init__(self):
                self.queries = []

            def request_url(self, query):
                return f"https://search.test/html/?q={query.replace(' ', '+')}"

            def search(self, query, max_results):
                self.queries.append((query, max_results))
                return [
                    {
                        "query": query,
                        "title": "Hormuz dependency source",
                        "url": "https://zenodo.org/records/query-plan-source",
                        "snippet": "KOR depends on Hormuz Strait shipping exposure.",
                        "rank": 1,
                        "provider": "fake",
                    }
                ]

        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        provider = FakeSearchProvider()
        frontier = [
            {
                "key": "proposed-graph:maritime-risk:edge:kor-hormuz",
                "name": "KOR has country dependency Hormuz Strait",
                "payload": {
                    "source_label": "KOR",
                    "target_label": "Hormuz Strait",
                    "relation": "has_country_dependency",
                },
            }
        ]

        results, events = repo._continuous_search_results_for_cycle(
            tenant,
            "continuous:maritime-risk:us-iran-impact:mvp",
            "Analyze maritime dependency risk",
            {},
            frontier,
            2,
            {"search_provider": "duckduckgo_html"},
            provider=provider,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["frontier_key"], "proposed-graph:maritime-risk:edge:kor-hormuz")
        self.assertEqual(results[0]["provider"], "fake")
        self.assertEqual(provider.queries[0][1], 2)
        self.assertIn("KOR", provider.queries[0][0])
        self.assertIn("Hormuz Strait", provider.queries[0][0])
        self.assertTrue(results[0]["request_url"].startswith("https://search.test/html/?q="))
        self.assertEqual(events[-1]["type"], "query_search_executed")
        self.assertTrue(events[-1]["request_url"].startswith("https://search.test/html/?q="))
        self.assertEqual(events[-1]["accepted_for_trust_filter_count"], 1)

    def test_continuous_query_search_failed_records_request_url(self):
        class FailingSearchProvider:
            def request_url(self, query):
                return f"https://search.test/html/?q={query.replace(' ', '+')}"

            def search(self, query, max_results):
                raise TimeoutError("duckduckgo timeout")

        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        frontier = [
            {
                "key": "proposed-graph:maritime-risk:edge:kor-hormuz",
                "name": "KOR has country dependency Hormuz Strait",
                "payload": {
                    "source_label": "KOR",
                    "target_label": "Hormuz Strait",
                    "relation": "has_country_dependency",
                },
            }
        ]

        results, events = repo._continuous_search_results_for_cycle(
            tenant,
            "continuous:maritime-risk:us-iran-impact:mvp",
            "Analyze maritime dependency risk",
            {},
            frontier,
            2,
            {"search_provider": "duckduckgo_html", "max_query_plans_per_frontier": 1},
            provider=FailingSearchProvider(),
        )

        self.assertEqual(results, [])
        failed = [event for event in events if event["type"] == "query_search_failed"][0]
        self.assertIn("KOR", failed["query"])
        self.assertTrue(failed["request_url"].startswith("https://search.test/html/?q="))
        self.assertIn("duckduckgo timeout", failed["error"])

    def test_continuous_query_search_coarsens_ladder_when_exact_query_has_no_results(self):
        class FakeSearchProvider:
            def __init__(self):
                self.queries = []

            def search(self, query, max_results):
                self.queries.append(query)
                if len(self.queries) == 1:
                    return []
                return [
                    {
                        "query": query,
                        "title": "Broader frontier source",
                        "url": "https://zenodo.org/records/query-ladder-source",
                        "snippet": "A broader source mentions KOR and Hormuz Strait exposure.",
                        "rank": 1,
                        "provider": "fake",
                    }
                ]

        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        provider = FakeSearchProvider()
        config = {"search_provider": "duckduckgo_html", "max_query_plans_per_frontier": 5}
        frontier = [
            {
                "key": "proposed-graph:maritime-risk:edge:kor-hormuz",
                "name": "KOR has country dependency Hormuz Strait",
                "payload": {
                    "source_type": "Country",
                    "target_type": "MaritimeChokepoint",
                    "source_label": "KOR",
                    "target_label": "Hormuz Strait",
                    "relation": "has_country_dependency",
                },
            }
        ]

        results, events = repo._continuous_search_results_for_cycle(
            tenant,
            "continuous:maritime-risk:us-iran-impact:mvp",
            "Analyze maritime dependency risk",
            {},
            frontier,
            2,
            config,
            provider=provider,
        )

        executed = [event for event in events if event["type"] == "query_search_executed"]
        self.assertEqual(len(provider.queries), 2)
        self.assertEqual(executed[0]["granularity"], "L0_path_exact")
        self.assertEqual(executed[0]["accepted_for_trust_filter_count"], 0)
        self.assertEqual(executed[1]["granularity"], "L1_single_endpoint")
        self.assertEqual(results[0]["query_plan"]["granularity"], "L1_single_endpoint")
        self.assertEqual(results[0]["query_plan"]["coarse_level"], 1)
        self.assertEqual(
            config["query_ladder_state"]["proposed-graph:maritime-risk:edge:kor-hormuz"]["last_search_signal"],
            "search_results_found",
        )

    def test_provided_search_results_bypass_query_search_provider(self):
        class FailingSearchProvider:
            def search(self, query, max_results):
                raise AssertionError("provider should not be called for explicit search_results")

        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        provided = [{"title": "provided", "url": "https://example.com/source"}]

        results, events = repo._continuous_search_results_for_cycle(
            tenant,
            "continuous:tenant-a:demo",
            "objective",
            {"search_results": provided},
            [{"key": "frontier:a", "name": "A"}],
            2,
            {},
            provider=FailingSearchProvider(),
        )

        self.assertEqual(results, provided)
        self.assertEqual(events[0]["type"], "provided_search_results_used")

    def test_graph_coverage_frontier_prefers_high_degree_nodes_without_hardcoded_seed(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        repo.full_graph = lambda tenant, limit=200: {
            "nodes": [
                {"id": "Country:LOW", "label": "LOW", "type": "Country"},
                {"id": "Country:HIGH", "label": "HIGH", "type": "Country"},
            ],
            "edges": [
                {"source": "Country:HIGH", "target": "Chokepoint:A"},
                {"source": "Country:HIGH", "target": "Chokepoint:B"},
                {"source": "Country:LOW", "target": "Chokepoint:A"},
            ],
        }

        items = repo._continuous_graph_coverage_frontier(tenant, limit=2)

        self.assertEqual([item["key"] for item in items], ["graph-coverage:Country:HIGH", "graph-coverage:Country:LOW"])
        self.assertEqual(items[0]["payload"]["selection_policy"], "degree_coverage")
        self.assertGreater(items[0]["payload"]["degree"], items[1]["payload"]["degree"])

    def test_graph_coverage_frontier_advances_after_cursor(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        repo.full_graph = lambda tenant, limit=200: {
            "nodes": [
                {"id": "Country:A", "label": "A", "type": "Country"},
                {"id": "Country:B", "label": "B", "type": "Country"},
                {"id": "Country:C", "label": "C", "type": "Country"},
                {"id": "Country:D", "label": "D", "type": "Country"},
            ],
            "edges": [
                {"source": "Country:A", "target": "Chokepoint:1"},
                {"source": "Country:B", "target": "Chokepoint:1"},
                {"source": "Country:C", "target": "Chokepoint:1"},
            ],
        }

        items = repo._continuous_graph_coverage_frontier(
            tenant,
            config={"frontier_state": {"coverage_cursor": 2}},
            limit=2,
        )

        self.assertEqual([item["key"] for item in items], ["graph-coverage:Country:C", "graph-coverage:Country:D"])

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

    def test_auto_scheduler_due_requires_idle_non_manual_due_session(self):
        repo = object.__new__(InstanceRepository)
        due_at = (datetime.utcnow() - timedelta(seconds=5)).isoformat()

        due, reason = repo._continuous_session_auto_due("idle", {"cadence": "custom", "custom_interval_minutes": 1, "next_run_at": due_at})
        self.assertTrue(due)
        self.assertEqual(reason, "next_run_due")

        due, reason = repo._continuous_session_auto_due("idle", {"cadence": "manual", "next_run_at": due_at})
        self.assertFalse(due)
        self.assertEqual(reason, "manual_cadence")

        due, reason = repo._continuous_session_auto_due("paused", {"cadence": "custom", "custom_interval_minutes": 1, "next_run_at": due_at})
        self.assertFalse(due)
        self.assertEqual(reason, "status_paused")

    def test_auto_scheduler_treats_non_manual_missing_next_run_as_due(self):
        repo = object.__new__(InstanceRepository)

        due, reason = repo._continuous_session_auto_due("idle", {"cadence": "custom", "custom_interval_minutes": 1})

        self.assertTrue(due)
        self.assertEqual(reason, "cadence_without_next_run_at")

    def test_runtime_state_exposes_persistent_frontier_queue(self):
        repo = object.__new__(InstanceRepository)
        due_at = (datetime.utcnow() - timedelta(seconds=5)).isoformat()
        state = repo._continuous_session_runtime_state(
            "idle",
            {
                "cadence": "custom",
                "custom_interval_minutes": 1,
                "next_run_at": due_at,
                "last_started_at": "2026-06-02T01:00:00",
                "last_finished_at": "2026-06-02T01:01:00",
                "completed_cycles": 2,
                "budget": {"max_cycles": 5, "max_frontier_per_cycle": 4},
                "visited_frontier_keys": ["queue:visited"],
                "frontier_state": {"coverage_cursor": 7, "last_enriched_at": {"queue:visited": "2026-06-02T00:00:00"}},
            },
            [
                {"key": "queue:first", "name": "First", "source_kind": "graph_coverage"},
                {"target_key": "queue:second", "name": "Second", "source_kind": "new_graph_node"},
            ],
        )

        self.assertTrue(state["persistent"])
        self.assertEqual(state["queue_mode"], "fifo_frontier_queue")
        self.assertEqual(state["frontier_queue_count"], 2)
        self.assertEqual(state["next_frontier_keys"], ["queue:first", "queue:second"])
        self.assertEqual(state["frontier_queue"]["total_count"], 2)
        self.assertEqual(state["frontier_queue"]["source_kind_counts"], {"graph_coverage": 1, "new_graph_node": 1})
        self.assertEqual(state["budget"]["remaining_cycles"], 3)
        self.assertEqual(state["frontier_state"]["visited_count"], 1)
        self.assertEqual(state["frontier_state"]["coverage_cursor"], 7)
        self.assertTrue(state["auto_due"])
        self.assertEqual(state["auto_due_reason"], "next_run_due")

    def test_resume_persistent_session_preserves_queue_and_schedules_immediate_tick(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        row = {
            "config_json": json.dumps(
                {
                    "cadence": "custom",
                    "custom_interval_minutes": 1,
                    "stop_reason": "operator paused",
                    "latest_events": [],
                }
            ),
            "frontier_json": json.dumps([{"key": "queue:first", "name": "First"}]),
        }
        captured = {}

        class CaptureConn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, _query, params):
                captured.update(params)

        class CaptureEngine:
            def begin(self):
                return CaptureConn()

        repo._continuous_session_row = lambda _tenant, _session_key: row
        repo.metadata_engine_for = lambda _tenant: CaptureEngine()
        repo.continuous_enrichment_session = lambda _tenant, _session_key: {"session": {"status": captured.get("status")}}

        result = repo.update_continuous_enrichment_session_status(tenant, "continuous:tenant-a", "idle")
        config = json.loads(captured["config_json"])

        self.assertEqual(result["session"]["status"], "idle")
        self.assertEqual(config["stop_reason"], None)
        self.assertIsNotNone(config["next_run_at"])
        self.assertEqual(config["latest_events"][-1]["type"], "session_resumed")
        self.assertEqual(config["latest_events"][-1]["frontier_queue_count"], 1)

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
