import json
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from agents.enrichment_loop_harness import apply_repair_plan, evaluate_enrichment_loop, load_loop_config
from agents.ontology_artifacts import ensure_artifact_schema


class EnrichmentLoopHarnessTest(unittest.TestCase):
    def _db(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        url = f"sqlite:///{tmpdir.name}/metadata.db"
        engine = create_engine(url)
        ensure_artifact_schema(engine)
        return url, engine

    def _insert_run(self, engine, tenant="tenant-a", run_key="run-a", safety=None, status="completed"):
        now = datetime.utcnow()
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO aletheia_iterative_graph_enrichment_runs
                        (project_id, run_key, source_agent, status, objective,
                         frontier_json, expansion_trace_json, safety_profile_json,
                         budget_json, skipped_sources_json, proposed_count,
                         pruned_count, finding_count, started_at, finished_at)
                    VALUES
                        (:project_id, :run_key, 'IterativeGraphEnrichmentAgent',
                         :status, 'test loop', '[]', '[]', :safety_profile_json,
                         '{}', '[]', 0, 0, 0, :started_at, :finished_at)
                    """
                ),
                {
                    "project_id": tenant,
                    "run_key": run_key,
                    "status": status,
                    "safety_profile_json": json.dumps(safety or {}),
                    "started_at": now - timedelta(seconds=30),
                    "finished_at": now,
                },
            )
            return result.lastrowid

    def _insert_element(self, engine, run_id, payload, *, name, status="approved", element_type="ontology_concept", tenant="tenant-a"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO aletheia_proposed_graph_elements
                        (run_id, project_id, element_key, element_type, name,
                         payload_json, evidence_refs_json, source_url,
                         confidence, status, iteration, created_at)
                    VALUES
                        (:run_id, :project_id, :element_key, :element_type, :name,
                         :payload_json, '[]', 'gpt_researcher://report/test',
                         0.9, :status, 1, :created_at)
                    """
                ),
                {
                    "run_id": run_id,
                    "project_id": tenant,
                    "element_key": f"proposed-graph:{tenant}:{name.lower().replace(' ', '-')}",
                    "element_type": element_type,
                    "name": name,
                    "payload_json": json.dumps(payload),
                    "status": status,
                    "created_at": datetime.utcnow(),
                },
            )

    def test_evaluates_approved_ontology_coverage(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "MaritimeRegion"},
            name="MaritimeRegion",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Red Sea",
                "object_type": "MaritimeRegion",
            },
            name="Red Sea",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "link",
                "ontology_part": "relation",
                "source_label": "Red Sea",
                "source_object_type": "MaritimeRegion",
                "target_label": "Bab el-Mandeb Strait",
                "target_object_type": "Chokepoint",
                "relation": "contains_access_route_to",
            },
            name="Red Sea relation",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "property",
                "ontology_part": "property",
                "object_label": "Red Sea",
                "label": "risk_level",
            },
            name="Red Sea risk level",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["tenant_metrics"]["approved_classes"], 1)
        self.assertEqual(report["tenant_metrics"]["approved_concrete_objects"], 1)
        self.assertEqual(report["tenant_metrics"]["approved_relations"], 1)
        self.assertEqual(report["tenant_metrics"]["objects_with_relation"], 1)
        self.assertEqual(report["tenant_metrics"]["objects_with_property"], 1)

    def test_next_focus_prioritizes_unsupported_classes(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "UnsupportedRiskTheme"},
            name="UnsupportedRiskTheme",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "ontology_shape_repair")
        self.assertIn("approved classes exist without approved concrete objects", report["verdict"]["reasons"])

    def test_next_focus_keeps_latency_as_warning_when_coverage_repair_is_actionable(self):
        url, engine = self._db()
        base = datetime.utcnow()
        safety = {
            "runtime_trace": [
                {"type": "semantic_pass_start", "ts": base.isoformat()},
                {"type": "semantic_pass_done", "ts": (base + timedelta(seconds=120)).isoformat()},
            ]
        }
        run_id = self._insert_run(engine, safety=safety)
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "UnsupportedRiskTheme"},
            name="UnsupportedRiskTheme",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "ontology_shape_repair")
        self.assertGreater(report["stage_latency_sec"]["semantic_extraction"], 60)
        self.assertTrue(
            any(str(reason).startswith("latency warning:") for reason in report["verdict"]["reasons"])
        )

    def test_identity_index_rebuild_latency_does_not_block_coverage_focus(self):
        url, engine = self._db()
        base = datetime.utcnow()
        safety = {
            "runtime_trace": [
                {"type": "identity_index_start", "ts": base.isoformat()},
                {"type": "identity_index_read_start", "ts": (base + timedelta(seconds=1)).isoformat()},
                {"type": "identity_index_read_done", "ts": (base + timedelta(seconds=2)).isoformat()},
                {"type": "identity_index_rebuild_start", "ts": (base + timedelta(seconds=2)).isoformat()},
                {"type": "identity_index_rebuild_done", "ts": (base + timedelta(seconds=119)).isoformat()},
                {"type": "identity_index_done", "ts": (base + timedelta(seconds=120)).isoformat()},
            ]
        }
        run_id = self._insert_run(engine, safety=safety)
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "UnsupportedRiskTheme"},
            name="UnsupportedRiskTheme",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "ontology_shape_repair")
        self.assertGreater(report["stage_latency_sec"]["identity_index_rebuild"], 100)
        self.assertLess(report["stage_latency_sec"]["identity_index_read"], 3)

    def test_repair_plan_marks_only_unsupported_approved_classes(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "SupportedRegion"},
            name="SupportedRegion",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Red Sea",
                "object_type": "SupportedRegion",
            },
            name="Red Sea",
        )
        self._insert_element(
            engine,
            run_id,
            {"artifact_type": "class", "ontology_part": "abstract_class", "label": "UnsupportedRiskTheme"},
            name="UnsupportedRiskTheme",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["label"], "UnsupportedRiskTheme")

        repair = apply_repair_plan(url, "tenant-a", report["repair_plan"], reviewer="test")
        self.assertEqual(repair["applied"], 1)

        repaired_report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))
        self.assertEqual(repaired_report["tenant_metrics"]["unsupported_class_count"], 0)
        self.assertEqual(repaired_report["tenant_metrics"]["approved_classes"], 1)

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT status, payload_json
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = 'tenant-a' AND name = 'UnsupportedRiskTheme'
                    """
                )
            ).mappings().first()
        self.assertEqual(row["status"], "needs_more_evidence")
        self.assertEqual(json.loads(row["payload_json"])["review_events"][-1]["source"], "enrichment_loop_harness")

    def test_class_assignment_repair_uses_generic_text_match(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "class",
                "ontology_part": "abstract_class",
                "label": "Country",
                "description": "A sovereign nation or country explicitly named in the source.",
            },
            name="Country",
            status="needs_more_evidence",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Iran",
                "description": "A specific country involved in maritime chokepoint tensions.",
                "evidence_quote": "Iran threatened traffic near the strait.",
            },
            name="Iran",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["repair_policy"]["class_assignment"]["auto_apply_min_score"] = 4.0
        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "class_assignment")
        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["assigned_class"], "Country")
        self.assertEqual(report["repair_plan"]["items"][0]["verification"]["method"], "generic_text_evidence_overlap")
        self.assertTrue(report["repair_plan"]["items"][0]["verification"]["auto_apply_eligible"])

        repair = apply_repair_plan(url, "tenant-a", report["repair_plan"], reviewer="test")
        self.assertEqual(repair["applied"], 1)

        repaired_report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)
        self.assertEqual(repaired_report["tenant_metrics"]["object_with_class_ratio"], 1.0)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT payload_json
                    FROM aletheia_proposed_graph_elements
                    WHERE project_id = 'tenant-a' AND name = 'Iran'
                    """
                )
            ).mappings().first()
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["object_type"], "Country")
        self.assertEqual(payload["identity"]["entity_type"], "Country")
        self.assertEqual(payload["loop_repair"]["verification"]["method"], "generic_text_evidence_overlap")

    def test_relation_completion_plan_lists_isolated_objects(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Red Sea",
                "object_type": "Waterway",
            },
            name="Red Sea",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Gulf of Aden",
                "object_type": "Waterway",
            },
            name="Gulf of Aden",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "link",
                "ontology_part": "relation",
                "source_label": "Red Sea",
                "target_label": "Gulf of Aden",
                "relation": "connects_to",
            },
            name="connects_to",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Suez Canal",
                "object_type": "Canal",
            },
            name="Suez Canal",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "relation_completion")
        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["label"], "Suez Canal")
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["source"], "loop_harness_relation_completion")
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["source_kind"], "loop_harness_relation_completion")
        self.assertGreater(report["repair_plan"]["items"][0]["frontier_item"]["priority"], 0)

    def test_property_completion_plan_lists_objects_without_properties(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Red Sea",
                "object_type": "Waterway",
            },
            name="Red Sea",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Gulf of Aden",
                "object_type": "Waterway",
            },
            name="Gulf of Aden",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "link",
                "ontology_part": "relation",
                "source_label": "Red Sea",
                "target_label": "Gulf of Aden",
                "relation": "connects_to",
            },
            name="connects_to",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "property_completion")
        self.assertEqual(report["repair_plan"]["item_count"], 2)
        self.assertEqual(report["repair_plan"]["items"][0]["recommended_action"], "enrich_properties")
        self.assertEqual(report["repair_plan"]["items"][0]["frontier_item"]["source"], "loop_harness_property_completion")

    def test_relation_completion_prioritizes_clear_typed_objects(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Red Sea",
                "object_type": "Sea",
            },
            name="Red Sea",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Suez Canal",
                "object_type": "Canal",
            },
            name="Suez Canal",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "relation_completion")
        self.assertEqual(
            {item["label"] for item in report["repair_plan"]["items"]},
            {"Red Sea", "Suez Canal"},
        )

    def test_relation_completion_filters_domain_irrelevant_typed_objects_when_configured(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Vasco da Gama",
                "object_type": "Explorer",
                "description": "Portuguese explorer and nobleman known for a historical voyage to India.",
            },
            name="Vasco da Gama",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Hormuz Crisis",
                "object_type": "Crisis",
                "description": "Maritime chokepoint disruption risk affecting shipping and oil transit.",
            },
            name="Hormuz Crisis",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        config["repair_policy"]["relation_completion"] = {
            "domain_relevance": {
                "enabled": True,
                "min_score": 1.0,
                "positive_terms": ["maritime", "chokepoint", "shipping", "transit", "risk"],
                "negative_terms": ["explorer", "voyage", "nobleman"],
                "negative_object_types": ["Explorer"],
                "negative_object_type_weight": 8.0,
            }
        }

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "relation_completion")
        self.assertEqual([item["label"] for item in report["repair_plan"]["items"]], ["Hormuz Crisis"])
        self.assertGreater(report["repair_plan"]["items"][0]["domain_relevance"]["score"], 0)

    def test_domain_relevance_uses_phrase_boundaries_not_substrings(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Portuguese Navigator",
                "object_type": "Person",
                "description": "A Portuguese historical figure mentioned in a report.",
            },
            name="Portuguese Navigator",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        config["repair_policy"]["relation_completion"] = {
            "domain_relevance": {
                "enabled": True,
                "min_score": 1.0,
                "positive_terms": ["port"],
                "negative_terms": [],
                "negative_object_types": [],
            }
        }

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "relation_completion")
        self.assertEqual(report["repair_plan"]["items"], [])

    def test_relation_completion_can_fallback_to_neutral_typed_objects_when_starved(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Hormuz Crisis",
                "object_type": "Crisis",
                "description": "A named approved object with no relation endpoint yet.",
            },
            name="Hormuz Crisis",
            status="approved",
        )
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Historical Navigator",
                "object_type": "Explorer",
                "description": "Explorer biography with historical voyage context.",
            },
            name="Historical Navigator",
            status="approved",
        )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        config["repair_policy"]["relation_completion"] = {
            "domain_relevance": {
                "enabled": True,
                "min_score": 1.0,
                "allow_neutral_fallback_when_starved": True,
                "positive_terms": ["maritime"],
                "negative_terms": ["explorer", "voyage"],
                "negative_object_types": ["Explorer"],
            }
        }

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        self.assertEqual(report["verdict"]["next_focus"], "relation_completion")
        self.assertEqual([item["label"] for item in report["repair_plan"]["items"]], ["Hormuz Crisis"])
        self.assertTrue(report["repair_plan"]["items"][0]["domain_relevance"]["fallback_used"])

    def test_domain_relevance_reads_url_encoded_source_terms(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Hormuz Crisis",
                "object_type": "Crisis",
            },
            name="Hormuz Crisis",
            status="approved",
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET source_url = 'gpt_researcher://report/Yemen%20Bab%20el%20Mandeb%20Strait%20maritime%20chokepoint'
                    WHERE project_id = 'tenant-a' AND name = 'Hormuz Crisis'
                    """
                )
            )

        config = load_loop_config(None)
        config["coverage_targets"]["unsupported_class_count_max"] = 10
        config["coverage_targets"]["unclassified_object_ratio_max"] = 1.0
        config["repair_policy"]["relation_completion"] = {
            "domain_relevance": {
                "enabled": True,
                "min_score": 1.0,
                "positive_terms": ["maritime", "chokepoint", "strait"],
                "negative_terms": [],
                "negative_object_types": [],
            }
        }

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=config)

        relevance = report["repair_plan"]["items"][0]["domain_relevance"]
        self.assertTrue(relevance["eligible"])
        self.assertIn("maritime", relevance["positive_hits"])
        self.assertIn("chokepoint", relevance["positive_hits"])

    def test_quality_repair_demotes_self_typed_concrete_objects(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Global Shipping",
                "object_type": "GlobalShipping",
            },
            name="Global Shipping",
            status="approved",
        )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "concrete_object_quality_repair")
        self.assertEqual(report["repair_plan"]["item_count"], 1)
        self.assertEqual(report["repair_plan"]["items"][0]["quality_issues"][0]["code"], "self_typed_label")

        repair = apply_repair_plan(url, "tenant-a", report["repair_plan"], reviewer="test")
        self.assertEqual(repair["applied"], 1)

        repaired_report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))
        self.assertEqual(repaired_report["tenant_metrics"]["approved_concrete_objects"], 0)

    def test_quality_repair_flags_polluted_research_query_source(self):
        url, engine = self._db()
        run_id = self._insert_run(engine)
        self._insert_element(
            engine,
            run_id,
            {
                "artifact_type": "object",
                "ontology_part": "concrete_object",
                "label": "Chinese Customs Data",
                "object_type": "DataSource",
            },
            name="Chinese Customs Data",
            status="approved",
        )
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE aletheia_proposed_graph_elements
                    SET source_url = 'gpt_researcher://report/Topic%20Alpha%20XYZ%20Alpha'
                    WHERE project_id = 'tenant-a' AND name = 'Chinese Customs Data'
                    """
                )
            )

        report = evaluate_enrichment_loop(url, "tenant-a", run_key="run-a", config=load_loop_config(None))

        self.assertEqual(report["verdict"]["next_focus"], "concrete_object_quality_repair")
        issue_codes = [item["code"] for item in report["repair_plan"]["items"][0]["quality_issues"]]
        self.assertIn("polluted_research_query", issue_codes)


if __name__ == "__main__":
    unittest.main()
