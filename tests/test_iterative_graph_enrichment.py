import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "agents"))

import agents.iterative_graph_enrichment_agent as iterative_graph_enrichment_agent  # noqa: E402
from agents.iterative_graph_enrichment_agent import (  # noqa: E402
    GRAPH_EXTRACTION_PROMPT_VERSION,
    GraphDeepResearchBenchmark,
    IterativeGraphEnrichmentAgent,
)
from agents.ontology_artifacts import (  # noqa: E402
    GraphIdentityIndex,
    IterativeGraphEnrichmentRun,
    OntologyArtifact,
    ProposedGraphElement,
    ensure_artifact_schema,
    upsert_artifact,
)


class StaticTestEmbeddingAdapter:
    model_name = "test-multilingual-mini"

    def embed(self, text: str):
        lowered = (text or "").lower()
        normalized = lowered.replace("-", " ")
        if "model unavailable" in lowered:
            return {
                "status": "degraded",
                "reason": "test_model_unavailable",
                "model": self.model_name,
                "vector": None,
                "dim": 0,
            }
        if "edge | country" in normalized and "bab el mandeb strait" in normalized:
            vector = [0.0, 0.0, 0.7, 0.7]
        if "node | chokepoint" in normalized and "bab el mandeb strait" in normalized:
            vector = [0.0, 0.0, 1.0, 0.0]
        elif "node | chokepoint" in normalized and "bab el mandeb" in normalized:
            vector = [0.0, 0.0, 0.82, 0.57]
        elif "中国" in text or "china" in lowered or "chn" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        elif "india" in lowered or "ind" in lowered or "印度" in text:
            vector = [0.0, 1.0, 0.0, 0.0]
        elif "bab el mandeb strait" in normalized:
            vector = [0.0, 0.0, 1.0, 0.0]
        elif "bab el mandeb" in normalized:
            vector = [0.0, 0.0, 0.82, 0.57]
        else:
            vector = [0.0, 0.0, 0.0, 1.0]
        return {
            "status": "ready",
            "model": self.model_name,
            "vector": vector,
            "dim": len(vector),
        }


