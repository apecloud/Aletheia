import json
import tempfile
import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

from agents.enrichment_loop_harness import evaluate_enrichment_loop, load_loop_config
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

    def test_next_focus_prioritizes_latency_before_coverage_repair(self):
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

        self.assertEqual(report["verdict"]["next_focus"], "stage_bottleneck_diagnosis")
        self.assertGreater(report["stage_latency_sec"]["semantic_extraction"], 60)


if __name__ == "__main__":
    unittest.main()
