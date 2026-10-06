import json
import tempfile
import unittest
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from aletheia.interfaces.api.server import InstanceRepository, _ontology_type_reference_check
from aletheia.ontology import registry as ontology_registry
from aletheia.ontology.store import ensure_artifact_schema
from aletheia.core.tenant_registry import TenantConfig, TenantRegistry


class OntologyTypeReferenceCheckPureFunctionTest(unittest.TestCase):
    def test_returns_none_when_no_relevant_fields(self):
        self.assertIsNone(_ontology_type_reference_check({"label": "Close Waterway"}, {"waterway"}))

    def test_flags_unknown_names(self):
        result = _ontology_type_reference_check({"target_object_types": ["Waterway", "Spaceship"]}, {"waterway"})
        self.assertTrue(result["has_unknown"])
        self.assertEqual(
            {entry["name"]: entry["known"] for entry in result["checked"]},
            {"Waterway": True, "Spaceship": False},
        )

    def test_all_known_names_are_not_flagged(self):
        result = _ontology_type_reference_check({"applies_to": ["Waterway"]}, {"waterway"})
        self.assertFalse(result["has_unknown"])

    def test_policy_applies_to_can_reference_an_action_name(self):
        """A policy's applies_to can legitimately name an action label, not
        an object type -- the known-names set passed in must be the union
        across node types, edge types, and actions, not node types alone."""
        result = _ontology_type_reference_check(
            {"applies_to": ["Close Waterway"]}, {"close waterway"},
        )
        self.assertFalse(result["has_unknown"])

    def test_dedupes_repeated_names_across_fields(self):
        result = _ontology_type_reference_check(
            {"applies_to": ["Waterway"], "target_object_types": ["Waterway"]}, {"waterway"},
        )
        self.assertEqual(len(result["checked"]), 1)

    def test_case_insensitive_match(self):
        result = _ontology_type_reference_check({"applies_to": ["WATERWAY"]}, {"waterway"})
        self.assertFalse(result["has_unknown"])


class KnownOntologyNamesIntegrationTest(unittest.TestCase):
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

    def test_known_names_union_across_nodes_edges_and_actions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            engine = repo.metadata_engine_for(tenant)
            ensure_artifact_schema(engine)
            Session = sessionmaker(bind=engine)
            with Session() as session:
                ontology_registry.propose_node_type(
                    session, tenant_id=tenant.tenant_id, name="Waterway", status="approved",
                )
                ontology_registry.propose_edge_type(
                    session, tenant_id=tenant.tenant_id, name="CONNECTS_TO",
                    domain=["Waterway"], range=["Waterway"], status="approved",
                )
                ontology_registry.propose_action(
                    session, tenant_id=tenant.tenant_id, name="Close Waterway",
                    applies_to=["Waterway"], trigger_event="disruption", status="approved",
                )
                # A draft action must NOT count as known -- only approved references are trusted.
                ontology_registry.propose_action(
                    session, tenant_id=tenant.tenant_id, name="Draft Only Action",
                    applies_to=["Waterway"], trigger_event="x", status="draft",
                )
                session.commit()

            known_names = repo._known_ontology_names(tenant)
            self.assertEqual(known_names, {"waterway", "connects_to", "close waterway"})

    def test_proposed_graph_elements_attaches_type_reference_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, tenant = self._sqlite_tenant_repo(tmpdir)
            engine = repo.metadata_engine_for(tenant)
            ensure_artifact_schema(engine)
            Session = sessionmaker(bind=engine)
            with Session() as session:
                ontology_registry.propose_node_type(
                    session, tenant_id=tenant.tenant_id, name="Waterway", status="approved",
                )
                session.commit()

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
                            (:project_id, 'type-ref-check-test', 'IterativeGraphEnrichmentAgent',
                             'completed', 'test type reference check', '[]', '[]',
                             '{}', '{}', '[]', 1, 0, 0, :started_at)
                        """
                    ),
                    {"project_id": tenant.tenant_id, "started_at": datetime.utcnow()},
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO aletheia_proposed_graph_elements
                            (run_id, project_id, element_key, element_type, name,
                             payload_json, evidence_refs_json, source_url,
                             confidence, status, iteration, created_at)
                        VALUES
                            (:run_id, :project_id, :element_key, 'ontology_concept', 'Close Waterway',
                             :payload_json, '["gpt_researcher://report/waterway"]',
                             'gpt_researcher://report/waterway', 0.8, 'draft', 1, :created_at)
                        """
                    ),
                    {
                        "run_id": run_result.lastrowid,
                        "project_id": tenant.tenant_id,
                        "element_key": "proposed-graph:tenant-a:ontology-concept:close-waterway",
                        "payload_json": json.dumps(
                            {
                                "artifact_type": "action",
                                "ontology_part": "action",
                                "label": "Close Waterway",
                                "target_object_types": ["Waterway", "Spaceship"],
                                "evidence_quote": "Authorities may close the waterway.",
                            }
                        ),
                        "created_at": datetime.utcnow(),
                    },
                )

            result = repo.proposed_graph_elements(tenant, status_filter="all")
            elements = {item["name"]: item for item in result["elements"]}
            check = elements["Close Waterway"]["type_reference_check"]
            self.assertTrue(check["has_unknown"])
            self.assertEqual(
                {entry["name"]: entry["known"] for entry in check["checked"]},
                {"Waterway": True, "Spaceship": False},
            )


if __name__ == "__main__":
    unittest.main()