class DegradedTestEmbeddingAdapter:
    model_name = "test-unavailable-mini"

    def embed(self, text: str):
        return {
            "status": "degraded",
            "reason": "test_model_unavailable",
            "model": self.model_name,
            "vector": None,
            "dim": 0,
        }


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
            natural_key="country",
            name="Country",
            description="Country node inferred from source schema.",
            payload={
                "object_name": "Country",
                "mapped_table_names": ["maritime_chokepoint_country_dependencies"],
                "properties": ["iso3", "country_code"],
                "llm_inferred": True,
                "prompt_version": "schema_graph_modeling_v1",
            },
            source_refs=["table:maritime_chokepoint_country_dependencies"],
            source_agent="SchemaGraphModelingAgent",
            project_id="maritime-risk",
            status="approved",
        )
        upsert_artifact(
            session,
            artifact_type="link",
            natural_key="trade_dependency",
            name="trade_dependency",
            description="Country exposure through a maritime chokepoint inferred from source schema.",
            payload={
                "source_object_key": "country",
                "target_object_key": "chokepoint",
                "relation": "trade_dependency",
                "properties": ["trade_at_risk_v", "trade_impacted", "dependency_share"],
                "llm_inferred": True,
                "prompt_version": "schema_graph_modeling_v1",
            },
            source_refs=["table:maritime_chokepoint_country_dependencies"],
            source_agent="SchemaGraphModelingAgent",
            project_id="maritime-risk",
            status="approved",
        )
        upsert_artifact(
            session,
            artifact_type="object",
            natural_key="chokepoint",
            name="Chokepoint",
            description="Maritime chokepoint node inferred from source schema.",
            payload={
                "object_name": "Chokepoint",
                "mapped_table_names": ["maritime_chokepoint_risk_indicators"],
                "properties": ["chokepoint_name"],
                "llm_inferred": True,
                "prompt_version": "schema_graph_modeling_v1",
            },
            source_refs=["table:maritime_chokepoint_risk_indicators"],
            source_agent="SchemaGraphModelingAgent",
            project_id="maritime-risk",
            status="approved",
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

    def _langextract_runner(self, source_text, _schema_context):
        def interval(text):
            start = source_text.find(text)
            if start < 0:
                return None
            return SimpleNamespace(start_pos=start, end_pos=start + len(text))

        extractions = []
        if "CHN" in source_text:
            extractions.append(
                SimpleNamespace(
                    extraction_class="graph_node",
                    extraction_text="CHN",
                    attributes={"schema_node_key": "country", "node_type": "Country", "confidence": 0.86},
                    char_interval=interval("CHN"),
                )
            )
        if "Bab el-Mandeb Strait" in source_text:
            extractions.append(
                SimpleNamespace(
                    extraction_class="graph_node",
                    extraction_text="Bab el-Mandeb Strait",
                    attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint", "confidence": 0.87},
                    char_interval=interval("Bab el-Mandeb Strait"),
                )
            )
        if "CHN" in source_text and "Bab el-Mandeb Strait" in source_text:
            relation_text = "expose CHN"
            extractions.append(
                SimpleNamespace(
                    extraction_class="graph_relation",
                    extraction_text=relation_text,
                    attributes={
                        "source_label": "CHN",
                        "target_label": "Bab el-Mandeb Strait",
                        "relation_label": "trade_dependency",
                        "confidence": 0.78,
                    },
                    char_interval=interval(relation_text),
                )
            )
            for metric in ("trade_at_risk_v", "trade_impacted"):
                if metric in source_text:
                    extractions.append(
                        SimpleNamespace(
                            extraction_class="graph_metric",
                            extraction_text=metric,
                            attributes={"source_label": "CHN", "target_label": "Bab el-Mandeb Strait", "metric_key": metric},
                            char_interval=interval(metric),
                        )
                    )
        return SimpleNamespace(extractions=extractions)

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
                langextract_runner=self._langextract_runner,
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
            self.assertEqual(before_count, 3)
            for item in proposed:
                payload = item["payload"]
                self.assertTrue(payload.get("task_id"))
                self.assertTrue(payload.get("run_id"))
                self.assertTrue(payload.get("frontier_id"))
                self.assertTrue(payload.get("candidate_id"))
                self.assertIn(
                    payload.get("dedup_decision"),
                    {"new_proposal", "merge_existing", "needs_review", "duplicate_existing_proposal", "duplicate_current_run"},
                )
                self.assertIn("review_required", payload)
                if item["element_type"] in {"node", "edge"}:
                    self.assertTrue(payload.get("identity"))
                    self.assertTrue(payload.get("identity_key"))

    def test_crawled_evidence_extracts_typed_graph_semantics(self):
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
                langextract_runner=self._langextract_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )

            result = agent.run("extract ontology node edge property description from maritime evidence")
            proposed = result["proposed_graph"]
            nodes = [item for item in proposed if item["element_type"] == "node"]
            edges = [item for item in proposed if item["element_type"] == "edge"]
            trade_edges = [item for item in edges if item["payload"].get("relation") == "trade_dependency"]

            self.assertTrue(nodes)
            self.assertTrue(trade_edges)
            for node in nodes:
                payload = node["payload"]
                self.assertEqual(payload["extraction"]["prompt_version"], GRAPH_EXTRACTION_PROMPT_VERSION)
                self.assertTrue(payload.get("description"))
                self.assertTrue(payload.get("properties"))
                self.assertTrue(payload.get("evidence_quote"))
                self.assertEqual(payload["extraction"].get("extraction_source"), "structured_llm_contract")
                self.assertEqual(
                    payload["extraction"].get("schema_context", {}).get("projection_source"),
                    "SchemaGraphModelingAgent",
                )
                self.assertEqual(payload.get("ontology_candidate", {}).get("schema_projection_source"), "SchemaGraphModelingAgent")
            for edge in trade_edges:
                payload = edge["payload"]
                self.assertEqual(payload["source_type"], "Country")
                self.assertEqual(payload["target_type"], "Chokepoint")
                self.assertTrue(payload.get("description"))
                self.assertTrue(payload.get("properties", {}).get("fact_node_hint"))
                self.assertIn("trade_at_risk_v", payload.get("metrics", []))
                self.assertEqual(payload.get("relation_ontology_candidate", {}).get("schema_projection_source"), "SchemaGraphModelingAgent")
            trace = result["run"]["expansion_trace"][0]
            self.assertEqual(trace["extraction_prompt_version"], GRAPH_EXTRACTION_PROMPT_VERSION)
            self.assertIn("ontology_candidates", trace["extraction_contract"]["outputs"])
            self.assertTrue(trace["last_extraction_profile"]["quality"]["has_properties"])
            self.assertTrue(trace["last_extraction_profile"]["quality"]["has_descriptions"])
            self.assertTrue(trace["last_extraction_profile"]["ontology_candidates"])

    def test_missing_schema_projection_does_not_write_dictionary_semantics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{Path(tmpdir) / 'metadata.db'}"
            engine = create_engine(db_url)
            ensure_artifact_schema(engine)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=self._langextract_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            frontier = {
                "key": "object:chokepoint",
                "name": "Chokepoint",
                "artifact_type": "object",
                "source": "test",
                "depth": 0,
            }

            result = agent.run("extract proposed graph without approved schema", frontier_items=[frontier])
            trace = result["run"]["expansion_trace"][0]
            extraction = trace["last_extraction_profile"]

            self.assertEqual(result["run"]["proposed_count"], 0)
            self.assertEqual(extraction["extraction_source"], "structured_llm_contract")
            self.assertEqual(extraction["schema_context"]["projection_source"], "none")
            self.assertEqual(
                extraction["rejected_or_ambiguous_candidates"][0]["reason"],
                "no_approved_schema_graph_projection",
            )

    def test_unmapped_relation_is_preserved_for_review_when_edge_type_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url = f"sqlite:///{Path(tmpdir) / 'metadata.db'}"
            engine = create_engine(db_url)
            ensure_artifact_schema(engine)
            Session = sessionmaker(bind=engine)
            session = Session()
            for natural_key, name, properties in (
                ("country", "Country", ["iso3", "country_code"]),
                ("chokepoint", "Chokepoint", ["chokepoint_name"]),
            ):
                upsert_artifact(
                    session,
                    artifact_type="object",
                    natural_key=natural_key,
                    name=name,
                    description=f"{name} node inferred from source schema.",
                    payload={
                        "object_name": name,
                        "mapped_table_names": [f"{natural_key}_source"],
                        "properties": properties,
                        "llm_inferred": True,
                        "prompt_version": "schema_graph_modeling_v1",
                    },
                    source_refs=[f"table:{natural_key}_source"],
                    source_agent="SchemaGraphModelingAgent",
                    project_id="maritime-risk",
                    status="approved",
                )
            session.commit()
            session.close()
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=self._langextract_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            frontier = {
                "key": "object:chokepoint",
                "name": "Chokepoint",
                "artifact_type": "object",
                "source": "test",
                "depth": 0,
            }

            result = agent.run("extract relation review gate without approved edge", frontier_items=[frontier])
            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            rejected = extraction["rejected_or_ambiguous_candidates"]

            self.assertFalse([item for item in result["proposed_graph"] if item["element_type"] == "edge"])
            self.assertTrue(rejected)
            self.assertEqual(rejected[0]["reason"], "unmapped_relation")
            self.assertEqual(rejected[0]["review_status"], "needs_review")
            self.assertTrue(rejected[0]["review_required"])
            self.assertIn("source_label", rejected[0])
            self.assertIn("target_label", rejected[0])
            self.assertTrue(rejected[0]["relation_label"])
            self.assertTrue(rejected[0]["evidence_quote"])
            self.assertEqual(rejected[0]["source_ref"], "https://zenodo.org/records/13841882")

    def test_langextract_runner_feeds_structured_contract(self):
        def char_interval(start, end):
            return SimpleNamespace(start_pos=start, end_pos=end)

        def fake_langextract_runner(_source_text, _schema_context):
            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="AAA",
                        attributes={"schema_node_key": "country", "node_type": "Country", "confidence": 0.91},
                        char_interval=char_interval(12, 15),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Delta Passage",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint", "confidence": 0.92},
                        char_interval=char_interval(32, 45),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="depends through",
                        attributes={
                            "source_label": "AAA",
                            "target_label": "Delta Passage",
                            "relation_label": "trade_dependency",
                            "confidence": 0.88,
                        },
                        char_interval=char_interval(16, 31),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_metric",
                        extraction_text="trade_at_risk_v",
                        attributes={"source_label": "AAA", "target_label": "Delta Passage", "metric_key": "trade_at_risk_v"},
                        char_interval=char_interval(55, 70),
                    ),
                ]
            )

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
                langextract_runner=fake_langextract_runner,
            )

            result = agent.run("extract via langextract")
            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            edge = next(
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "edge"
                and item["payload"].get("source_label") == "AAA"
                and item["payload"].get("target_label") == "Delta Passage"
            )

            self.assertEqual(extraction["extraction_engine"], "google/langextract")
            self.assertEqual(extraction["extraction_engine_status"], "runner")
            self.assertEqual(edge["payload"]["relation"], "trade_dependency")
            self.assertIn("trade_at_risk_v", edge["payload"]["metrics"])
            self.assertTrue(edge["payload"]["source_grounding"])
            self.assertTrue(
                any(
                    item.get("extraction_text") == "depends through"
                    and item.get("char_interval", {}).get("start_pos") == 16
                    for item in edge["payload"]["source_grounding"]
                )
            )
            node = next(
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "node" and item["payload"].get("label") == "AAA"
            )
            self.assertEqual(node["payload"]["source_grounding"][0]["extraction_text"], "AAA")
            self.assertEqual(node["payload"]["source_grounding"][0]["char_interval"]["start_pos"], 12)

    def test_no_langextract_key_blocks_production_heuristic_proposals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            with patch.dict(
                "os.environ",
                {
                    "LANGEXTRACT_API_KEY": "",
                    "GEMINI_API_KEY": "",
                    "GOOGLE_API_KEY": "",
                    "ALETHEIA_DISABLE_DOTENV_API_KEYS": "1",
                },
            ):
                agent = IterativeGraphEnrichmentAgent(
                    db_url,
                    tenant="maritime-risk",
                    search_results_json=self._fixture(tmpdir),
                    allowed_domains=["zenodo.org"],
                    max_iterations=1,
                    max_frontier=1,
                    max_results_per_query=1,
                )
                result = agent.run("no-key must not write heuristic proposed graph")

            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            self.assertEqual(result["run"]["proposed_count"], 0)
            self.assertEqual(extraction["extraction_engine"], "google/langextract")
            self.assertEqual(extraction["extraction_engine_status"], "api_key_missing")
            self.assertEqual(extraction["rejected_or_ambiguous_candidates"][0]["reason"], "langextract_api_key_missing")
            self.assertFalse(extraction["nodes"])
            self.assertFalse(extraction["edges"])

    def test_langextract_key_can_be_read_from_configured_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("GEMINI_API_KEY=gemini-from-env-file\n", encoding="utf-8")
            iterative_graph_enrichment_agent._DOTENV_CACHE = None
            try:
                with patch.dict(
                    "os.environ",
                    {
                        "LANGEXTRACT_API_KEY": "",
                        "GEMINI_API_KEY": "",
                        "GOOGLE_API_KEY": "",
                        "ALETHEIA_ENV_FILE": str(env_file),
                    },
                ):
                    self.assertEqual(
                        iterative_graph_enrichment_agent._configured_api_key(
                            "LANGEXTRACT_API_KEY",
                            "GEMINI_API_KEY",
                            "GOOGLE_API_KEY",
                        ),
                        "gemini-from-env-file",
                    )
            finally:
                iterative_graph_enrichment_agent._DOTENV_CACHE = None

    def test_relation_alias_direction_and_metric_normalization_use_schema_metadata(self):
        def fake_runner(_source_text, _schema_context):
            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="CHN",
                        attributes={"schema_node_key": "country", "node_type": "Country"},
                        char_interval=SimpleNamespace(start_pos=10, end_pos=13),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Bab el-Mandeb Strait",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint"},
                        char_interval=SimpleNamespace(start_pos=28, end_pos=48),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="exposes",
                        attributes={
                            "source_label": "Bab el-Mandeb Strait",
                            "target_label": "CHN",
                            "relation_label": "trade dependency",
                        },
                        char_interval=SimpleNamespace(start_pos=49, end_pos=56),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_metric",
                        extraction_text="trade at risk v",
                        attributes={
                            "source_label": "Bab el-Mandeb Strait",
                            "target_label": "CHN",
                            "metric_key": "trade at risk v",
                        },
                        char_interval=SimpleNamespace(start_pos=60, end_pos=75),
                    ),
                ]
            )

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
                langextract_runner=fake_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            result = agent.run("normalize relation semantics")
            edge = next(item for item in result["proposed_graph"] if item["element_type"] == "edge")

            self.assertEqual(edge["payload"]["source_label"], "CHN")
            self.assertEqual(edge["payload"]["target_label"], "Bab el-Mandeb Strait")
            self.assertEqual(edge["payload"]["relation"], "trade_dependency")
            self.assertEqual(edge["payload"]["properties"]["relation_direction"], "reversed")
            self.assertIn("trade_at_risk_v", edge["payload"]["metrics"])
            self.assertTrue(
                any(
                    item.get("extraction_text") == "exposes"
                    for item in edge["payload"]["source_grounding"]
                )
            )

    def test_unmapped_relation_with_approved_edge_goes_to_review_not_edge(self):
        def fake_runner(_source_text, _schema_context):
            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="CHN",
                        attributes={"schema_node_key": "country", "node_type": "Country"},
                        char_interval=SimpleNamespace(start_pos=0, end_pos=3),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Bab el-Mandeb Strait",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint"},
                        char_interval=SimpleNamespace(start_pos=12, end_pos=32),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="mentions an unapproved blockade relation",
                        attributes={
                            "source_label": "CHN",
                            "target_label": "Bab el-Mandeb Strait",
                            "relation_label": "unapproved blockade relation",
                        },
                        char_interval=SimpleNamespace(start_pos=33, end_pos=70),
                    ),
                ]
            )

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
                langextract_runner=fake_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            result = agent.run("unmapped relation review gate")
            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            rejected = extraction["rejected_or_ambiguous_candidates"]

            self.assertFalse([item for item in result["proposed_graph"] if item["element_type"] == "edge"])
            self.assertTrue(any(item.get("reason") == "ambiguous_relation" for item in rejected))
            ambiguous = next(item for item in rejected if item.get("reason") == "ambiguous_relation")
            self.assertEqual(ambiguous["review_status"], "needs_review")
            self.assertTrue(ambiguous["review_required"])
            self.assertEqual(ambiguous["relation_label"], "unapproved blockade relation")

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
                langextract_runner=self._langextract_runner,
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

    def test_graph_context_query_plan_uses_frontier_path_terms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            frontier = {
                "key": "proposed-graph:edge:chn-bab-el-mandeb",
                "name": "CHN depends on Bab el-Mandeb Strait",
                "artifact_type": "proposed_edge",
                "source": "proposed_graph",
                "path": "CHN -> depends_on -> Bab el-Mandeb Strait -> trade_at_risk_v",
                "payload": {
                    "source_type": "Country",
                    "target_type": "Chokepoint",
                    "source_label": "CHN",
                    "target_label": "Bab el-Mandeb Strait",
                    "relation": "depends_on",
                    "metrics": ["trade_at_risk_v"],
                },
            }
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=2,
                langextract_runner=self._langextract_runner,
            )

            plan = agent._query_plan_for_frontier(frontier, "discover maritime trade exposure")
            query = plan["query"]
            self.assertIn("CHN", query)
            self.assertIn("China", query)
            self.assertIn("Bab el-Mandeb Strait", query)
            self.assertIn("maritime chokepoint", query)
            self.assertIn("trade disruption", query)
            self.assertEqual(plan["graph_context_used"]["relation"], "depends_on")
            self.assertIn("CHN", plan["graph_context_used"]["neighbor_nodes"])
            self.assertIn("Bab el-Mandeb Strait", plan["path_context_used"]["path_label"])
            self.assertIn("trade_at_risk_v", plan["query_terms"]["metrics"])

    def test_run_trace_records_graph_aware_query_provenance(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            frontier = {
                "key": "proposed-graph:edge:chn-bab-el-mandeb",
                "name": "CHN depends on Bab el-Mandeb Strait",
                "artifact_type": "proposed_edge",
                "source": "proposed_graph",
                "path": "CHN -> depends_on -> Bab el-Mandeb Strait -> trade_at_risk_v",
                "payload": {
                    "source_type": "Country",
                    "target_type": "Chokepoint",
                    "source_label": "CHN",
                    "target_label": "Bab el-Mandeb Strait",
                    "relation": "depends_on",
                    "metrics": ["trade_at_risk_v"],
                },
            }
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=2,
                langextract_runner=self._langextract_runner,
            )
            result = agent.run("discover maritime trade exposure", frontier_items=[frontier])
            trace = result["run"]["expansion_trace"][0]

            self.assertIn("China", trace["query"])
            self.assertIn("trade disruption", trace["query"])
            self.assertEqual(trace["graph_context_used"]["relation"], "depends_on")
            self.assertEqual(trace["path_context_used"]["source_label"], "CHN")
            self.assertIn("query_terms", result["run"]["skipped_sources"][0])
            self.assertIn("CHN", result["run"]["skipped_sources"][0]["query_terms"]["countries"])

    def test_rerun_same_frontier_does_not_duplicate_proposed_nodes_or_edges(self):
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
                langextract_runner=self._langextract_runner,
            )
            first = agent.run("discover hazard chokepoint country trade action paths")
            first_fp = agent.graph_fingerprint()
            second = agent.run("discover hazard chokepoint country trade action paths")
            second_fp = agent.graph_fingerprint()

            self.assertEqual(first_fp["proposed_graph_elements"], second_fp["proposed_graph_elements"])
            self.assertFalse([item for item in second["proposed_graph"] if item["element_type"] == "node"])
            self.assertEqual(
                {
                    item["payload"].get("identity_key")
                    for item in first["proposed_graph"]
                    if item["element_type"] == "edge"
                },
                {
                    item["payload"].get("identity_key")
                    for item in second["proposed_graph"]
                    if item["element_type"] == "edge"
                },
            )
            self.assertTrue(
                any(
                    item["payload"].get("dedup_decision") == "duplicate_existing_proposal"
                    and item["payload"].get("matched_status") == "proposed"
                    for item in second["proposed_graph"]
                    if item["element_type"] == "edge"
                )
            )
            self.assertTrue(
                any(
                    item.get("reason") == "duplicate_endpoint_node_not_proposed"
                    for item in second["run"]["skipped_sources"]
                    if item.get("element_type") == "node"
                )
            )

    def test_duplicate_endpoint_nodes_are_edge_context_not_node_proposals(self):
        def runner_for_metric(metric_key):
            def fake_runner(source_text, _schema_context):
                def interval(text):
                    start = source_text.find(text)
                    if start < 0:
                        return None
                    return SimpleNamespace(start_pos=start, end_pos=start + len(text))

                return SimpleNamespace(
                    extractions=[
                        SimpleNamespace(
                            extraction_class="graph_node",
                            extraction_text="CHN",
                            attributes={"schema_node_key": "country", "node_type": "Country", "confidence": 0.86},
                            char_interval=interval("CHN"),
                        ),
                        SimpleNamespace(
                            extraction_class="graph_node",
                            extraction_text="Bab el-Mandeb Strait",
                            attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint", "confidence": 0.87},
                            char_interval=interval("Bab el-Mandeb Strait"),
                        ),
                        SimpleNamespace(
                            extraction_class="graph_relation",
                            extraction_text="exposes",
                            attributes={
                                "source_label": "CHN",
                                "target_label": "Bab el-Mandeb Strait",
                                "relation_label": "trade_dependency",
                                "confidence": 0.78,
                            },
                            char_interval=interval("exposes"),
                        ),
                        SimpleNamespace(
                            extraction_class="graph_metric",
                            extraction_text=metric_key,
                            attributes={
                                "source_label": "CHN",
                                "target_label": "Bab el-Mandeb Strait",
                                "metric_key": metric_key,
                            },
                            char_interval=interval(metric_key),
                        ),
                    ]
                )

            return fake_runner

        def metric_fixture(tmpdir, metric_key):
            path = Path(tmpdir) / f"{metric_key}.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "title": f"CHN Bab el-Mandeb Strait {metric_key}",
                            "url": f"https://zenodo.org/records/{metric_key}",
                            "snippet": f"CHN exposes Bab el-Mandeb Strait with {metric_key}.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return str(path)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            first_agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=metric_fixture(tmpdir, "trade_at_risk_v"),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=runner_for_metric("trade_at_risk_v"),
                embedding_adapter=DegradedTestEmbeddingAdapter(),
            )
            first = first_agent.run("discover first maritime edge")
            self.assertTrue([item for item in first["proposed_graph"] if item["element_type"] == "node"])

            second_agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=metric_fixture(tmpdir, "trade_impacted"),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=runner_for_metric("trade_impacted"),
                embedding_adapter=DegradedTestEmbeddingAdapter(),
            )
            second = second_agent.run("discover second maritime edge with same endpoints")
            nodes = [item for item in second["proposed_graph"] if item["element_type"] == "node"]
            edges = [item for item in second["proposed_graph"] if item["element_type"] == "edge"]

            self.assertFalse(nodes)
            self.assertEqual(len(edges), 1)
            edge_payload = edges[0]["payload"]
            self.assertEqual(edge_payload["dedup_decision"], "new_proposal")
            self.assertEqual(edge_payload.get("endpoint_review_required"), False)
            endpoint_evidence = edge_payload.get("endpoint_dedup_evidence") or {}
            self.assertEqual(endpoint_evidence.get("source", {}).get("dedup_decision"), "duplicate_existing_proposal")
            self.assertEqual(endpoint_evidence.get("target", {}).get("dedup_decision"), "duplicate_existing_proposal")
            self.assertFalse(endpoint_evidence.get("source", {}).get("proposed_node_created"))
            self.assertFalse(endpoint_evidence.get("target", {}).get("proposed_node_created"))
            self.assertTrue(endpoint_evidence.get("source", {}).get("matched_node_key"))
            self.assertTrue(endpoint_evidence.get("target", {}).get("matched_node_key"))
            self.assertTrue(
                any(
                    item.get("reason") == "duplicate_endpoint_node_not_proposed"
                    for item in second["run"]["skipped_sources"]
                    if item.get("element_type") == "node"
                )
            )

    def test_persistent_identity_index_is_populated_and_reused(self):
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
                langextract_runner=self._langextract_runner,
            )
            first = agent.run("discover hazard chokepoint country trade action paths")
            first_snapshot = agent.identity_index_snapshot()

            self.assertTrue(first["run"]["safety_profile"]["identity_dedup"]["persistent_identity_index"])
            self.assertGreater(first_snapshot["identity_index_count"], 0)
            self.assertTrue(
                all(
                    row["source_space"] in {"approved_ontology_artifact", "proposed_graph"}
                    for row in first_snapshot["identity_index"]
                )
            )
            self.assertEqual(
                first_snapshot["identity_index_count"],
                len({row["identity_key"] for row in first_snapshot["identity_index"]}),
            )

            second = agent.run("discover hazard chokepoint country trade action paths")
            second_snapshot = agent.identity_index_snapshot()
            self.assertEqual(first_snapshot["identity_index_count"], second_snapshot["identity_index_count"])
            self.assertTrue(
                any(
                    item["payload"].get("matched_source") == "proposed_graph"
                    and item["payload"].get("dedup_decision") == "duplicate_existing_proposal"
                    for item in second["proposed_graph"]
                    if item["element_type"] in {"node", "edge"}
                )
            )

            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                proposed_identity_count = len(
                    [
                        row
                        for row in first_snapshot["identity_index"]
                        if row["source_space"] == "proposed_graph"
                    ]
                )
                self.assertEqual(
                    session.query(GraphIdentityIndex)
                    .filter_by(project_id="maritime-risk", source_space="proposed_graph")
                    .count(),
                    proposed_identity_count,
                )
            finally:
                session.close()

    def test_vector_dedup_matches_cross_language_entity_without_changing_candidate_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                existing_identity = {
                    "kind": "node",
                    "entity_type": "Country",
                    "label": "中国",
                    "normalized_label": "",
                    "aliases": [],
                    "source_identity": None,
                    "property_fingerprint": "zh-cn-country",
                }
                agent._upsert_identity_index_row(
                    session,
                    identity=existing_identity,
                    identity_key="node:maritime-risk:country:zh-cn:legacy",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:node:china-zh",
                    source_status="draft",
                    payload={"label": "中国", "description": "国家 中国"},
                )
                session.commit()
            finally:
                session.close()

            candidate = {
                "element_type": "node",
                "name": "China",
                "payload": {
                    "ontology_type": "Country",
                    "label": "China",
                    "description": "Country China",
                    "properties": {},
                },
                "evidence_refs": ["source:en"],
                "source_url": "https://example.org/china",
                "confidence": 0.8,
                "iteration": 1,
            }
            session = agent.Session()
            try:
                identity_index = agent._identity_index(session)
            finally:
                session.close()
            before = agent._annotate_candidate_identity(
                candidate,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )
            after = agent._annotate_candidate_identity(
                {**candidate, "payload": {**candidate["payload"], "description": "Updated English description"}},
                task_id="task-b",
                run_id="run-b",
                frontier_id="frontier-b",
                candidate_seq=2,
                identity_index=identity_index,
            )

            self.assertEqual(before["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(before["payload"]["match_method"], "vector_embedding")
            self.assertEqual(before["payload"]["matched_node_key"], "proposed-graph:maritime-risk:node:china-zh")
            self.assertEqual(before["payload"]["embedding_model"], "test-multilingual-mini")
            self.assertEqual(before["payload"]["vector_distance"], 0.0)
            self.assertTrue(before["payload"]["vector_top_k"])
            self.assertEqual(before["payload"]["candidate_id"], after["payload"]["candidate_id"])
            self.assertEqual(before["payload"]["identity_key"], after["payload"]["identity_key"])

    def test_vector_dedup_keeps_different_entity_as_new_proposal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "China",
                        "normalized_label": "china",
                        "aliases": [],
                        "source_identity": None,
                        "property_fingerprint": "china",
                    },
                    identity_key="node:maritime-risk:country:china:legacy",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:node:china",
                    source_status="draft",
                    payload={"label": "China"},
                )
                session.commit()
            finally:
                session.close()

            session = agent.Session()
            try:
                identity_index = agent._identity_index(session)
            finally:
                session.close()
            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "India",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "India",
                        "description": "Country India",
                        "properties": {},
                    },
                    "evidence_refs": ["source:en"],
                    "source_url": "https://example.org/india",
                    "confidence": 0.8,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "new_proposal")
            self.assertEqual(candidate["payload"]["match_method"], "vector_embedding")
            self.assertGreater(candidate["payload"]["vector_distance"], 0.24)
            self.assertEqual(candidate["payload"]["merge_decision_source"], "vector_embedding_distance")

    def test_vector_dedup_edge_structural_conflict_requires_review_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "edge",
                        "source_type": "Country",
                        "target_type": "Chokepoint",
                        "source_node": "chn",
                        "target_node": "bab el mandeb strait",
                        "relation": "trade_dependency",
                        "source_identity": "trade_at_risk_v",
                        "property_fingerprint": "trade-edge",
                    },
                    identity_key="edge:maritime-risk:chn:trade_dependency:bab:trade_at_risk_v",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:edge:trade",
                    source_status="draft",
                    payload={
                        "source_type": "Country",
                        "source_label": "CHN",
                        "relation": "trade_dependency",
                        "target_type": "Chokepoint",
                        "target_label": "Bab el-Mandeb Strait",
                        "metrics": ["trade_at_risk_v"],
                    },
                )
                session.commit()
            finally:
                session.close()

            candidate = {
                "element_type": "edge",
                "name": "CHN security presence Bab el-Mandeb Strait",
                "payload": {
                    "source_type": "Country",
                    "source_label": "CHN",
                    "relation": "security_presence",
                    "target_type": "Chokepoint",
                    "target_label": "Bab el-Mandeb Strait",
                    "description": "CHN security presence near Bab el-Mandeb Strait",
                    "properties": {"metrics": ["military_presence_score"]},
                    "metrics": ["military_presence_score"],
                },
                "evidence_refs": ["source:security"],
                "source_url": "https://example.org/security",
                "confidence": 0.82,
                "iteration": 1,
            }
            session = agent.Session()
            try:
                identity_index = agent._identity_index(session)
            finally:
                session.close()
            annotated = agent._annotate_candidate_identity(
                candidate,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(annotated["payload"]["match_method"], "vector_embedding")
            self.assertEqual(annotated["payload"]["decision_reason"], "structural_conflict")
            self.assertEqual(annotated["payload"]["dedup_decision"], "needs_review")
            self.assertFalse(annotated["payload"]["structure_compatible"])
            self.assertIn("relation", annotated["payload"]["conflict_fields"])
            self.assertIn("source_identity", annotated["payload"]["conflict_fields"])
            self.assertNotEqual(annotated["payload"]["dedup_decision"], "duplicate_existing_proposal")

    def test_embedding_unavailable_degrades_without_lexical_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=DegradedTestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "China",
                        "normalized_label": "china",
                        "aliases": [],
                        "source_identity": None,
                        "property_fingerprint": "china",
                    },
                    identity_key="node:maritime-risk:country:china:legacy",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:node:china",
                    source_status="draft",
                    payload={"label": "China"},
                )
                session.commit()
            finally:
                session.close()

            session = agent.Session()
            try:
                identity_index = agent._identity_index(session)
            finally:
                session.close()
            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "Chine",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "Chine",
                        "description": "Model unavailable near text",
                        "properties": {},
                    },
                    "evidence_refs": ["source:fr"],
                    "source_url": "https://example.org/chine",
                    "confidence": 0.8,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "new_proposal")
            self.assertEqual(candidate["payload"]["match_method"], "embedding_degraded")
            self.assertTrue(candidate["payload"]["embedding_degraded"])
            self.assertEqual(candidate["payload"]["merge_decision_source"], "embedding_unavailable_degraded")
            self.assertFalse(candidate["payload"]["llm_merge_decision_allowed"])

    def test_rebuild_identity_index_indexes_existing_approved_and_proposed_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            upsert_artifact(
                session,
                artifact_type="object",
                natural_key="country-chn",
                name="Country",
                description="Approved country node.",
                payload={"name": "Country", "label": "CHN", "source_id": "Country:CHN"},
                source_refs=["source:approved-country"],
                source_agent="test",
                project_id="maritime-risk",
                status="approved",
            )
            prior_run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="proposed-run",
                objective="existing proposed node",
                status="completed",
            )
            session.add(prior_run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=prior_run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:bab",
                    element_type="node",
                    name="Bab el-Mandeb Strait",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "Chokepoint",
                            "label": "Bab el-Mandeb Strait",
                            "properties": {"source_id": "Chokepoint:BAB"},
                        }
                    ),
                    evidence_refs_json=json.dumps(["source:proposed"]),
                    confidence=0.7,
                    status="draft",
                )
            )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(db_url, tenant="maritime-risk")
            rebuilt = agent.rebuild_identity_index()
            self.assertEqual(rebuilt["identity_index_count"], 5)
            self.assertEqual(
                {row["source"] for row in rebuilt["identity_index"]},
                {"approved_ontology_artifact", "proposed_graph"},
            )

            snapshot = agent.identity_index_snapshot()
            self.assertEqual(snapshot["identity_index_count"], 5)
            self.assertEqual(
                {row["source_space"] for row in snapshot["identity_index"]},
                {"approved_ontology_artifact", "proposed_graph"},
            )

    def test_approved_node_match_merges_without_new_proposal_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            prior_run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="approved-run",
                objective="approved node",
                status="completed",
            )
            session.add(prior_run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=prior_run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:approved-chn",
                    element_type="node",
                    name="CHN",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "Country",
                            "label": "CHN",
                            "identity": {
                                "kind": "node",
                                "entity_type": "Country",
                                "label": "CHN",
                                "normalized_label": "chn",
                                "aliases": [],
                                "source_identity": "Country:CHN",
                                "source_refs": [],
                                "property_fingerprint": "approved",
                            },
                        }
                    ),
                    evidence_refs_json="[]",
                    confidence=0.9,
                    status="approved",
                )
            )
            session.commit()
            before_count = session.query(ProposedGraphElement).filter_by(project_id="maritime-risk").count()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=self._langextract_runner,
            )
            result = agent.run("discover hazard chokepoint country trade action paths")
            after_fp = agent.graph_fingerprint()

            self.assertEqual(after_fp["proposed_graph_elements"], before_count + result["run"]["proposed_count"])
            self.assertTrue(
                any(
                    item.get("dedup_decision") == "merge_existing"
                    and item.get("matched_node_key") == "proposed-graph:maritime-risk:node:approved-chn"
                    for item in result["run"]["skipped_sources"]
                )
            )

    def test_same_run_duplicate_candidate_is_skipped_in_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            duplicate_fixture = Path(tmpdir) / "duplicate-search.json"
            duplicate_fixture.write_text(
                json.dumps(
                    [
                        {
                            "title": "Bab el-Mandeb Strait conflict risk affects CHN trade",
                            "url": "https://zenodo.org/records/13841882",
                            "snippet": "Bab el-Mandeb Strait exposes CHN to trade_at_risk_v and trade_impacted.",
                        },
                        {
                            "title": "Second Bab el-Mandeb Strait evidence affects CHN trade",
                            "url": "https://zenodo.org/records/13841883",
                            "snippet": "Bab el-Mandeb Strait exposes CHN to trade_at_risk_v and trade_impacted.",
                        },
                    ]
                ),
                encoding="utf-8",
            )
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=str(duplicate_fixture),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=2,
                langextract_runner=self._langextract_runner,
            )
            result = agent.run("discover hazard chokepoint country trade action paths")

            self.assertTrue(
                any(
                    item.get("dedup_decision") == "duplicate_current_run"
                    for item in result["run"]["skipped_sources"]
                )
            )
            node_identity_keys = [
                item["payload"].get("identity_key")
                for item in result["proposed_graph"]
                if item["element_type"] == "node"
            ]
            self.assertEqual(len(node_identity_keys), len(set(node_identity_keys)))

    def test_candidate_id_uses_graph_identity_not_occurrence_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(db_url, tenant="maritime-risk")
            base_node = {
                "element_type": "node",
                "name": "Bab el-Mandeb Strait",
                "payload": {
                    "ontology_type": "Chokepoint",
                    "label": "Bab el-Mandeb Strait",
                    "description": "Initial description",
                    "properties": {"source_id": ["b-source", "a-source"], "aliases": ["Bab", "Mandeb"]},
                    "evidence_quote": "first quote",
                },
                "evidence_refs": ["source:b", "source:a"],
                "source_url": "https://zenodo.org/records/1",
                "confidence": 0.71,
                "iteration": 1,
            }

            first = agent._annotate_candidate_identity(
                base_node,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=[],
            )
            occurrence_variant = {
                **base_node,
                "payload": {
                    **base_node["payload"],
                    "description": "Changed description",
                    "evidence_quote": "changed quote",
                    "properties": {"source_id": ["a-source", "b-source"], "aliases": ["Mandeb", "Bab"]},
                },
                "evidence_refs": ["source:a", "source:b"],
                "confidence": 0.94,
            }
            second = agent._annotate_candidate_identity(
                occurrence_variant,
                task_id="task-b",
                run_id="run-b",
                frontier_id="frontier-b",
                candidate_seq=99,
                identity_index=[],
            )

            self.assertEqual(first["payload"]["candidate_id"], second["payload"]["candidate_id"])
            self.assertNotEqual(first["payload"]["source_fingerprint"], second["payload"]["source_fingerprint"])
            self.assertNotEqual(first["payload"]["evidence_fingerprint"], second["payload"]["evidence_fingerprint"])
            self.assertNotEqual(first["payload"]["audit_context"]["frontier_id"], second["payload"]["audit_context"]["frontier_id"])

            type_variant = {
                **base_node,
                "payload": {**base_node["payload"], "ontology_type": "Port"},
            }
            label_variant = {
                **base_node,
                "name": "Hormuz Strait",
                "payload": {**base_node["payload"], "label": "Hormuz Strait"},
            }
            changed_type = agent._annotate_candidate_identity(
                type_variant,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=2,
                identity_index=[],
            )
            changed_label = agent._annotate_candidate_identity(
                label_variant,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=3,
                identity_index=[],
            )

            self.assertNotEqual(first["payload"]["candidate_id"], changed_type["payload"]["candidate_id"])
            self.assertNotEqual(first["payload"]["candidate_id"], changed_label["payload"]["candidate_id"])

    def test_edge_candidate_id_changes_only_when_graph_edge_identity_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(db_url, tenant="maritime-risk")
            base_edge = {
                "element_type": "edge",
                "name": "CHN trade dependency Bab el-Mandeb Strait",
                "payload": {
                    "source_type": "Country",
                    "source_label": "CHN",
                    "relation": "trade_dependency",
                    "target_type": "Chokepoint",
                    "target_label": "Bab el-Mandeb Strait",
                    "description": "Initial edge",
                    "properties": {
                        "fact_node_hint": "trade_dependency:CHN::Bab el-Mandeb Strait",
                        "metrics": ["trade_at_risk_v"],
                    },
                    "metrics": ["trade_at_risk_v"],
                    "evidence_quote": "first quote",
                },
                "evidence_refs": ["source:b", "source:a"],
                "source_url": "https://zenodo.org/records/1",
                "confidence": 0.72,
                "iteration": 1,
            }

            first = agent._annotate_candidate_identity(
                base_edge,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=[],
            )
            occurrence_variant = {
                **base_edge,
                "payload": {**base_edge["payload"], "description": "Changed edge", "evidence_quote": "changed quote"},
                "evidence_refs": ["source:a", "source:b"],
                "confidence": 0.88,
            }
            second = agent._annotate_candidate_identity(
                occurrence_variant,
                task_id="task-b",
                run_id="run-b",
                frontier_id="frontier-b",
                candidate_seq=2,
                identity_index=[],
            )

            self.assertEqual(first["payload"]["candidate_id"], second["payload"]["candidate_id"])
            source_url_variant = {
                **base_edge,
                "source_url": "https://zenodo.org/records/2",
                "evidence_refs": ["source:c"],
            }
            third = agent._annotate_candidate_identity(
                source_url_variant,
                task_id="task-c",
                run_id="run-c",
                frontier_id="frontier-c",
                candidate_seq=3,
                identity_index=[],
            )

            self.assertEqual(first["payload"]["candidate_id"], third["payload"]["candidate_id"])

            no_stable_fact_source_a = {
                "element_type": "edge",
                "name": "KOR has country dependency Hormuz Strait",
                "payload": {
                    "source_type": "Country",
                    "source_label": "KOR",
                    "relation": "has_country_dependency",
                    "target_type": "Maritime Chokepoint",
                    "target_label": "Hormuz Strait",
                    "properties": {},
                },
                "source_url": "https://zenodo.org/records/a",
                "evidence_refs": ["https://zenodo.org/records/a"],
            }
            no_stable_fact_source_b = {
                **no_stable_fact_source_a,
                "source_url": "https://zenodo.org/records/b",
                "evidence_refs": ["https://zenodo.org/records/b"],
            }
            no_stable_a = agent._annotate_candidate_identity(
                no_stable_fact_source_a,
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=4,
                identity_index=[],
            )
            no_stable_b = agent._annotate_candidate_identity(
                no_stable_fact_source_b,
                task_id="task-b",
                run_id="run-b",
                frontier_id="frontier-b",
                candidate_seq=5,
                identity_index=[],
            )

            self.assertEqual(no_stable_a["payload"]["candidate_id"], no_stable_b["payload"]["candidate_id"])
            self.assertEqual(no_stable_a["payload"]["identity_key"], no_stable_b["payload"]["identity_key"])

            for changed_payload in (
                {"source_label": "IND"},
                {"target_label": "Hormuz Strait"},
                {"relation": "raises_risk_for"},
                {"metrics": ["dependency_share"], "properties": {"metrics": ["dependency_share"]}},
            ):
                edge = {
                    **base_edge,
                    "payload": {**base_edge["payload"], **changed_payload},
                }
                changed = agent._annotate_candidate_identity(
                    edge,
                    task_id="task-a",
                    run_id="run-a",
                    frontier_id="frontier-a",
                    candidate_seq=3,
                    identity_index=[],
                )
                self.assertNotEqual(first["payload"]["candidate_id"], changed["payload"]["candidate_id"])

    def test_ambiguous_existing_proposed_node_requires_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            prior_run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="prior-run",
                objective="prior ambiguous node",
                status="completed",
            )
            session.add(prior_run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=prior_run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:ambiguous-bab",
                    element_type="node",
                    name="Bab el Mandeb",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "Chokepoint",
                            "label": "Bab el Mandeb",
                            "properties": {},
                        }
                    ),
                    evidence_refs_json="[]",
                    confidence=0.6,
                    status="draft",
                )
            )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=self._fixture(tmpdir),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=self._langextract_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            result = agent.run("discover hazard chokepoint country trade action paths")
            chokepoint = next(
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "node" and item["payload"].get("label") == "Bab el-Mandeb Strait"
            )

            self.assertEqual(chokepoint["status"], "needs_more_evidence")
            self.assertEqual(chokepoint["payload"]["dedup_decision"], "needs_review")
            self.assertTrue(chokepoint["payload"]["review_required"])
            self.assertEqual(chokepoint["payload"]["matched_node_key"], "proposed-graph:maritime-risk:node:ambiguous-bab")
            self.assertGreaterEqual(chokepoint["payload"]["match_score"], 0.75)
            self.assertLess(chokepoint["payload"]["match_score"], 0.92)
            edge = next(item for item in result["proposed_graph"] if item["element_type"] == "edge")
            self.assertEqual(edge["status"], "needs_more_evidence")
            self.assertTrue(edge["payload"]["endpoint_review_required"])
            self.assertTrue(edge["payload"]["endpoint_dedup_evidence"]["target"]["review_required"])
            self.assertEqual(edge["payload"]["endpoint_decision_reason"], "endpoint_identity_needs_review")


if __name__ == "__main__":
    unittest.main()
