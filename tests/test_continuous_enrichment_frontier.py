import json
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from server.aletheia_server import (
    InstanceRepository,
    _apply_edge_source_identity_presentation_guard,
    _apply_possible_duplicate_presentation_guard,
    _dedup_audit_from_payload,
    _is_current_graph_proposal,
    _knowledge_candidate_profile,
)
from agents.ontology_artifacts import ensure_artifact_schema, upsert_artifact
from agents.iterative_graph_enrichment_agent import _graph_context_query_plan
from tenant_registry import TenantConfig, TenantRegistry


class ContinuousEnrichmentFrontierTest(unittest.TestCase):
    def test_knowledge_candidate_profile_keeps_graph_kind_internal(self):
        node_profile = _knowledge_candidate_profile("node", {"ontology_type": "Country", "label": "Iran"})
        edge_profile = _knowledge_candidate_profile(
            "edge",
            {"source_label": "Iran", "relation": "depends_on", "target_label": "Strait of Hormuz"},
        )

        self.assertEqual(node_profile["graph_element_kind"], "node")
        self.assertEqual(node_profile["knowledge_kind"], "object")
        self.assertEqual(node_profile["review_domain"], "knowledge")
        self.assertEqual(edge_profile["graph_element_kind"], "edge")
        self.assertEqual(edge_profile["knowledge_kind"], "relation")
        self.assertEqual(edge_profile["review_domain"], "knowledge")

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

    def test_approving_ontology_concept_promotes_to_catalog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            engine = repo.metadata_engine_for(tenant)
            ensure_artifact_schema(engine)
            payload = {
                "artifact_type": "event",
                "label": "PortClosureEvent",
                "description": "A port closure that should trigger operational response.",
                "trigger_event": "Port closure announced",
                "target_object_types": ["Port"],
                "expected_effects": ["Port.operational_status -> closed"],
                "evidence_quote": "The port was closed after the announcement.",
            }
            with engine.begin() as conn:
                run_result = conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_iterative_graph_enrichment_runs
                            (project_id, run_key, source_agent, status, objective,
                             frontier_json, expansion_trace_json, safety_profile_json,
                             budget_json, skipped_sources_json, proposed_count,
                             pruned_count, finding_count, started_at)
                        VALUES
                            (:project_id, :run_key, 'IterativeGraphEnrichmentAgent',
                             'completed', 'test ontology promotion', '[]', '[]',
                             '{}', '{}', '[]', 1, 0, 0, :started_at)
                        """
                    ),
                    {
                        "project_id": tenant.tenant_id,
                        "run_key": "ontology-promotion-test",
                        "started_at": datetime.utcnow(),
                    },
                )
                run_id = run_result.lastrowid
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_proposed_graph_elements
                            (run_id, project_id, element_key, element_type, name,
                             payload_json, evidence_refs_json, source_url,
                             confidence, status, iteration, created_at)
                        VALUES
                            (:run_id, :project_id, :element_key, 'ontology_concept',
                             :name, :payload_json, :evidence_refs_json, :source_url,
                             0.88, 'draft', 1, :created_at)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "project_id": tenant.tenant_id,
                        "element_key": "proposed-graph:tenant-a:ontology-concept:port-closure-event",
                        "name": "PortClosureEvent",
                        "payload_json": json.dumps(payload),
                        "evidence_refs_json": json.dumps(["gpt_researcher://report/port-closure"]),
                        "source_url": "gpt_researcher://report/port-closure",
                        "created_at": datetime.utcnow(),
                    },
                )

            result = repo.review_proposed_graph_element(
                tenant,
                "proposed-graph:tenant-a:ontology-concept:port-closure-event",
                "approve",
                {"reviewer": "M. Aoki", "reason": "test promotion", "review_surface": "ontology"},
            )

            self.assertEqual(result["element"]["status"], "approved")
            self.assertTrue(result["write_boundary"]["canonical_write"])
            self.assertTrue(result["write_boundary"]["graph_space_write"])
            self.assertEqual(result["write_boundary"]["target"], "ontology_catalog_and_graph_space")
            self.assertEqual(result["promoted_artifact"]["artifact_type"], "action")
            self.assertTrue(result["promoted_artifact"]["graph_space_element_key"].startswith("ontology-model:tenant-a:node:"))

            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT artifact_type, name, status, payload_json
                        FROM aletheia_ontology_artifacts
                        WHERE project_id = :project_id AND name = 'PortClosureEvent'
                        """
                    ),
                    {"project_id": tenant.tenant_id},
                ).mappings().one()
                graph_row = conn.execute(
                    text(
                        """
                        SELECT element_type, status, payload_json
                        FROM aletheia_proposed_graph_elements
                        WHERE project_id = :project_id AND element_key = :element_key
                        """
                    ),
                    {
                        "project_id": tenant.tenant_id,
                        "element_key": result["promoted_artifact"]["graph_space_element_key"],
                    },
                ).mappings().one()
            artifact_payload = json.loads(row["payload_json"])
            self.assertEqual(row["artifact_type"], "action")
            self.assertEqual(row["status"], "approved")
            self.assertEqual(artifact_payload["source_artifact_type"], "event")
            self.assertEqual(artifact_payload["source_proposed_graph_element_key"], "proposed-graph:tenant-a:ontology-concept:port-closure-event")
            graph_payload = json.loads(graph_row["payload_json"])
            self.assertEqual(graph_row["element_type"], "ontology_model_node")
            self.assertEqual(graph_row["status"], "approved")
            self.assertEqual(graph_payload["ontology_artifact"], result["promoted_artifact"]["canonical_key"])
            self.assertEqual(graph_payload["graph_space"]["space"], "ontology_model")

    def test_concrete_object_candidate_resolves_to_schema_instance_by_explicit_identity(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        repo.types = lambda tenant, include_draft=False: {
            "types": [
                {"type": "Jurisdiction", "label": "Jurisdiction", "ontology_artifact": "object:jurisdiction"},
            ]
        }
        repo.search = lambda tenant, object_type, query, limit=25, include_draft=False: {
            "instances": [
                {
                    "id": "Jurisdiction:CHN",
                    "label": "CHN",
                    "ontology_artifact": "object:jurisdiction",
                    "status": "approved",
                }
            ]
        }

        result = repo._resolve_ontology_candidate_existing_instance(
            tenant,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "China",
                "identity": {"source_identity": "CHN"},
            },
            catalog_type="object",
            artifact_type="object",
            label="China",
        )

        self.assertIsNotNone(result)
        self.assertEqual(result["canonical_key"], "Jurisdiction:CHN")
        self.assertEqual(result["ontology_artifact"], "object:jurisdiction")
        self.assertEqual(result["match_method"], "schema_exact_instance_query")
        self.assertTrue(result["matched_existing_instance"])

    def test_ontology_model_graph_links_semantic_items_to_approved_ontology(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            engine = repo.metadata_engine_for(tenant)
            ensure_artifact_schema(engine)
            with engine.begin() as conn:
                run_result = conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_iterative_graph_enrichment_runs
                            (project_id, run_key, source_agent, status, objective,
                             frontier_json, expansion_trace_json, safety_profile_json,
                             budget_json, skipped_sources_json, proposed_count,
                             pruned_count, finding_count, started_at)
                        VALUES
                            (:project_id, :run_key, 'IterativeGraphEnrichmentAgent',
                             'completed', 'test ontology model graph', '[]', '[]',
                             '{}', '{}', '[]', 1, 0, 0, :started_at)
                        """
                    ),
                    {
                        "project_id": tenant.tenant_id,
                        "run_key": "ontology-model-graph-test",
                        "started_at": datetime.utcnow(),
                    },
                )
                run_id = run_result.lastrowid
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_proposed_graph_elements
                            (run_id, project_id, element_key, element_type, name,
                             payload_json, evidence_refs_json, source_url,
                             confidence, status, iteration, created_at)
                        VALUES
                            (:run_id, :project_id, :element_key, 'recommendation',
                             :name, :payload_json, :evidence_refs_json, :source_url,
                             0.81, 'needs_more_evidence', 1, :created_at)
                        """
                    ),
                    {
                        "run_id": run_id,
                        "project_id": tenant.tenant_id,
                        "element_key": "proposed-graph:tenant-a:recommendation:reroute",
                        "name": "Reroute vessels after closure",
                        "payload_json": json.dumps(
                            {
                                "recommended_action": "ProposeShippingReroute",
                                "source_url": "gpt_researcher://report/test",
                                "evidence_quote": "Operators should reroute vessels after the port closure.",
                            }
                        ),
                        "evidence_refs_json": json.dumps(["gpt_researcher://report/test"]),
                        "source_url": "gpt_researcher://report/test",
                        "created_at": datetime.utcnow(),
                    },
                )
            from sqlalchemy.orm import sessionmaker

            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                upsert_artifact(
                    session,
                    artifact_type="action",
                    natural_key="propose-shipping-reroute",
                    name="ProposeShippingReroute",
                    description="Operational action for rerouting vessels.",
                    payload={
                        "artifact_type": "action",
                        "ontology_part": "action",
                        "source_artifact_type": "action",
                        "label": "ProposeShippingReroute",
                        "target_object_types": ["Vessel"],
                        "evidence_quote": "Operators should reroute vessels after the port closure.",
                        "source_url": "gpt_researcher://report/test",
                    },
                    source_refs=["gpt_researcher://report/test"],
                    source_agent="DeepResearchOntologyExpansion",
                    project_id=tenant.tenant_id,
                    confidence=0.81,
                    status="approved",
                )
                session.commit()
            finally:
                session.close()

            graph = repo.ontology_model_graph(tenant, limit=50)

            self.assertTrue(graph["approved"])
            node_ids = {node["id"] for node in graph["nodes"]}
            self.assertIn("ontology:action:propose-shipping-reroute", node_ids)
            self.assertIn("semantic:proposed-graph:tenant-a:recommendation:reroute", node_ids)
            self.assertTrue(
                any(
                    edge["source"] == "semantic:proposed-graph:tenant-a:recommendation:reroute"
                    and edge["target"] == "ontology:action:propose-shipping-reroute"
                    and edge["label"] == "supports"
                    for edge in graph["edges"]
                )
            )

    def test_auto_approve_low_duplicate_graph_proposal_uses_review_settings(self):
        repo = object.__new__(InstanceRepository)
        reviewed = []
        repo.review_proposed_graph_element = lambda tenant, key, action, body: reviewed.append((key, action, body)) or {
            "element": {"element_key": key, "status": "approved"}
        }
        tenant = TenantConfig(
            tenant_id="tenant-a",
            namespace="tenant-a",
            display_name="Tenant A",
            graph_database="tenant_a",
            metadata_db_url="sqlite:///:memory:",
            source_db_url="sqlite:///:memory:",
        )
        element = {
            "element_key": "proposed-graph:tenant-a:node:high-confidence",
            "element_type": "node",
            "status": "draft",
            "confidence": 0.82,
            "payload": {"dedup_decision": "new_proposal", "match_score": 0.42},
        }

        result = repo._continuous_auto_approve_low_duplicate_proposals(
            tenant,
            [element],
            {
                "auto_approve_low_duplicate_proposals": True,
                "auto_approve_min_confidence": 0.8,
                "auto_approve_max_duplicate_score": 0.5,
                "auto_review_reviewer": "Auto Review",
            },
        )

        self.assertEqual(len(result["reviewed"]), 1)
        self.assertEqual(reviewed[0][0], "proposed-graph:tenant-a:node:high-confidence")
        self.assertEqual(reviewed[0][1], "approve")
        self.assertIn("confidence 0.8200", reviewed[0][2]["reason"])

    def test_auto_approve_low_duplicate_skips_high_duplicate_score(self):
        repo = object.__new__(InstanceRepository)
        repo.review_proposed_graph_element = lambda *_args, **_kwargs: self.fail("high duplicate proposal should not auto approve")
        tenant = TenantConfig(
            tenant_id="tenant-a",
            namespace="tenant-a",
            display_name="Tenant A",
            graph_database="tenant_a",
            metadata_db_url="sqlite:///:memory:",
            source_db_url="sqlite:///:memory:",
        )
        element = {
            "element_key": "proposed-graph:tenant-a:node:duplicate",
            "element_type": "node",
            "status": "draft",
            "confidence": 0.91,
            "payload": {"dedup_decision": "needs_review", "match_score": 0.73},
        }

        result = repo._continuous_auto_approve_low_duplicate_proposals(
            tenant,
            [element],
            {
                "auto_approve_low_duplicate_proposals": True,
                "auto_approve_min_confidence": 0.8,
                "auto_approve_max_duplicate_score": 0.5,
            },
        )

        self.assertEqual(result["reviewed"], [])
        self.assertEqual(result["skipped"][0]["reason"], "above_duplicate_threshold")

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

    def test_continuous_config_accepts_auto_review_similarity_settings(self):
        repo = object.__new__(InstanceRepository)

        config = repo._continuous_update_config(
            {},
            {
                "auto_review_similar_proposals": True,
                "auto_review_llm_verifier": False,
                "auto_reject_similarity_threshold": "0.91",
                "auto_review_reviewer": "Auto Reviewer",
            },
        )

        self.assertTrue(config["auto_review_similar_proposals"])
        self.assertFalse(config["auto_review_llm_verifier"])
        self.assertEqual(config["auto_reject_similarity_threshold"], 0.91)
        self.assertEqual(config["auto_review_reviewer"], "Auto Reviewer")

    def test_auto_review_rejects_high_similarity_non_conflicting_duplicate(self):
        repo = object.__new__(InstanceRepository)
        element = {
            "element_key": "proposed-graph:edge:new",
            "status": "draft",
            "payload": {
                "dedup_decision": "duplicate_existing_proposal",
                "match_score": 0.95,
                "matched_node_key": "proposed-graph:edge:existing",
            },
        }

        should_reject, reason, score = repo._should_auto_reject_similar_proposal(
            element,
            {"auto_review_similar_proposals": True, "auto_reject_similarity_threshold": 0.92},
        )

        self.assertTrue(should_reject)
        self.assertEqual(reason, "high_similarity_duplicate")
        self.assertEqual(score, 0.95)

    def test_auto_review_does_not_reject_structural_conflict(self):
        repo = object.__new__(InstanceRepository)
        element = {
            "element_key": "proposed-graph:edge:new",
            "status": "needs_more_evidence",
            "payload": {
                "dedup_decision": "needs_review",
                "decision_reason": "structural_conflict",
                "conflict_fields": ["source_node", "target_node", "relation"],
                "match_score": 0.99,
                "matched_node_key": "proposed-graph:edge:existing",
            },
        }

        should_reject, reason, score = repo._should_auto_reject_similar_proposal(
            element,
            {"auto_review_similar_proposals": True, "auto_reject_similarity_threshold": 0.92},
        )

        self.assertFalse(should_reject)
        self.assertEqual(reason, "structural_conflict_requires_human_review")
        self.assertEqual(score, 0.99)

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

    def test_frontier_query_plan_is_recall_oriented_not_instruction_or_schema_terms(self):
        plan = _graph_context_query_plan(
            {
                "key": "proposed-graph:maritime-risk:edge:1bdbe61b8ac0d42f",
                "name": "Iran has systemic risk Strait of Hormuz",
                "payload": {
                    "source_label": "Iran",
                    "target_label": "Strait of Hormuz",
                    "relation": "has_systemic_risk",
                },
            },
            "Find recent public evidence about maritime chokepoint disruption risks affecting China trade dependencies.",
            "maritime-risk",
        )

        queries = [item["query"] for item in plan["plans"]]
        joined = " ".join(queries).lower()
        self.assertTrue(any("iran" in query.lower() and "strait of hormuz" in query.lower() for query in queries))
        self.assertNotIn("find recent public evidence", joined)
        self.assertNotIn("has_systemic_risk", joined)
        self.assertNotIn("proposed_edge", joined)
        self.assertNotIn("evidenceentity", joined)

    def test_frontier_query_plan_excludes_retrieval_provider_terms_from_objective(self):
        plan = _graph_context_query_plan(
            {
                "key": "graph-coverage:MaritimeChokepoint:Cape of Good Hope",
                "name": "Cape of Good Hope",
                "artifact_type": "MaritimeChokepoint",
                "payload": {"label": "Cape of Good Hope"},
            },
            "Expand maritime chokepoint ontology and semantic knowledge coverage using GPT Researcher summaries.",
            "maritime-risk",
        )

        queries = [item["query"] for item in plan["plans"]]
        joined = " ".join(queries).lower()
        self.assertTrue(any("cape of good hope" in query.lower() for query in queries))
        self.assertNotIn("gpt researcher", joined)
        self.assertNotIn("researcher", joined)
        self.assertNotIn("expand", joined)

    def test_continuous_cycle_splits_research_topic_from_execution_goal(self):
        repo = object.__new__(InstanceRepository)
        retrieval, execution = repo._continuous_retrieval_objective(
            "Expand ontology coverage using GPT Researcher summaries",
            {"research_topic": "maritime chokepoint disruption risks"},
            {"objective": "Run enrichment workflow using GPT Researcher"},
        )

        self.assertEqual(retrieval, "maritime chokepoint disruption risks")
        self.assertEqual(execution, "Run enrichment workflow using GPT Researcher")

    def test_continuous_cycle_legacy_objective_fallback_filters_provider_terms(self):
        repo = object.__new__(InstanceRepository)
        retrieval, execution = repo._continuous_retrieval_objective(
            "",
            {},
            {"objective": "Expand maritime chokepoint ontology coverage using GPT Researcher summaries"},
        )

        self.assertIn("maritime", retrieval)
        self.assertIn("chokepoint", retrieval)
        self.assertNotIn("GPT", retrieval)
        self.assertNotIn("Researcher", retrieval)
        self.assertNotIn("Expand", retrieval)
        self.assertEqual(execution, "Expand maritime chokepoint ontology coverage using GPT Researcher summaries")

    def test_deep_research_mode_selects_topic_frontier(self):
        repo = object.__new__(InstanceRepository)
        repo._continuous_proposed_graph_frontier = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("deep research should not load proposed graph frontier")
        )
        tenant = type("Tenant", (), {"tenant_id": "maritime-risk"})()
        config = {
            "research_mode": "deep_research",
            "research_topics": ["Middle East maritime chokepoint systemic risk"],
            "retrieval_lanes": ["breaking_news", "academic"],
            "recency_windows": ["24h", "historical"],
            "frontier_state": {},
            "frontier_cooldown_minutes": 360,
            "max_frontier": 4,
        }

        selected = repo._continuous_frontier_for_cycle(tenant, [], config, 4)

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["source_kind"], "research_topic")
        self.assertEqual(selected[0]["name"], "Middle East maritime chokepoint systemic risk")
        self.assertEqual(selected[0]["payload"]["retrieval_lanes"], ["breaking_news", "academic"])

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

    def test_instance_coverage_frontier_includes_approved_instance_with_missing_relations(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        repo.types = lambda tenant, include_draft=False: {
            "types": [
                {"type": "Country", "table": "country", "ontology_artifact": "object:country"},
            ]
        }
        repo.search = lambda tenant, object_type, query, limit=25, include_draft=False: {
            "instances": [
                {
                    "id": "Country:CHN",
                    "label": "CHN",
                    "source_table": "country",
                    "source_pk": "iso3=CHN",
                    "ontology_artifact": "object:country",
                }
            ]
        }
        repo.detail = lambda tenant, object_type, instance_id: {
            "relations_summary": {"edges": 0, "by_relation": {}}
        }

        items = repo._continuous_instance_coverage_frontier(
            tenant,
            config={"instance_coverage_min_edges": 1},
            limit=10,
        )

        self.assertEqual([item["key"] for item in items], ["instance-coverage:Country:CHN"])
        self.assertEqual(items[0]["source_kind"], "instance_coverage")
        self.assertEqual(items[0]["payload"]["selection_policy"], "approved_instance_relation_or_enrichment_gap_coverage")
        self.assertEqual(items[0]["payload"]["relation_count"], 0)

    def test_instance_coverage_frontier_includes_instance_with_sparse_enrichment_even_when_relations_exist(self):
        repo = object.__new__(InstanceRepository)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        repo.types = lambda tenant, include_draft=False: {
            "types": [
                {"type": "Country", "table": "country", "ontology_artifact": "object:country"},
            ]
        }
        repo.search = lambda tenant, object_type, query, limit=25, include_draft=False: {
            "instances": [
                {"id": "Country:CHN", "label": "CHN", "source_table": "country", "source_pk": "iso3=CHN"},
            ]
        }
        repo.detail = lambda tenant, object_type, instance_id: {
            "relations_summary": {"edges": 49, "by_relation": {"country_chokepoint_dependency": 25}}
        }
        repo._continuous_instance_enrichment_counts = lambda tenant, records: {
            record["node_id"]: 1 for record in records
        }

        items = repo._continuous_instance_coverage_frontier(
            tenant,
            config={"instance_coverage_min_edges": 1, "instance_coverage_min_enrichment_items": 2},
            limit=10,
        )

        self.assertEqual([item["key"] for item in items], ["instance-coverage:Country:CHN"])
        self.assertEqual(items[0]["payload"]["relation_coverage_gap"], 0)
        self.assertEqual(items[0]["payload"]["enrichment_coverage_gap"], 1)

    def test_stored_frontier_backfills_dynamic_instance_coverage_when_queue_unavailable(self):
        now = datetime.utcnow().isoformat()
        dynamic = [
            {
                "key": "instance-coverage:Country:CHN",
                "name": "CHN Country relation coverage",
                "source_kind": "instance_coverage",
                "payload": {
                    "label": "CHN",
                    "ontology_type": "Country",
                    "identity_key": "Country:CHN",
                    "selection_policy": "approved_instance_relation_or_enrichment_gap_coverage",
                },
            }
        ]
        repo = self._repo_with_candidates(dynamic)
        stored_frontier = [
            {"key": "queue:recent", "name": "Recent queued", "source_kind": "graph_coverage"},
        ]

        selected = repo._continuous_frontier_for_cycle(
            None,
            stored_frontier,
            {
                "frontier_state": {"last_enriched_at": {"queue:recent": now}},
                "frontier_cooldown_minutes": 360,
            },
            1,
        )

        self.assertEqual([item["key"] for item in selected], ["instance-coverage:Country:CHN"])
        self.assertEqual(selected[0]["source_kind"], "instance_coverage")
        self.assertEqual(
            selected[0]["frontier_identity"],
            "node:country:chn",
        )

    def test_objective_matched_dynamic_frontier_can_preempt_stored_queue(self):
        dynamic = [
            {
                "key": "instance-coverage:Country:XYZ",
                "name": "XYZ Country enrichment coverage",
                "source_kind": "instance_coverage",
                "payload": {"label": "XYZ", "ontology_type": "Country", "identity_key": "Country:XYZ"},
            }
        ]
        repo = self._repo_with_candidates(dynamic)
        stored_frontier = [
            {
                "key": "queue:generic",
                "name": "Generic stored frontier",
                "source_kind": "graph_coverage",
                "priority": 10,
            }
        ]

        selected = repo._continuous_frontier_for_cycle(
            None,
            stored_frontier,
            {
                "frontier_state": {},
                "frontier_cooldown_minutes": 360,
                "_frontier_selection_objective": "expand Country XYZ semantic coverage",
            },
            1,
        )

        self.assertEqual([item["key"] for item in selected], ["instance-coverage:Country:XYZ"])

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

    def test_running_session_stale_detection_uses_started_at_threshold(self):
        repo = object.__new__(InstanceRepository)
        old_started_at = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
        recent_started_at = (datetime.utcnow() - timedelta(seconds=30)).isoformat()

        stale = repo._continuous_running_stale(
            "running",
            {"last_started_at": old_started_at, "running_stale_after_seconds": 60},
        )
        fresh = repo._continuous_running_stale(
            "running",
            {"last_started_at": recent_started_at, "running_stale_after_seconds": 60},
        )

        self.assertIsNotNone(stale)
        self.assertGreaterEqual(stale["elapsed_seconds"], 60)
        self.assertIsNone(fresh)

    def test_reading_stale_running_session_recovers_to_idle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            session_key = repo._default_continuous_session(tenant)
            old_started_at = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
            config = {"last_started_at": old_started_at, "running_stale_after_seconds": 60}
            with repo.metadata_engine_for(tenant).begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE aletheia_continuous_enrichment_sessions
                        SET status = 'running',
                            config_json = :config_json
                        WHERE project_id = :tenant_id AND session_key = :session_key
                        """
                    ),
                    {
                        "tenant_id": tenant.tenant_id,
                        "session_key": session_key,
                        "config_json": json.dumps(config),
                    },
                )

            session = repo.continuous_enrichment_session(tenant, session_key)["session"]

            self.assertEqual(session["status"], "idle")
            self.assertEqual(session["config"]["stop_reason"], "recovered stale running session")
            self.assertEqual(session["latest_events"][-1]["type"], "stale_running_recovered")

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

    def test_frontier_score_penalizes_weak_search_labels(self):
        repo = object.__new__(InstanceRepository)
        weak = {
            "key": "proposed-graph:edge:weak",
            "name": "second strait has_systemic_risk Iran",
            "source_kind": "new_graph_edge",
            "payload": {"source_label": "second strait", "target_label": "Iran", "relation": "has_systemic_risk"},
        }
        strong = {
            "key": "proposed-graph:edge:strong",
            "name": "Iran depends on Strait of Hormuz",
            "source_kind": "new_graph_edge",
            "payload": {"source_label": "Iran", "target_label": "Strait of Hormuz", "relation": "depends_on"},
        }

        weak_score = repo._continuous_frontier_score(weak, {})["score"]
        strong_score = repo._continuous_frontier_score(strong, {})["score"]

        self.assertGreater(strong_score, weak_score)

    def test_frontier_score_boosts_objective_matched_instance_without_hardcoded_label(self):
        repo = object.__new__(InstanceRepository)
        matched = {
            "key": "instance-coverage:Country:XYZ",
            "name": "XYZ Country enrichment coverage",
            "source_kind": "instance_coverage",
            "payload": {"label": "XYZ", "ontology_type": "Country", "identity_key": "Country:XYZ"},
        }
        unrelated = {
            "key": "instance-coverage:Country:ABC",
            "name": "ABC Country enrichment coverage",
            "source_kind": "instance_coverage",
            "payload": {"label": "ABC", "ontology_type": "Country", "identity_key": "Country:ABC"},
        }

        config = {"_frontier_selection_objective": "expand Country XYZ coverage"}
        matched_score = repo._continuous_frontier_score(matched, config)["score"]
        unrelated_score = repo._continuous_frontier_score(unrelated, config)["score"]

        self.assertGreater(matched_score, unrelated_score)

    def test_llm_frontier_rerank_can_reorder_valid_shortlist(self):
        candidates = [
            {"key": "frontier:a", "name": "A", "source_kind": "graph_coverage", "priority": 20},
            {"key": "frontier:b", "name": "B", "source_kind": "graph_coverage", "priority": 20},
            {"key": "frontier:c", "name": "C", "source_kind": "graph_coverage", "priority": 20},
        ]
        repo = self._repo_with_candidates(candidates)
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()

        def rerank(_tenant, _objective, shortlist, _max_frontier, _config):
            by_key = {item["key"]: item for item in shortlist}
            return [by_key["frontier:c"], by_key["frontier:a"]], {"planner": "llm", "selected_keys": ["frontier:c", "frontier:a"]}

        repo._continuous_llm_rerank_frontier = rerank
        selected = repo._continuous_frontier_for_cycle(
            tenant,
            [],
            {"frontier_selector": "llm_with_fallback", "_frontier_selection_objective": "expand evidence"},
            2,
        )

        self.assertEqual([item["key"] for item in selected], ["frontier:c", "frontier:a"])

    def test_llm_frontier_rerank_applies_to_stored_frontier_queue(self):
        repo = self._repo_with_candidates([])
        tenant = type("Tenant", (), {"tenant_id": "tenant-a"})()
        stored_frontier = [
            {"key": "queue:first", "name": "Weak queued", "source_kind": "graph_coverage", "priority": 10},
            {"key": "queue:second", "name": "Strong queued", "source_kind": "graph_coverage", "priority": 10},
        ]

        def rerank(_tenant, _objective, shortlist, _max_frontier, _config):
            by_key = {item["key"]: item for item in shortlist}
            return [by_key["queue:second"]], {"planner": "llm", "selected_keys": ["queue:second"]}

        config = {"frontier_selector": "llm_with_fallback", "_frontier_selection_objective": "expand evidence"}
        repo._continuous_llm_rerank_frontier = rerank
        selected = repo._continuous_frontier_for_cycle(tenant, stored_frontier, config, 1)

        self.assertEqual([item["key"] for item in selected], ["queue:second"])
        self.assertEqual(config["_frontier_selection_trace"]["planner"], "llm")
        self.assertEqual(config["_frontier_selection_trace"]["candidate_source"], "stored_frontier_with_dynamic_backfill")

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
