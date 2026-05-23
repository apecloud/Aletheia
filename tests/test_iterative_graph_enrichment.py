import json
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

from agents.iterative_graph_enrichment_agent import (  # noqa: E402
    GraphDeepResearchBenchmark,
    IterativeGraphEnrichmentAgent,
)
from agents.ontology_artifacts import OntologyArtifact, ensure_artifact_schema, upsert_artifact  # noqa: E402


class IterativeGraphEnrichmentTest(unittest.TestCase):
    def _seed_db(self, tmpdir: str):
        db_url = f"sqlite:///{Path(tmpdir) / 'metadata.db'}"
        engine = create_engine(db_url)
        ensure_artifact_schema(engine)
        Session = sessionmaker(bind=engine)
        session = Session()
        upsert_artifact(
            session,
            artifact_type="object",
            natural_key="chokepoint",
            name="Chokepoint",
            description="Maritime chokepoint object.",
            payload={"name": "Chokepoint", "source_table": "maritime_chokepoint_risk_indicators"},
            source_refs=["table:maritime_chokepoint_risk_indicators"],
            source_agent="test",
            project_id="maritime-risk",
            status="draft",
        )
        session.commit()
        before = session.query(OntologyArtifact).filter_by(project_id="maritime-risk").count()
        session.close()
        return db_url, before

    def _fixture(self, tmpdir: str):
        path = Path(tmpdir) / "search.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "title": "Bab el-Mandeb Strait conflict risk affects CHN IND USA trade",
                        "url": "https://zenodo.org/records/13841882",
                        "snippet": (
                            "likelihood_conflict and severity_conflict around Bab el-Mandeb Strait expose "
                            "CHN IND USA to trade_at_risk_v and trade_impacted; analyst review action required."
                        ),
                    },
                    {
                        "title": "Untrusted maritime claim",
                        "url": "https://example.org/untrusted",
                        "snippet": "Hormuz Strait sanctions impact",
                    },
                ]
            ),
            encoding="utf-8",
        )
        return str(path)

    def test_iterative_run_creates_proposed_graph_without_canonical_writes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, before_count = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=2,
            )
            before_fp = agent.graph_fingerprint()
            result = agent.run("discover hazard chokepoint country trade action paths")
            after_fp = agent.graph_fingerprint()

            proposed = result["proposed_graph"]
            self.assertGreaterEqual(result["run"]["proposed_count"], 3)
            self.assertTrue(any(item["element_type"] == "node" for item in proposed))
            self.assertTrue(any(item["element_type"] == "edge" for item in proposed))
            self.assertTrue(any(item["element_type"] == "finding" for item in proposed))
            self.assertTrue(all(item["evidence_refs"] for item in proposed))
            self.assertEqual(result["run"]["skipped_sources"][0]["reason"], "blocked_domain_not_allowlisted")
            self.assertEqual(before_fp["ontology_artifacts"], after_fp["ontology_artifacts"])
            self.assertEqual(before_count, 1)

    def test_benchmark_reads_baseline_as_comparison_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
            )
            result = agent.run("discover hazard chokepoint country trade action paths")
            benchmark = GraphDeepResearchBenchmark(db_url, tenant="maritime-risk").compare(
                result["run"]["run_key"],
                {
                    "summary": "A mainstream deep research style report on chokepoint trade exposure.",
                    "claims": ["Bab el-Mandeb disruption can affect Asian importers."],
                    "sources": ["https://zenodo.org/records/13841882"],
                    "mentions_multi_hop": True,
                    "recommended_actions": ["Monitor shipping chokepoints."],
                },
            )
            self.assertTrue(benchmark["boundary"]["baseline_is_comparison_artifact_only"])
            self.assertFalse(benchmark["boundary"]["baseline_writes_to_proposed_graph"])
            self.assertIn("traceability", benchmark["comparison"]["dimensions"])
            self.assertGreaterEqual(benchmark["comparison"]["summary"]["aletheia_complete_deep_graph_findings"], 1)


if __name__ == "__main__":
    unittest.main()
