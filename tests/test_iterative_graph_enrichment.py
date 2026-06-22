import json
import os
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
    GPTResearcherSearchProvider,
    IterativeGraphEnrichmentAgent,
    _configure_gpt_researcher_env,
    _graph_context_query_plan,
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
        if "node | country" in normalized and "similarland alpha" in normalized:
            vector = [1.0, 0.0, 0.0, 0.0]
        elif "node | country" in normalized and "similarland beta" in normalized:
            vector = [0.7, 0.714, 0.0, 0.0]
        elif "finding |" in normalized and ("hormuz" in normalized or "energy disruption" in normalized):
            vector = [0.6, 0.6, 0.0, 0.0]
        elif "edge | country" in normalized and "bab el mandeb strait" in normalized:
            vector = [0.0, 0.0, 0.7, 0.7]
        elif "node | chokepoint" in normalized and "bab el mandeb strait" in normalized:
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


class FakeGPTResearcher:
    def __init__(self, query, **_kwargs):
        self.query = query

    async def conduct_research(self):
        return {
            "query": self.query,
            "sources": [
                {"url": "https://zenodo.org/records/13841882", "title": "Maritime chokepoint source"},
            ],
        }

    async def write_report(self, **_kwargs):
        return (
            "Bab el-Mandeb Strait conflict risk affects CHN trade exposure. "
            "The evidence says disruption can expose CHN to trade_at_risk_v and trade_impacted; "
            "analyst review action required. Source: https://zenodo.org/records/13841882"
        )


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


class OrthogonalShortAliasEmbeddingAdapter:
    model_name = "test-orthogonal-short-alias-mini"

    def embed(self, text: str):
        lowered = (text or "").lower()
        if "node | country | us | us" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        else:
            vector = [0.0, 1.0, 0.0, 0.0]
        return {
            "status": "ready",
            "model": self.model_name,
            "vector": vector,
            "dim": len(vector),
        }


class TypeArtifactNearestEmbeddingAdapter:
    model_name = "test-type-artifact-nearest-mini"

    def embed(self, text: str):
        lowered = (text or "").lower()
        if "node | country | us | us" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        elif "node | country | country | country" in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        else:
            vector = [0.0, 1.0, 0.0, 0.0]
        return {
            "status": "ready",
            "model": self.model_name,
            "vector": vector,
            "dim": len(vector),
        }


class WrongVectorNeighborEmbeddingAdapter:
    model_name = "test-wrong-vector-neighbor-mini"

    def embed(self, text: str):
        lowered = (text or "").lower()
        if "strait of hormuz" in lowered and "maritimechokepoint" not in lowered:
            vector = [1.0, 0.0, 0.0, 0.0]
        elif "lombok strait" in lowered:
            vector = [0.99, 0.01, 0.0, 0.0]
        elif "strait of hormuz" in lowered and "maritimechokepoint" in lowered:
            vector = [0.85, 0.15, 0.0, 0.0]
        else:
            vector = [0.0, 1.0, 0.0, 0.0]
        return {
            "status": "ready",
            "model": self.model_name,
            "vector": vector,
            "dim": len(vector),
        }


class IterativeGraphEnrichmentTest(unittest.TestCase):
    def setUp(self):
        self._old_disable_research_semantic_llm = os.environ.get("ALETHEIA_DISABLE_RESEARCH_SEMANTIC_LLM")
        os.environ["ALETHEIA_DISABLE_RESEARCH_SEMANTIC_LLM"] = "1"

    def tearDown(self):
        if self._old_disable_research_semantic_llm is None:
            os.environ.pop("ALETHEIA_DISABLE_RESEARCH_SEMANTIC_LLM", None)
        else:
            os.environ["ALETHEIA_DISABLE_RESEARCH_SEMANTIC_LLM"] = self._old_disable_research_semantic_llm

    def test_edge_identity_uses_canonical_endpoint_dedup_evidence(self):
        candidate = {
            "element_type": "edge",
            "name": "Iran depends on Hormuz",
            "payload": {
                "source_type": "Country",
                "source_label": "Iran",
                "target_type": "Chokepoint",
                "target_label": "Hormuz",
                "relation": "depends_on",
                "endpoint_dedup_evidence": {
                    "source": {
                        "matched_node_key": "Country:IRN",
                        "matched_space": "approved_graph",
                    },
                    "target": {
                        "matched_node_key": "Chokepoint:Strait of Hormuz",
                        "matched_space": "approved_graph",
                    },
                },
            },
        }

        identity = iterative_graph_enrichment_agent._candidate_identity_payload(candidate)
        identity_key = iterative_graph_enrichment_agent._identity_key("maritime-risk", identity)

        self.assertEqual(identity["source_canonical_key"], "country irn")
        self.assertEqual(identity["target_canonical_key"], "chokepoint strait of hormuz")
        self.assertIn("country irn", identity_key)
        self.assertIn("chokepoint strait of hormuz", identity_key)
        self.assertIn("depends on:country irn::chokepoint strait of hormuz", identity["source_identity"])

    def test_edge_with_approved_canonical_endpoints_merges_existing_approved_edge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            approved_identity = iterative_graph_enrichment_agent._candidate_identity_payload(
                {
                    "element_type": "edge",
                    "name": "USA depends on Strait of Hormuz",
                    "payload": {
                        "source_type": "Country",
                        "source_label": "USA",
                        "target_type": "Chokepoint",
                        "target_label": "Strait of Hormuz",
                        "relation": "depends_on",
                        "properties": {"schema_edge_key": "link:depends_on"},
                    },
                    "evidence_refs": ["approved"],
                }
            )
            identity_index = [
                {
                    "node_key": "Country:USA->Chokepoint:Strait of Hormuz:link:depends_on",
                    "status": "approved",
                    "source": "approved_graph_instance",
                    "identity": approved_identity,
                    "identity_key": iterative_graph_enrichment_agent._identity_key("maritime-risk", approved_identity),
                    "payload": {},
                }
            ]

            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "edge",
                    "name": "US depends on Hormuz",
                    "payload": {
                        "source_type": "Country",
                        "source_label": "US",
                        "target_type": "Chokepoint",
                        "target_label": "Hormuz",
                        "relation": "depends_on",
                        "description": "Evidence says the US depends on Hormuz.",
                        "endpoint_dedup_evidence": {
                            "source": {
                                "matched_node_key": "Country:USA",
                                "matched_space": "approved_graph",
                                "dedup_decision": "merge_existing",
                            },
                            "target": {
                                "matched_node_key": "Chokepoint:Strait of Hormuz",
                                "matched_space": "approved_graph",
                                "dedup_decision": "merge_existing",
                            },
                        },
                    },
                    "evidence_refs": ["https://example.org/hormuz"],
                    "source_url": "https://example.org/hormuz",
                    "confidence": 0.7,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

        self.assertEqual(candidate["payload"]["dedup_decision"], "merge_existing")
        self.assertEqual(candidate["status"], "draft")
        self.assertEqual(candidate["payload"]["match_method"], "approved_canonical_edge")
        self.assertEqual(candidate["payload"]["matched_source"], "approved_graph_instance")
        self.assertEqual(
            candidate["payload"]["matched_node_key"],
            "Country:USA->Chokepoint:Strait of Hormuz:link:depends_on",
        )
        self.assertEqual(candidate["payload"]["decision_reason"], "approved canonical edge already exists")

    def test_proposed_node_index_transitively_prefers_approved_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="node-alias-run",
                objective="node alias",
                status="completed",
            )
            session.add(run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:hormuz",
                    element_type="node",
                    name="Hormuz",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "Chokepoint",
                            "label": "Hormuz",
                            "dedup_decision": "merge_existing",
                            "matched_node_key": "Chokepoint:Strait of Hormuz",
                            "matched_source": "approved_graph_instance",
                            "matched_status": "approved",
                        }
                    ),
                    evidence_refs_json=json.dumps([]),
                    source_url="https://example.test/hormuz",
                    confidence=0.82,
                    status="draft",
                )
            )
            session.commit()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            indexed = agent._proposed_identity_index(session)
            session.close()

        row = next(item for item in indexed if item["identity"].get("label") == "Hormuz")
        self.assertEqual(row["node_key"], "Chokepoint:Strait of Hormuz")
        self.assertEqual(row["source"], "approved_graph_instance")
        self.assertEqual(row["status"], "approved")

    def test_cleanup_duplicate_edges_uses_approved_endpoint_aliases(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="cleanup-run",
                objective="cleanup duplicate edges",
                status="completed",
            )
            session.add(run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:hormuz",
                    element_type="node",
                    name="Hormuz",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "Chokepoint",
                            "label": "Hormuz",
                            "dedup_decision": "merge_existing",
                            "matched_node_key": "Chokepoint:Strait of Hormuz",
                            "matched_source": "approved_graph_instance",
                            "matched_status": "approved",
                        }
                    ),
                    evidence_refs_json=json.dumps([]),
                    confidence=0.82,
                    status="draft",
                )
            )
            direct_payload = {
                "source_type": "Country",
                "source_label": "Iran",
                "target_type": "Chokepoint",
                "target_label": "Strait of Hormuz",
                "relation": "depends_on",
                "endpoint_dedup_evidence": {
                    "source": {"matched_node_key": "Country:IRN", "matched_space": "approved_graph"},
                    "target": {"matched_node_key": "Chokepoint:Strait of Hormuz", "matched_space": "approved_graph"},
                },
            }
            alias_payload = {
                "source_type": "Country",
                "source_label": "Iran",
                "target_type": "Chokepoint",
                "target_label": "Hormuz",
                "relation": "depends_on",
                "endpoint_dedup_evidence": {
                    "source": {"matched_node_key": "Country:IRN", "matched_space": "approved_graph"},
                    "target": {
                        "matched_node_key": "proposed-graph:maritime-risk:node:hormuz",
                        "matched_space": "proposed_graph",
                    },
                },
            }
            session.add_all(
                [
                    ProposedGraphElement(
                        run_id=run.id,
                        project_id="maritime-risk",
                        element_key="proposed-graph:maritime-risk:edge:direct",
                        element_type="edge",
                        name="Iran depends on Strait of Hormuz",
                        payload_json=json.dumps(direct_payload),
                        evidence_refs_json=json.dumps([]),
                        confidence=0.7,
                        status="draft",
                    ),
                    ProposedGraphElement(
                        run_id=run.id,
                        project_id="maritime-risk",
                        element_key="proposed-graph:maritime-risk:edge:alias",
                        element_type="edge",
                        name="Iran depends on Hormuz",
                        payload_json=json.dumps(alias_payload),
                        evidence_refs_json=json.dumps([]),
                        confidence=0.78,
                        status="needs_more_evidence",
                    ),
                ]
            )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            cleanup = agent.cleanup_duplicate_proposed_edges()

            session = Session()
            try:
                alias = session.query(ProposedGraphElement).filter_by(element_key="proposed-graph:maritime-risk:edge:alias").one()
                direct = session.query(ProposedGraphElement).filter_by(element_key="proposed-graph:maritime-risk:edge:direct").one()
                alias_payload = json.loads(alias.payload_json)
                direct_payload = json.loads(direct.payload_json)
            finally:
                session.close()

        self.assertEqual(alias.status, "rejected")
        self.assertEqual(alias_payload["matched_edge_key"], "proposed-graph:maritime-risk:edge:direct")
        self.assertEqual(alias_payload["canonical_edge_cleanup"]["duplicate_of"], "proposed-graph:maritime-risk:edge:direct")
        self.assertEqual(direct.status, "draft")
        self.assertTrue(direct_payload["canonical_edge_cleanup"]["retained"])
        self.assertEqual(cleanup["reviewed"][0]["element_key"], "proposed-graph:maritime-risk:edge:alias")

    def test_cleanup_rejects_proposed_edge_when_approved_canonical_edge_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="approved-edge-cleanup-run",
                objective="cleanup approved duplicate",
                status="completed",
            )
            session.add(run)
            session.flush()
            proposed_payload = {
                "source_type": "Country",
                "source_label": "US",
                "target_type": "Chokepoint",
                "target_label": "Hormuz",
                "relation": "depends_on",
                "endpoint_dedup_evidence": {
                    "source": {"matched_node_key": "Country:USA", "matched_space": "approved_graph"},
                    "target": {"matched_node_key": "Chokepoint:Strait of Hormuz", "matched_space": "approved_graph"},
                },
            }
            session.add(
                ProposedGraphElement(
                    run_id=run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:edge:us-hormuz",
                    element_type="edge",
                    name="US depends on Hormuz",
                    payload_json=json.dumps(proposed_payload),
                    evidence_refs_json=json.dumps([]),
                    confidence=0.7,
                    status="draft",
                )
            )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            approved_identity = iterative_graph_enrichment_agent._candidate_identity_payload(
                {
                    "element_type": "edge",
                    "name": "USA depends on Strait of Hormuz",
                    "payload": {
                        "source_type": "Country",
                        "source_label": "USA",
                        "target_type": "Chokepoint",
                        "target_label": "Strait of Hormuz",
                        "relation": "depends_on",
                        "properties": {"schema_edge_key": "link:depends_on"},
                    },
                    "evidence_refs": ["approved"],
                }
            )
            approved_entry = {
                "node_key": "Country:USA->Chokepoint:Strait of Hormuz:link:depends_on",
                "status": "approved",
                "source": "approved_graph_instance",
                "identity": approved_identity,
                "identity_key": iterative_graph_enrichment_agent._identity_key("maritime-risk", approved_identity),
                "payload": {},
            }
            agent._approved_graph_instance_identity_index = lambda session: [approved_entry]
            cleanup = agent.cleanup_duplicate_proposed_edges()

            session = Session()
            try:
                row = session.query(ProposedGraphElement).filter_by(
                    element_key="proposed-graph:maritime-risk:edge:us-hormuz"
                ).one()
                payload = json.loads(row.payload_json)
            finally:
                session.close()

        self.assertEqual(row.status, "rejected")
        self.assertEqual(payload["dedup_decision"], "merge_existing")
        self.assertEqual(payload["matched_source"], "approved_graph_instance")
        self.assertEqual(payload["matched_edge_key"], "Country:USA->Chokepoint:Strait of Hormuz:link:depends_on")
        self.assertEqual(cleanup["reviewed"][0]["element_key"], "proposed-graph:maritime-risk:edge:us-hormuz")

    def test_unresolved_generic_entity_reference_is_not_promoted_to_node(self):
        self.assertTrue(iterative_graph_enrichment_agent._is_unresolved_entity_reference("Second Entity", "Entity"))
        self.assertFalse(iterative_graph_enrichment_agent._is_unresolved_entity_reference("Taiwan Entity", "Entity"))

        def fake_runner(source_text, _schema_context):
            def interval(text):
                start = source_text.find(text)
                return SimpleNamespace(start_pos=start, end_pos=start + len(text)) if start >= 0 else None

            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Iran",
                        attributes={"schema_node_key": "country", "node_type": "Country", "confidence": 0.86},
                        char_interval=interval("Iran"),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Second Chokepoint",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint", "confidence": 0.82},
                        char_interval=interval("Second Chokepoint"),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="eyes",
                        attributes={
                            "source_label": "Iran",
                            "target_label": "Second Chokepoint",
                            "relation_label": "eyes",
                        },
                        char_interval=interval("eyes"),
                    ),
                ]
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                langextract_runner=fake_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                result = SimpleNamespace(
                    title="Iran Eyes Second Chokepoint",
                    snippet="Iran Eyes Second Chokepoint",
                    url="https://example.org/iran-eyes-second-chokepoint",
                )
                extraction = agent._extract_graph_evidence_contract(
                    session,
                    {"key": "frontier:bab", "name": "Bab el-Mandeb Strait"},
                    result,
                    "Iran Eyes Second Chokepoint",
                )
            finally:
                session.close()

            elements = agent._candidate_elements(
                extraction,
                {"key": "frontier:bab", "name": "Bab el-Mandeb Strait"},
                result,
                "Iran Eyes Second Chokepoint",
                1,
            )

        self.assertFalse([item for item in extraction["nodes"] if item.get("label") == "Second Chokepoint"])
        self.assertFalse([item for item in elements if item["element_type"] == "node" and item["name"] == "Second Chokepoint"])
        self.assertTrue(
            any(
                item.get("reason") == "unresolved_entity_reference"
                and item.get("label") == "Second Chokepoint"
                for item in extraction["rejected_or_ambiguous_candidates"]
            )
        )

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

    def test_gpt_researcher_env_uses_dotenv_gemini_key_and_safe_defaults(self):
        global_cache = iterative_graph_enrichment_agent._DOTENV_CACHE
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("GEMINI_API_KEY=test-gemini-key\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "ALETHEIA_ENV_FILE": str(env_path),
                },
                clear=True,
            ):
                iterative_graph_enrichment_agent._DOTENV_CACHE = None
                configured = _configure_gpt_researcher_env()

                self.assertEqual(os.environ["GEMINI_API_KEY"], "test-gemini-key")
                self.assertEqual(os.environ["GOOGLE_API_KEY"], "test-gemini-key")
                self.assertEqual(os.environ["FAST_LLM"], "google_genai:gemini-2.5-flash")
                self.assertEqual(os.environ["SMART_LLM"], "google_genai:gemini-2.5-flash")
                self.assertEqual(os.environ["STRATEGIC_LLM"], "google_genai:gemini-2.5-flash")
                self.assertEqual(os.environ["EMBEDDING"], "google_genai:models/text-embedding-004")
                self.assertEqual(os.environ["RETRIEVER"], "duckduckgo")
                self.assertEqual(configured["applied"]["GOOGLE_API_KEY"], "from_gemini_api_key")
        iterative_graph_enrichment_agent._DOTENV_CACHE = global_cache

    def test_gpt_researcher_provider_parses_cited_report_sources(self):
        provider = GPTResearcherSearchProvider(researcher_cls=FakeGPTResearcher)

        results = provider.search("research chokepoint exposure", 3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, "gpt_researcher")
        self.assertEqual(results[0].url, "https://zenodo.org/records/13841882")
        self.assertIn("Bab el-Mandeb Strait", results[0].snippet)
        self.assertIn("trade_at_risk_v", results[0].snippet)

    def test_iterative_run_uses_gpt_researcher_report_as_graph_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                research_provider="gpt_researcher",
                gpt_researcher_cls=FakeGPTResearcher,
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=3,
                langextract_runner=self._langextract_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )

            result = agent.run("use GPT Researcher to discover graph evidence")
            proposed = result["proposed_graph"]

            self.assertEqual(result["run"]["status"], "completed")
            self.assertTrue(any(item["element_type"] == "edge" for item in proposed))
            self.assertTrue(any(item["element_type"] == "finding" for item in proposed))
            self.assertTrue(all(item["source_url"] == "https://zenodo.org/records/13841882" for item in proposed))
            self.assertEqual(result["run"]["expansion_trace"][0]["research_provider"], "gpt_researcher")

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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
            self.assertFalse(any("allowlist" in str(item.get("reason") or "") for item in result["run"]["skipped_sources"]))
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

    def test_generic_entity_candidates_are_rejected_before_proposal(self):
        def generic_runner(source_text, _schema_context):
            def interval(text):
                start = source_text.find(text)
                if start < 0:
                    return None
                return SimpleNamespace(start_pos=start, end_pos=start + len(text))

            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="countries",
                        attributes={"schema_node_key": "country", "node_type": "Country", "confidence": 0.82},
                        char_interval=interval("countries"),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="chokepoints",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint", "confidence": 0.82},
                        char_interval=interval("chokepoints"),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="depend on",
                        attributes={
                            "source_label": "countries",
                            "target_label": "chokepoints",
                            "relation_label": "trade_dependency",
                            "confidence": 0.76,
                        },
                        char_interval=interval("depend on"),
                    ),
                ]
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "generic.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Generic report",
                            "url": "https://zenodo.org/records/generic",
                            "snippet": "countries depend on chokepoints in global trade.",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=str(path),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=generic_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )

            result = agent.run("reject generic graph candidates")

            self.assertFalse([item for item in result["proposed_graph"] if item["element_type"] in {"node", "edge"}])
            self.assertTrue(
                any(
                    str(item.get("reason") or "").startswith("candidate_quality_gate:")
                    for item in result["run"]["skipped_sources"]
                )
            )

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

    def test_structural_link_type_is_not_used_as_graph_relation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            link = session.query(OntologyArtifact).filter_by(
                project_id="maritime-risk",
                canonical_key="link:trade_dependency",
            ).one()
            payload = json.loads(link.payload_json)
            payload.pop("relation", None)
            payload["link_type"] = "MANY_TO_MANY"
            link.payload_json = json.dumps(payload)
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

            result = agent.run("structural link type should not become graph predicate")
            edges = [item for item in result["proposed_graph"] if item["element_type"] == "edge"]
            trade_edges = [item for item in edges if item["payload"].get("source_label") == "CHN"]

            self.assertTrue(trade_edges)
            self.assertEqual(trade_edges[0]["payload"]["relation"], "trade_dependency")
            self.assertNotEqual(trade_edges[0]["payload"]["relation"], "MANY_TO_MANY")
            self.assertEqual(trade_edges[0]["payload"]["properties"]["relation_cardinality"], "MANY_TO_MANY")
            self.assertEqual(
                trade_edges[0]["payload"]["relation_ontology_candidate"]["schema_artifact_key"],
                "link:trade_dependency",
            )
            identity = iterative_graph_enrichment_agent._candidate_identity_payload(trade_edges[0])
            identity_key = iterative_graph_enrichment_agent._identity_key("maritime-risk", identity)
            self.assertNotIn("MANY_TO_MANY", identity_key)
            self.assertNotIn("many to many", identity_key.lower())
            self.assertIn("trade dependency", identity_key)

    def test_missing_schema_projection_does_not_write_dictionary_semantics(self):
        def empty_semantic_runner(_source_text, _frontier_item, _source_ref):
            return {"items": [], "ontology_candidates": [], "status": "runner"}

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
                research_semantic_runner=empty_semantic_runner,
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

    def test_unmapped_relation_becomes_review_gated_fact_edge_when_edge_type_missing(self):
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

            fact_edges = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "edge" and item["payload"].get("fact_layer")
            ]
            self.assertFalse(fact_edges)
            self.assertTrue(rejected)
            self.assertEqual(rejected[0]["reason"], "unmapped_relation")
            self.assertEqual(rejected[0]["review_status"], "needs_review")
            self.assertTrue(rejected[0]["review_required"])
            self.assertTrue(rejected[0]["proposal_suppressed"])
            self.assertEqual(rejected[0]["review_surface"], "live_trace")
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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
                    embedding_adapter=StaticTestEmbeddingAdapter(),
                )
                result = agent.run("no-key must not write heuristic proposed graph")

            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            self.assertEqual(result["run"]["proposed_count"], 0)
            self.assertEqual(extraction["extraction_engine"], "google/langextract")
            self.assertEqual(extraction["extraction_engine_status"], "api_key_missing")
            self.assertEqual(extraction["rejected_or_ambiguous_candidates"][0]["reason"], "langextract_api_key_missing")
            self.assertFalse(extraction["nodes"])
            self.assertFalse(extraction["edges"])

    def test_ambiguous_relation_endpoint_stays_in_live_trace(self):
        def fake_runner(_source_text, _schema_context):
            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="US",
                        attributes={"schema_node_key": "country", "node_type": "Country"},
                        char_interval=SimpleNamespace(start_pos=0, end_pos=2),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="is exposed through a contested passage",
                        attributes={
                            "source_label": "US",
                            "target_label": "unresolved passage",
                            "relation_label": "exposed through",
                        },
                        char_interval=SimpleNamespace(start_pos=3, end_pos=42),
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
            result = agent.run("preserve unresolved relation facts")
            extraction = result["run"]["expansion_trace"][0]["last_extraction_profile"]
            rejected = extraction["rejected_or_ambiguous_candidates"]
            fact_nodes = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "node" and item["payload"].get("fact_layer")
            ]
            fact_edges = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "edge" and item["payload"].get("fact_layer")
            ]

            endpoint_rejected = next(item for item in rejected if item.get("reason") == "ambiguous_relation_endpoint")
            self.assertTrue(endpoint_rejected["proposal_suppressed"])
            self.assertEqual(endpoint_rejected["review_surface"], "live_trace")
            self.assertFalse(fact_nodes)
            self.assertFalse(fact_edges)
            self.assertFalse(
                [
                    item
                    for item in fact_nodes
                    if item["payload"].get("ontology_type") == "EvidenceFact"
                    and item["payload"].get("label") == "exposed through"
                ]
            )

    def test_reversed_ambiguous_relation_grounding_uses_grounded_direction(self):
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
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            extraction = {
                "prompt_version": "test",
                "extraction_source": "structured_llm_contract",
                "extraction_engine": "google/langextract",
                "extraction_engine_status": "ok",
                "schema_context": {"node_types": {}, "edge_types": []},
                "ontology_candidates": [],
                "nodes": [],
                "edges": [],
                "findings": [],
                "quality": {"extraction_steps": []},
                "rejected_or_ambiguous_candidates": [
                    {
                        "reason": "ambiguous_relation",
                        "review_required": True,
                        "review_status": "needs_review",
                        "source_label": "target wrapper",
                        "target_label": "source wrapper",
                        "source_type": "TargetType",
                        "target_type": "SourceType",
                        "relation_label": "mentions",
                        "evidence_quote": "source wrapper mentions target wrapper.",
                        "source_grounding": [
                            {
                                "extraction_class": "graph_relation",
                                "extraction_text": "mentions",
                                "attributes": {
                                    "source_label": "source wrapper",
                                    "target_label": "target wrapper",
                                    "source_node_key": "SourceType",
                                    "target_node_key": "TargetType",
                                    "relation_label": "mentions",
                                },
                            }
                        ],
                    }
                ],
            }

            elements = agent._candidate_elements(
                extraction,
                {"key": "object:test", "name": "Test", "artifact_type": "object", "source": "test", "depth": 0},
                SimpleNamespace(url="https://example.org/source", title="Example source"),
                "source wrapper mentions target wrapper.",
                1,
            )
            fact_nodes = [item for item in elements if item["element_type"] == "node" and item["payload"].get("fact_layer")]
            fact_edges = [item for item in elements if item["element_type"] == "edge" and item["payload"].get("fact_layer")]

            self.assertFalse(fact_nodes)
            self.assertFalse(fact_edges)
            self.assertTrue(extraction["rejected_or_ambiguous_candidates"][0]["proposal_suppressed"])
            self.assertEqual(extraction["rejected_or_ambiguous_candidates"][0]["review_surface"], "live_trace")

    def test_relation_only_ambiguous_endpoint_does_not_use_frontier_fallback(self):
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
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            extraction = {
                "prompt_version": "test",
                "extraction_source": "structured_llm_contract",
                "extraction_engine": "google/langextract",
                "extraction_engine_status": "ok",
                "schema_context": {"node_types": {}, "edge_types": []},
                "ontology_candidates": [],
                "nodes": [],
                "edges": [],
                "findings": [],
                "quality": {"extraction_steps": []},
                "rejected_or_ambiguous_candidates": [
                    {
                        "reason": "ambiguous_relation_endpoint",
                        "review_required": True,
                        "review_status": "needs_review",
                        "relation_label": "has_country_dependency",
                        "evidence_quote": "South Korea has country dependency through Hormuz.",
                        "source_ref": "https://hormuztracker.org/",
                    }
                ],
            }
            frontier = {
                "key": "proposed-graph:maritime-risk:edge:0029a93a5f44baa6",
                "name": "South Korea (KOR) has country dependency Hormuz Strait",
                "artifact_type": "proposed_edge",
                "source": "proposed_graph",
                "payload": {
                    "source_type": "Country",
                    "source_label": "South Korea (KOR)",
                    "relation": "has_country_dependency",
                    "target_type": "Maritime Chokepoint",
                    "target_label": "Hormuz Strait",
                },
            }

            elements = agent._candidate_elements(
                extraction,
                frontier,
                SimpleNamespace(url="https://hormuztracker.org/", title="Hormuz tracker"),
                "summary",
                1,
            )
            fact_edges = [item for item in elements if item["element_type"] == "edge" and item["payload"].get("fact_layer")]
            relation_nodes = [
                item
                for item in elements
                if item["element_type"] == "node" and item["name"] == "has_country_dependency"
            ]

            self.assertFalse(fact_edges)
            self.assertFalse(relation_nodes)
            candidate = extraction["rejected_or_ambiguous_candidates"][0]
            self.assertTrue(candidate["proposal_suppressed"])
            self.assertEqual(candidate["review_surface"], "live_trace")
            self.assertNotIn("source_label", candidate)
            self.assertNotIn("target_label", candidate)

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

    def test_unmapped_relation_with_approved_edge_stays_audit_only(self):
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

            fact_edges = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "edge" and item["payload"].get("fact_layer")
            ]
            fact_nodes = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "node" and item["payload"].get("fact_layer")
            ]
            self.assertFalse(fact_edges)
            self.assertFalse(fact_nodes)
            self.assertTrue(any(item.get("reason") == "ambiguous_relation" for item in rejected))
            ambiguous = next(item for item in rejected if item.get("reason") == "ambiguous_relation")
            self.assertEqual(ambiguous["review_status"], "needs_review")
            self.assertTrue(ambiguous["review_required"])
            self.assertTrue(ambiguous["proposal_suppressed"])
            self.assertEqual(ambiguous["review_surface"], "live_trace")
            self.assertEqual(ambiguous["relation_label"], "unapproved blockade relation")

    def test_deep_research_unmapped_relation_becomes_ontology_relation_proposal(self):
        def fake_runner(_source_text, _schema_context):
            return SimpleNamespace(
                extractions=[
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Iran",
                        attributes={"schema_node_key": "country", "node_type": "Country"},
                        char_interval=SimpleNamespace(start_pos=0, end_pos=4),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_node",
                        extraction_text="Strait of Hormuz",
                        attributes={"schema_node_key": "chokepoint", "node_type": "Chokepoint"},
                        char_interval=SimpleNamespace(start_pos=22, end_pos=38),
                    ),
                    SimpleNamespace(
                        extraction_class="graph_relation",
                        extraction_text="raises war-risk insurance premiums through",
                        attributes={
                            "source_label": "Iran",
                            "target_label": "Strait of Hormuz",
                            "relation_label": "raises war-risk insurance premiums through",
                        },
                        char_interval=SimpleNamespace(start_pos=5, end_pos=21),
                    ),
                ]
            )

        def empty_semantic_runner(_source_text, _frontier_item, _source_ref):
            return {"items": [], "ontology_candidates": [], "status": "runner"}

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
                research_semantic_runner=empty_semantic_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            frontier = {
                "key": "research-topic:maritime-risk:middle-east-risk",
                "name": "Middle East maritime systemic risk",
                "kind": "research_topic",
                "source_kind": "research_topic",
                "payload": {"research_mode": "deep_research"},
            }

            result = agent.run("deep research should expand ontology relation types", frontier_items=[frontier])
            relation_proposals = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "ontology_concept"
                and item["payload"].get("artifact_type") == "link"
                and item["payload"].get("ontology_part") == "relation"
                and item["payload"].get("label") == "raises war-risk insurance premiums through"
            ]

            self.assertTrue(relation_proposals)
            proposal = relation_proposals[0]
            payload = proposal["payload"]
            self.assertEqual(proposal["status"], "needs_more_evidence")
            self.assertEqual(payload["proposal_scope"], "ontology_expansion")
            self.assertEqual(payload["label"], "raises war-risk insurance premiums through")
            self.assertEqual(payload["domain"], "Country")
            self.assertEqual(payload["range"], "Chokepoint")
            self.assertEqual(payload["ontology_candidate"]["artifact_type"], "link")
            self.assertEqual(payload["extraction"]["review_boundary"], "ontology_concept_review")
            self.assertFalse(payload["writes_canonical"])

    def test_deep_research_extracts_situation_metrics_and_claims(self):
        def empty_runner(_source_text, _schema_context):
            return SimpleNamespace(extractions=[])

        def semantic_runner(_source_text, _frontier_item, source_ref):
            quote = "War risk premiums surged from 0.2% to as high as 1%, indicating rapid financial risk escalation."
            return {
                "items": [
                    {
                        "element_type": "situation",
                        "name": "2026 Strait of Hormuz disruption",
                        "confidence": 0.7,
                        "payload": {
                            "subject": "Strait of Hormuz",
                            "time_scope": "2026",
                            "geography_scope": "Strait of Hormuz",
                            "claim": "The Strait of Hormuz disruption affected global energy trade.",
                            "evidence_quote": "The Strait of Hormuz disruption in 2026 affected global energy trade.",
                        },
                    },
                    {
                        "element_type": "metric_observation",
                        "name": "Strait of Hormuz share of global oil and LNG",
                        "confidence": 0.74,
                        "payload": {
                            "subject": "Strait of Hormuz",
                            "metric_key": "global_oil_lng_share",
                            "value": 0.2,
                            "unit": "share",
                            "evidence_quote": "Approximately 20% of the world's oil and LNG passes through the strait.",
                        },
                    },
                    {
                        "element_type": "metric_observation",
                        "name": "Strait of Hormuz share of global seaborne oil trade",
                        "confidence": 0.74,
                        "payload": {
                            "subject": "Strait of Hormuz",
                            "metric_key": "global_seaborne_oil_trade_share",
                            "value": 0.25,
                            "unit": "share",
                            "evidence_quote": "The chokepoint carries around a quarter of global seaborne oil trade.",
                        },
                    },
                    {
                        "element_type": "metric_change_observation",
                        "name": "War risk premiums increase",
                        "confidence": 0.76,
                        "payload": {
                            "subject": "War risk premiums",
                            "metric_key": "war_risk_premiums",
                            "baseline_value": 0.002,
                            "observed_value": 0.01,
                            "change_ratio": 5.0,
                            "direction": "increase",
                            "evidence_quote": quote,
                        },
                    },
                    {
                        "element_type": "indicator_claim",
                        "name": "War risk premiums indicate financial risk escalation",
                        "confidence": 0.72,
                        "payload": {
                            "subject": "war_risk_premiums_change",
                            "indicates": "financial_risk_escalation",
                            "claim": "The premium increase indicates rapid financial risk escalation.",
                            "evidence_quote": quote,
                        },
                    },
                    {
                        "element_type": "impact_claim",
                        "name": "Disruption increases fuel prices",
                        "confidence": 0.68,
                        "payload": {
                            "subject": "Strait of Hormuz disruption",
                            "target": "fuel_prices",
                            "direction": "increase",
                            "claim": "The disruption caused soaring fuel prices.",
                            "evidence_quote": "The disruption caused soaring fuel prices, rising freight costs, and higher shipping insurance costs.",
                        },
                    },
                ],
                "ontology_candidates": [
                    {
                        "artifact_type": "object",
                        "label": "Supply Chain Chokepoint",
                        "description": "A recurring constricted dependency point in supply chains.",
                        "evidence_quote": "These chokepoints, whether maritime passages, processing facilities for essential minerals, or nodes in advanced technological supply chains, represent not only strategic opportunities but also systemic risks.",
                        "confidence": 0.63,
                    },
                    {
                        "artifact_type": "property",
                        "label": "Preparedness Gap",
                        "description": "A property capturing incomplete exposure assessment or inadequate preparedness.",
                        "property_of": "Situation",
                        "evidence_quote": "the comprehensive assessment of countries' exposure to these disruptions remains largely incomplete, thereby inhibiting adequate preparedness",
                        "confidence": 0.61,
                    },
                ],
                "status": "runner",
            }

        report = (
            "The Strait of Hormuz disruption in 2026 affected global energy trade. "
            "Approximately 20% of the world's oil and LNG passes through the strait. "
            "The chokepoint carries around a quarter of global seaborne oil trade. "
            "War risk premiums surged from 0.2% to as high as 1%, indicating rapid financial risk escalation. "
            "The disruption caused soaring fuel prices, rising freight costs, and higher shipping insurance costs. "
            "Oil market volatility and natural gas market volatility also increased."
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "search.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Strait of Hormuz disruption report",
                            "url": "https://zenodo.org/records/semantic",
                            "snippet": report,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=str(path),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=empty_runner,
                research_semantic_runner=semantic_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            frontier = {
                "key": "research-topic:maritime-risk:hormuz-situation",
                "name": "Hormuz situation research",
                "kind": "research_topic",
                "source_kind": "research_topic",
                "payload": {"research_mode": "deep_research"},
            }

            result = agent.run("deep research should extract situation metric and impact proposals", frontier_items=[frontier])
            by_type = {}
            for item in result["proposed_graph"]:
                by_type.setdefault(item["element_type"], []).append(item)

            self.assertIn("situation", by_type)
            self.assertIn("metric_observation", by_type)
            self.assertIn("metric_change_observation", by_type)
            self.assertIn("impact_claim", by_type)
            self.assertIn("indicator_claim", by_type)
            self.assertIn("ontology_concept", by_type)
            metric_values = sorted(item["payload"].get("value") for item in by_type["metric_observation"])
            self.assertIn(0.2, metric_values)
            self.assertIn(0.25, metric_values)
            change = by_type["metric_change_observation"][0]["payload"]
            self.assertEqual(change["metric_key"], "war_risk_premiums")
            self.assertEqual(change["baseline_value"], 0.002)
            self.assertEqual(change["observed_value"], 0.01)
            self.assertEqual(change["change_ratio"], 5.0)
            self.assertTrue(all(item["status"] == "needs_more_evidence" for items in by_type.values() for item in items))
            self.assertTrue(
                all(
                    item["payload"]["proposal_scope"] == "research_semantic_extraction"
                    for element_type, items in by_type.items()
                    if element_type != "ontology_concept"
                    for item in items
                )
            )
            ontology_payloads = [item["payload"] for item in by_type["ontology_concept"]]
            self.assertIn("object", {item["artifact_type"] for item in ontology_payloads})
            self.assertTrue(all(item["payload"]["proposal_scope"] == "ontology_expansion" for item in by_type["ontology_concept"]))

    def test_deep_research_semantic_proposals_deduplicate_across_runs(self):
        def empty_runner(_source_text, _schema_context):
            return SimpleNamespace(extractions=[])

        def semantic_runner(_source_text, _frontier_item, _source_ref):
            return {
                "items": [
                    {
                        "element_type": "situation",
                        "name": "2026 Strait of Hormuz disruption",
                        "confidence": 0.7,
                        "payload": {
                            "subject": "Strait of Hormuz",
                            "time_scope": "2026",
                            "claim": "The Strait of Hormuz disruption affected global energy trade.",
                            "evidence_quote": "The Strait of Hormuz disruption in 2026 affected global energy trade.",
                        },
                    },
                    {
                        "element_type": "metric_observation",
                        "name": "Strait of Hormuz share of global oil and LNG",
                        "confidence": 0.74,
                        "payload": {
                            "subject": "Strait of Hormuz",
                            "metric_key": "global_oil_lng_share",
                            "value": 0.2,
                            "unit": "share",
                            "evidence_quote": "Approximately 20% of the world's oil and LNG passes through the strait.",
                        },
                    },
                    {
                        "element_type": "metric_change_observation",
                        "name": "War risk premiums increase",
                        "confidence": 0.76,
                        "payload": {
                            "subject": "War risk premiums",
                            "metric_key": "war_risk_premiums",
                            "baseline_value": 0.002,
                            "observed_value": 0.01,
                            "evidence_quote": "War risk premiums surged from 0.2% to as high as 1%, indicating rapid financial risk escalation.",
                        },
                    },
                    {
                        "element_type": "indicator_claim",
                        "name": "War risk premiums indicate financial risk escalation",
                        "confidence": 0.72,
                        "payload": {
                            "subject": "war_risk_premiums_change",
                            "indicates": "financial_risk_escalation",
                            "claim": "The premium increase indicates rapid financial risk escalation.",
                            "evidence_quote": "War risk premiums surged from 0.2% to as high as 1%, indicating rapid financial risk escalation.",
                        },
                    },
                ],
                "status": "runner",
            }

        report = (
            "The Strait of Hormuz disruption in 2026 affected global energy trade. "
            "Approximately 20% of the world's oil and LNG passes through the strait. "
            "War risk premiums surged from 0.2% to as high as 1%, indicating rapid financial risk escalation."
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "search.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Strait of Hormuz disruption report",
                            "url": "https://zenodo.org/records/semantic",
                            "snippet": report,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            db_url, _ = self._seed_db(tmpdir)
            frontier = {
                "key": "research-topic:maritime-risk:hormuz-situation",
                "name": "Hormuz situation research",
                "kind": "research_topic",
                "source_kind": "research_topic",
                "payload": {"research_mode": "deep_research"},
            }
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                search_results_json=str(path),
                allowed_domains=["zenodo.org"],
                max_iterations=1,
                max_frontier=1,
                max_results_per_query=1,
                langextract_runner=empty_runner,
                research_semantic_runner=semantic_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            first = agent.run("deep research semantic proposal dedup", frontier_items=[frontier])
            second = agent.run("deep research semantic proposal dedup", frontier_items=[frontier])
            first_keys = {
                item["element_key"]
                for item in first["proposed_graph"]
                if item["element_type"] in {"situation", "metric_observation", "metric_change_observation", "indicator_claim"}
            }
            second_keys = {
                item["element_key"]
                for item in second["proposed_graph"]
                if item["element_type"] in {"situation", "metric_observation", "metric_change_observation", "indicator_claim"}
            }
            self.assertTrue(first_keys)
            self.assertFalse(second_keys)
            duplicate_records = [
                item
                for item in second["run"]["skipped_sources"]
                if item.get("element_type") in {"situation", "metric_observation", "metric_change_observation", "indicator_claim"}
                and item.get("dedup_decision") in {"duplicate_existing_proposal", "merge_existing"}
            ]
            self.assertEqual(len(duplicate_records), len(first_keys))

    def test_operational_ontology_proposals_keep_action_fields_and_quality_gate(self):
        def semantic_runner(_source_text, _frontier_item, _source_ref):
            return {
                "items": [],
                "ontology_candidates": [
                    {
                        "artifact_type": "action",
                        "ontology_part": "action",
                        "label": "Close Waterway",
                        "description": "Close a waterway in response to an operational disruption.",
                        "trigger_event": "Waterway Disruption Event",
                        "trigger_or_condition": "shipping disruption threshold crossed",
                        "target_object_types": ["Waterway"],
                        "input_parameters": ["closure_reason", "closure_start_time"],
                        "expected_effects": ["set operational_status to Closed"],
                        "guardrails": ["requires authorized operator approval"],
                        "evidence_quote": "Authorities may close the canal when conflict disrupts safe transit.",
                        "confidence": 0.73,
                    },
                    {
                        "artifact_type": "event",
                        "ontology_part": "event",
                        "label": "Waterway Closure Event",
                        "description": "A time-bounded closure event for a waterway.",
                        "trigger_or_condition": "safe transit unavailable",
                        "affected_object_types": ["Waterway", "Route"],
                        "state_changes": ["operational_status changes from Open to Closed"],
                        "evidence_quote": "Authorities may close the canal when conflict disrupts safe transit.",
                        "confidence": 0.71,
                    },
                    {
                        "artifact_type": "function",
                        "ontology_part": "function",
                        "label": "Calculate Closure Duration",
                        "description": "Calculate how long a waterway remained closed.",
                        "inputs": ["closure_start_time", "reopen_time"],
                        "outputs": ["duration_days"],
                        "evidence_quote": "The closure lasted from the shutdown date to reopening.",
                        "confidence": 0.69,
                    },
                    {
                        "artifact_type": "policy",
                        "ontology_part": "policy",
                        "label": "Waterway Closure Approval Policy",
                        "description": "Govern closure actions.",
                        "applies_to": ["Close Waterway"],
                        "guardrails": ["requires authorized operator approval"],
                        "evidence_quote": "Closure requires authorization before transit is suspended.",
                        "confidence": 0.68,
                    },
                ],
                "status": "runner",
            }

        result = SimpleNamespace(
            url="gpt_researcher://report/operational-ontology",
            title="Operational ontology report",
            snippet="",
        )
        elements = iterative_graph_enrichment_agent._research_semantic_proposals(
            {"key": "research-topic:waterway", "source_kind": "research_topic"},
            result,
            "Authorities may close the canal when conflict disrupts safe transit.",
            1,
            semantic_runner,
        )
        proposals = {item["name"]: item for item in elements if item["element_type"] == "ontology_concept"}
        self.assertEqual(set(proposals), {
            "Close Waterway",
            "Waterway Closure Event",
            "Calculate Closure Duration",
            "Waterway Closure Approval Policy",
        })
        agent = IterativeGraphEnrichmentAgent.__new__(IterativeGraphEnrichmentAgent)
        for proposal in proposals.values():
            self.assertEqual(agent._candidate_quality_issues(proposal), [])
        action_payload = proposals["Close Waterway"]["payload"]
        self.assertEqual(action_payload["trigger_event"], "Waterway Disruption Event")
        self.assertEqual(action_payload["expected_effects"], ["set operational_status to Closed"])
        self.assertEqual(action_payload["ontology_candidate"]["guardrails"], ["requires authorized operator approval"])

    def test_operational_ontology_identity_distinguishes_action_shape(self):
        def action_item(trigger_event, expected_effects):
            return {
                "element_type": "ontology_concept",
                "name": "Update Waterway Status",
                "payload": {
                    "artifact_type": "action",
                    "label": "Update Waterway Status",
                    "trigger_event": trigger_event,
                    "target_object_types": ["Waterway"],
                    "input_parameters": ["status"],
                    "expected_effects": expected_effects,
                    "guardrails": ["requires review"],
                    "evidence_quote": "Operators update waterway status after review.",
                    "source_url": "gpt_researcher://report/identity",
                },
            }

        first = iterative_graph_enrichment_agent._candidate_identity_payload(
            action_item("Closure Event", ["set status to Closed"])
        )
        second = iterative_graph_enrichment_agent._candidate_identity_payload(
            action_item("Reopening Event", ["set status to Open"])
        )
        self.assertNotEqual(first["source_identity"], second["source_identity"])

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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )

            plan = agent._query_plan_for_frontier(frontier, "discover maritime trade exposure")
            query = plan["query"]
            self.assertIn("CHN", query)
            self.assertIn("Bab el Mandeb Strait", query)
            self.assertNotIn("depends_on", query)
            self.assertNotIn("trade_at_risk_v", query)
            self.assertEqual(plan["graph_context_used"]["relation"], "depends_on")
            self.assertIn("CHN", plan["graph_context_used"]["neighbor_nodes"])
            self.assertIn("Bab el-Mandeb Strait", plan["path_context_used"]["path_label"])
            self.assertIn("trade_at_risk_v", plan["query_terms"]["metrics"])
            self.assertEqual(plan["selected_plan"]["intent"], "path_evidence")
            self.assertEqual(plan["selected_plan"]["granularity"], "L0_path_exact")
            self.assertEqual(plan["selected_plan"]["radius"], 0)
            self.assertEqual(plan["selected_plan"]["query"], plan["query"])
            self.assertEqual(
                [item["intent"] for item in plan["plans"]],
                [
                    "path_evidence",
                    "single_endpoint_expansion",
                    "loose_pair_discovery",
                    "schema_broad_discovery",
                ],
            )
            self.assertEqual([item["coarse_level"] for item in plan["plans"]], [0, 1, 2, 3])
            self.assertEqual(plan["plans"][1]["radius"], 1)
            self.assertIn("Bab el Mandeb Strait", plan["plans"][1]["query"])
            self.assertIn("SchemaGraph mapping", plan["plans"][2]["acceptance"])
            self.assertTrue(plan["plans"][3]["query"])
            self.assertEqual(plan["expansion_policy"]["default_max_radius"], 1)
            self.assertEqual(plan["expansion_policy"]["query_ladder"][0], "L0_path_exact")
            self.assertNotIn("L4_objective_broad", plan["expansion_policy"]["query_ladder"])
            self.assertNotIn("objective_broad_scan", [item["intent"] for item in plan["plans"]])
            self.assertIn("graph_anchor", plan["relevance_gate"])

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
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            result = agent.run("discover maritime trade exposure", frontier_items=[frontier])
            trace = result["run"]["expansion_trace"][0]

            self.assertNotIn("depends_on", trace["query"])
            self.assertNotIn("trade_at_risk_v", trace["query"])
            self.assertEqual(trace["graph_context_used"]["relation"], "depends_on")
            self.assertEqual(trace["path_context_used"]["source_label"], "CHN")
            self.assertEqual(trace["selected_query_plan"]["intent"], "path_evidence")
            self.assertEqual(trace["selected_query_plan"]["granularity"], "L0_path_exact")
            self.assertEqual(trace["query_plans"][1]["intent"], "single_endpoint_expansion")
            self.assertEqual(trace["query_plans"][-1]["granularity"], "L3_schema_broad")
            self.assertNotIn("L4_objective_broad", [item["granularity"] for item in trace["query_plans"]])
            self.assertIn("schema", trace["relevance_gate"])
            self.assertFalse(any("allowlist" in str(item.get("reason") or "") for item in result["run"]["skipped_sources"]))

    def test_graph_context_query_plan_keeps_node_frontier_label_in_exact_query(self):
        plan = _graph_context_query_plan(
            {
                "key": "proposed-graph:node:hormuz",
                "name": "Hormuz",
                "artifact_type": "proposed_node",
                "ontology_type": "Maritime Chokepoint",
                "payload": {"ontology_type": "Maritime Chokepoint", "label": "Hormuz"},
            },
            "Analyze US-Iran escalation impacts",
            "maritime-risk",
        )

        self.assertIn("Hormuz", plan["selected_plan"]["query"])
        self.assertEqual(plan["selected_plan"]["granularity"], "L0_path_exact")
        self.assertIn({"term": "Hormuz", "source": "frontier.label"}, plan["selected_plan"]["source_terms"])
        for query_plan in plan["plans"]:
            self.assertIn("Hormuz", query_plan["query"])
        self.assertNotIn("Country Analyze US-Iran", " ".join(item["query"] for item in plan["plans"]))

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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
            identity = edge_payload.get("identity") or {}
            self.assertEqual(identity.get("source_canonical_key"), "")
            self.assertEqual(identity.get("target_canonical_key"), "")
            self.assertTrue(
                any(
                    item.get("reason") == "duplicate_endpoint_node_not_proposed"
                    for item in second["run"]["skipped_sources"]
                    if item.get("element_type") == "node"
                )
            )

    def test_persistent_identity_index_is_populated_and_reused(self):
        def empty_semantic_runner(_source_text, _frontier_item, _source_ref):
            return {"items": [], "ontology_candidates": [], "status": "runner"}

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
                research_semantic_runner=empty_semantic_runner,
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
            self.assertGreaterEqual(second_snapshot["identity_index_count"], first_snapshot["identity_index_count"])
            self.assertEqual(
                second_snapshot["identity_index_count"],
                len({row["identity_key"] for row in second_snapshot["identity_index"]}),
            )
            self.assertTrue(
                any(
                    payload.get("matched_source") == "proposed_graph"
                    and payload.get("dedup_decision") == "duplicate_existing_proposal"
                    for payload in [
                        *[item.get("payload") or {} for item in second["proposed_graph"]],
                        *second["run"]["skipped_sources"],
                    ]
                )
            )

            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                proposed_identity_count = len(
                    [
                        row
                        for row in second_snapshot["identity_index"]
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

    def test_llm_duplicate_verifier_receives_top_twenty_vector_candidates(self):
        captured = {}

        def verifier(prompt):
            captured.update(prompt)
            return {
                "enabled": True,
                "status": "ok",
                "decision": "distinct",
                "matched_node_key": None,
                "confidence": 0.91,
                "reason": "same broad type but different entity identity",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
                duplicate_verifier_runner=verifier,
            )
            session = agent.Session()
            try:
                for index in range(25):
                    label = f"China Alias {index:02d}"
                    agent._upsert_identity_index_row(
                        session,
                        identity={
                            "kind": "node",
                            "entity_type": "Country",
                            "label": label,
                            "normalized_label": label.lower(),
                            "aliases": [],
                            "source_identity": None,
                            "property_fingerprint": label.lower(),
                        },
                        identity_key=f"node:maritime-risk:country:china-alias-{index:02d}",
                        source_space="proposed_graph",
                        source_key=f"proposed-graph:maritime-risk:node:china-alias-{index:02d}",
                        source_status="draft",
                        payload={"label": label},
                    )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
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
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(len(captured["retrieved_candidates"]), 20)
            self.assertEqual(candidate["payload"]["dedup_decision"], "new_proposal")
            self.assertEqual(candidate["payload"]["decision_reason"], "llm_verified_distinct")
            self.assertEqual(candidate["payload"]["llm_duplicate_verdict"]["decision"], "distinct")
            self.assertTrue(candidate["payload"]["llm_merge_decision_allowed"])

    def test_llm_duplicate_verifier_can_confirm_duplicate_candidate(self):
        def verifier(_prompt):
            return {
                "enabled": True,
                "status": "ok",
                "decision": "duplicate",
                "matched_node_key": "proposed-graph:maritime-risk:node:china",
                "confidence": 0.96,
                "reason": "same country entity and type",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
                duplicate_verifier_runner=verifier,
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
                        "aliases": ["CHN"],
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
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
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
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(candidate["payload"]["decision_reason"], "llm_verified_duplicate")
            self.assertEqual(candidate["payload"]["llm_duplicate_verdict"]["decision"], "duplicate")

    def test_similar_node_with_confidence_above_threshold_dedups_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
                node_similarity_dedup_threshold=0.6,
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "Similarland Alpha",
                        "normalized_label": "similarland alpha",
                        "aliases": [],
                        "source_identity": None,
                        "property_fingerprint": "similarland-alpha",
                    },
                    identity_key="node:maritime-risk:country:similarland-alpha:legacy",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:node:similarland-alpha",
                    source_status="draft",
                    payload={"label": "Similarland Alpha"},
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "Similarland Beta",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "Similarland Beta",
                        "description": "A similar candidate country node.",
                        "properties": {},
                    },
                    "evidence_refs": ["source:similar-beta"],
                    "source_url": "https://example.org/similar-beta",
                    "confidence": 0.61,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(candidate["payload"]["match_method"], "vector_embedding")
            self.assertEqual(candidate["payload"]["decision_reason"], "node_similarity_confidence_threshold_met")
            self.assertGreaterEqual(candidate["payload"]["match_score"], 0.6)
            self.assertGreater(candidate["payload"]["candidate_confidence"], 0.6)
            self.assertEqual(candidate["payload"]["node_similarity_dedup_threshold"], 0.6)
            self.assertEqual(candidate["payload"]["matched_node_key"], "proposed-graph:maritime-risk:node:similarland-alpha")

    def test_similar_node_at_confidence_threshold_does_not_direct_dedup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
                node_similarity_dedup_threshold=0.6,
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "Similarland Alpha",
                        "normalized_label": "similarland alpha",
                        "aliases": [],
                        "source_identity": None,
                        "property_fingerprint": "similarland-alpha",
                    },
                    identity_key="node:maritime-risk:country:similarland-alpha:legacy",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:node:similarland-alpha",
                    source_status="draft",
                    payload={"label": "Similarland Alpha"},
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "Similarland Beta",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "Similarland Beta",
                        "description": "A similar candidate country node.",
                        "properties": {},
                    },
                    "evidence_refs": ["source:similar-beta"],
                    "source_url": "https://example.org/similar-beta",
                    "confidence": 0.6,
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
            self.assertEqual(candidate["payload"]["decision_reason"], "nearest vector outside dedup threshold")
            self.assertEqual(candidate["payload"]["candidate_confidence"], 0.6)
            self.assertGreaterEqual(candidate["payload"]["match_score"], 0.6)

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
            self.assertEqual(annotated["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertFalse(annotated["payload"]["structure_compatible"])
            self.assertIn("relation", annotated["payload"]["conflict_fields"])
            self.assertIn("source_identity", annotated["payload"]["conflict_fields"])

    def test_edge_source_url_difference_does_not_block_vector_dedup(self):
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
                        "source_node": "south korea kor",
                        "target_node": "hormuz strait",
                        "relation": "has country dependency",
                        "source_identity": "https://source-a.example/hormuz|country_dependency",
                        "property_fingerprint": "legacy-edge-with-source-url",
                    },
                    identity_key="edge:maritime-risk:south-korea:has-country-dependency:hormuz:legacy-source-a",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:edge:legacy-source-a",
                    source_status="draft",
                    payload={
                        "source_type": "Country",
                        "source_label": "South Korea (KOR)",
                        "relation": "has_country_dependency",
                        "target_type": "Chokepoint",
                        "target_label": "Hormuz Strait",
                        "metrics": ["country_dependency"],
                        "source_url": "https://source-a.example/hormuz",
                    },
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            annotated = agent._annotate_candidate_identity(
                {
                    "element_type": "edge",
                    "name": "South Korea (KOR) has country dependency Hormuz Strait",
                    "payload": {
                        "source_type": "Country",
                        "source_label": "South Korea (KOR)",
                        "relation": "has_country_dependency",
                        "target_type": "Chokepoint",
                        "target_label": "Hormuz Strait",
                        "description": "South Korea has country dependency on Hormuz Strait.",
                        "properties": {"metric_key": "country_dependency", "source_url": "https://source-b.example/hormuz"},
                        "metrics": ["country_dependency"],
                    },
                    "evidence_refs": ["https://source-b.example/hormuz"],
                    "source_url": "https://source-b.example/hormuz",
                    "confidence": 0.82,
                    "iteration": 1,
                },
                task_id="task-edge",
                run_id="run-edge",
                frontier_id="frontier-edge",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(annotated["payload"]["match_method"], "vector_embedding")
            self.assertEqual(annotated["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertTrue(annotated["payload"]["structure_compatible"])
            self.assertNotIn("source_identity", annotated["payload"]["conflict_fields"])
            self.assertIn("compatible source/metric identity", annotated["payload"]["match_evidence"])
            self.assertEqual(
                annotated["payload"]["matched_node_key"],
                "proposed-graph:maritime-risk:edge:legacy-source-a",
            )
            self.assertNotIn("source-b.example", annotated["payload"]["identity_key"])

    def test_edge_metric_identity_conflict_still_requires_review(self):
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
                        "source_node": "south korea kor",
                        "target_node": "hormuz strait",
                        "relation": "has country dependency",
                        "source_identity": "trade_at_risk_v",
                        "property_fingerprint": "trade-metric-edge",
                    },
                    identity_key="edge:maritime-risk:south-korea:has-country-dependency:hormuz:trade",
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:edge:trade-metric",
                    source_status="draft",
                    payload={
                        "source_type": "Country",
                        "source_label": "South Korea (KOR)",
                        "relation": "has_country_dependency",
                        "target_type": "Chokepoint",
                        "target_label": "Hormuz Strait",
                        "metrics": ["trade_at_risk_v"],
                    },
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            annotated = agent._annotate_candidate_identity(
                {
                    "element_type": "edge",
                    "name": "South Korea (KOR) has country dependency Hormuz Strait",
                    "payload": {
                        "source_type": "Country",
                        "source_label": "South Korea (KOR)",
                        "relation": "has_country_dependency",
                        "target_type": "Chokepoint",
                        "target_label": "Hormuz Strait",
                        "description": "South Korea has country dependency on Hormuz Strait.",
                        "properties": {"metric_key": "military_presence_score"},
                        "metrics": ["military_presence_score"],
                    },
                    "evidence_refs": ["https://source-b.example/hormuz"],
                    "source_url": "https://source-b.example/hormuz",
                    "confidence": 0.82,
                    "iteration": 1,
                },
                task_id="task-edge",
                run_id="run-edge",
                frontier_id="frontier-edge",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(annotated["payload"]["match_method"], "vector_embedding")
            self.assertEqual(annotated["payload"]["decision_reason"], "structural_conflict")
            self.assertEqual(annotated["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertFalse(annotated["payload"]["structure_compatible"])
            self.assertIn("source_identity", annotated["payload"]["conflict_fields"])

    def test_finding_embedding_dedup_does_not_use_source_url_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            existing_item = {
                "element_type": "finding",
                "name": "Hormuz energy disruption affects import exposure",
                "payload": {
                    "finding_type": "deep_graph_finding",
                    "title": "Hormuz energy disruption affects import exposure",
                    "conclusion": "Energy disruption near Hormuz Strait affects importer exposure.",
                    "evidence_chain": [
                        {"kind": "relation", "metric": "country_dependency", "value": "country dependency", "source_ref": "https://source-a.example/finding"},
                        {"kind": "risk_metric", "metric": "energy_import_exposure", "value": "energy disruption", "source_ref": "https://source-a.example/finding"},
                    ],
                },
                "evidence_refs": ["https://source-a.example/finding"],
                "source_url": "https://source-a.example/finding",
                "confidence": 0.82,
                "iteration": 1,
            }
            existing_identity = iterative_graph_enrichment_agent._candidate_identity_payload(existing_item)
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity=existing_identity,
                    identity_key=iterative_graph_enrichment_agent._identity_key("maritime-risk", existing_identity),
                    source_space="proposed_graph",
                    source_key="proposed-graph:maritime-risk:finding:hormuz-energy-a",
                    source_status="draft",
                    evidence_refs=existing_item["evidence_refs"],
                    payload=existing_item["payload"],
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = {
                "element_type": "finding",
                "name": "Energy import exposure near Hormuz Strait",
                "payload": {
                    "finding_type": "deep_graph_finding",
                    "title": "Energy import exposure near Hormuz Strait",
                    "conclusion": "Import exposure can rise when energy traffic through Hormuz is disrupted.",
                    "evidence_chain": [
                        {"kind": "risk_metric", "metric": "energy_import_exposure", "value": "energy disruption", "source_ref": "https://source-b.example/finding"},
                        {"kind": "relation", "metric": "country_dependency", "value": "country dependency", "source_ref": "https://source-b.example/finding"},
                    ],
                },
                "evidence_refs": ["https://source-b.example/finding"],
                "source_url": "https://source-b.example/finding",
                "confidence": 0.8,
                "iteration": 1,
            }
            annotated = agent._annotate_candidate_identity(
                candidate,
                task_id="task-finding",
                run_id="run-finding",
                frontier_id="frontier-finding",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(annotated["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(annotated["payload"]["match_method"], "vector_embedding")
            self.assertEqual(annotated["payload"]["matched_node_key"], "proposed-graph:maritime-risk:finding:hormuz-energy-a")
            self.assertEqual(annotated["payload"]["embedding_model"], "test-multilingual-mini")
            self.assertEqual(annotated["payload"]["vector_distance"], 0.0)
            self.assertNotIn("source-a.example", annotated["payload"]["identity_key"])
            self.assertNotIn("source-b.example", annotated["payload"]["identity_key"])
            self.assertFalse(annotated["payload"]["llm_merge_decision_allowed"])

    def test_repeated_finding_is_skipped_from_new_proposed_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            first_agent = IterativeGraphEnrichmentAgent(
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
            first = first_agent.run("discover hazard chokepoint country trade action paths")
            self.assertTrue([item for item in first["proposed_graph"] if item["element_type"] == "finding"])
            first_snapshot = first_agent.identity_index_snapshot()
            self.assertTrue(any(row["element_kind"] == "finding" for row in first_snapshot["identity_index"]))

            second_agent = IterativeGraphEnrichmentAgent(
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
            second = second_agent.run("discover hazard chokepoint country trade action paths")
            self.assertFalse([item for item in second["proposed_graph"] if item["element_type"] == "finding"])
            self.assertTrue(
                any(
                    item.get("element_type") == "finding"
                    and item.get("reason") == "duplicate_finding_not_proposed"
                    and item.get("dedup_decision") == "duplicate_existing_proposal"
                    for item in second["run"]["skipped_sources"]
                )
            )
            self.assertEqual(second["run"]["finding_count"], 0)

    def test_stale_persistent_index_rebuilds_to_include_existing_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            first_agent = IterativeGraphEnrichmentAgent(
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
            first = first_agent.run("discover hazard chokepoint country trade action paths")
            self.assertTrue([item for item in first["proposed_graph"] if item["element_type"] == "finding"])

            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                deleted = (
                    session.query(GraphIdentityIndex)
                    .filter_by(project_id="maritime-risk", element_kind="finding")
                    .delete(synchronize_session=False)
                )
                self.assertGreater(deleted, 0)
                self.assertGreater(session.query(GraphIdentityIndex).filter_by(project_id="maritime-risk").count(), 0)
                session.commit()
            finally:
                session.close()

            second_agent = IterativeGraphEnrichmentAgent(
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
            second = second_agent.run("discover hazard chokepoint country trade action paths")
            self.assertFalse([item for item in second["proposed_graph"] if item["element_type"] == "finding"])
            self.assertTrue(
                any(
                    item.get("element_type") == "finding"
                    and item.get("reason") == "duplicate_finding_not_proposed"
                    for item in second["run"]["skipped_sources"]
                )
            )
            snapshot = second_agent.identity_index_snapshot()
            self.assertTrue(any(row["element_kind"] == "finding" for row in snapshot["identity_index"]))

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

    def test_embedding_unavailable_short_alias_conflict_requires_review(self):
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
                        "label": "United States (USA)",
                        "normalized_label": "united states usa",
                        "aliases": [],
                        "source_identity": "Country:USA",
                        "property_fingerprint": "usa-country",
                    },
                    identity_key="node:maritime-risk:country:united-states:legacy",
                    source_space="approved_graph",
                    source_key="Country:USA",
                    source_status="approved",
                    payload={"label": "United States (USA)", "properties": {"source_id": "Country:USA"}},
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
                    "name": "US",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "US",
                        "description": "Grounded source mention for a country abbreviation.",
                        "properties": {"source_id": "Country:US"},
                    },
                    "evidence_refs": ["source:us"],
                    "source_url": "https://example.org/us",
                    "confidence": 0.82,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "needs_review")
            self.assertEqual(candidate["status"], "needs_more_evidence")
            self.assertEqual(candidate["payload"]["match_method"], "embedding_degraded_alias_scan")
            self.assertEqual(
                candidate["payload"]["decision_reason"],
                "possible_duplicate_alias_conflict_embedding_degraded",
            )
            self.assertTrue(candidate["payload"]["possible_duplicate"])
            self.assertEqual(candidate["payload"]["matched_node_key"], "Country:USA")
            self.assertTrue(candidate["payload"]["possible_duplicate_candidates"])
            self.assertTrue(candidate["payload"]["embedding_degraded"])
            self.assertFalse(candidate["payload"]["llm_merge_decision_allowed"])

    def test_embedding_ready_short_alias_conflict_requires_review_when_vector_is_weak(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=OrthogonalShortAliasEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "United States (USA)",
                        "normalized_label": "united states usa",
                        "aliases": [],
                        "source_identity": "Country:USA",
                        "property_fingerprint": "usa-country",
                    },
                    identity_key="node:maritime-risk:country:usa:iso3=USA",
                    source_space="approved_graph_instance",
                    source_key="Country:USA",
                    source_status="approved",
                    payload={"label": "United States (USA)", "properties": {"source_pk": "USA"}},
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "US",
                    "payload": {
                        "ontology_type": "Country",
                        "label": "US",
                        "description": "Grounded source mention for a country abbreviation.",
                        "properties": {},
                    },
                    "evidence_refs": ["source:us"],
                    "source_url": "https://example.org/us",
                    "confidence": 0.82,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "merge_existing")
            self.assertEqual(candidate["status"], "draft")
            self.assertIn(candidate["payload"]["match_method"], {"stable_identity_key", "vector_embedding"})
            self.assertFalse(candidate["payload"]["possible_duplicate"])
            self.assertEqual(candidate["payload"]["matched_node_key"], "Country:USA")
            self.assertEqual(candidate["payload"]["matched_source"], "approved_graph_instance")
            self.assertFalse(candidate["payload"]["embedding_degraded"])
            self.assertFalse(candidate["payload"]["llm_merge_decision_allowed"])

    def test_node_dedup_does_not_return_type_artifact_as_entity_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=TypeArtifactNearestEmbeddingAdapter(),
            )
            session = agent.Session()
            try:
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "Country",
                        "normalized_label": "country",
                        "source_identity": "object:country",
                        "property_fingerprint": "country-type",
                    },
                    identity_key="node:maritime-risk:country:country:type",
                    source_space="approved_ontology_artifact",
                    source_key="object:country",
                    source_status="approved",
                    payload={"label": "Country"},
                )
                agent._upsert_identity_index_row(
                    session,
                    identity={
                        "kind": "node",
                        "entity_type": "Country",
                        "label": "USA",
                        "normalized_label": "usa",
                        "aliases": [],
                        "source_identity": "Country:USA",
                        "property_fingerprint": "usa-country",
                    },
                    identity_key="node:maritime-risk:country:usa:iso3=USA",
                    source_space="approved_graph_instance",
                    source_key="Country:USA",
                    source_status="approved",
                    payload={"label": "USA", "properties": {"source_pk": "USA"}},
                )
                session.commit()
                identity_index = agent._identity_index(session)
            finally:
                session.close()

            candidate = agent._annotate_candidate_identity(
                {
                    "element_type": "node",
                    "name": "US",
                    "payload": {"ontology_type": "Country", "label": "US", "properties": {}},
                    "evidence_refs": ["source:us"],
                    "confidence": 0.82,
                    "iteration": 1,
                },
                task_id="task-a",
                run_id="run-a",
                frontier_id="frontier-a",
                candidate_seq=1,
                identity_index=identity_index,
            )

            self.assertEqual(candidate["payload"]["dedup_decision"], "merge_existing")
            self.assertIn(candidate["payload"]["match_method"], {"stable_identity_key", "vector_embedding"})
            self.assertEqual(candidate["payload"]["matched_node_key"], "Country:USA")
            self.assertEqual(candidate["payload"]["matched_source"], "approved_graph_instance")
            self.assertNotEqual(candidate["payload"]["matched_node_key"], "object:country")

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
            self.assertGreaterEqual(rebuilt["identity_index_count"], 5)
            self.assertEqual(
                {row["source"] for row in rebuilt["identity_index"]},
                {"approved_ontology_artifact", "proposed_graph"},
            )
            self.assertIn(
                "ontology_concept",
                {row["identity"].get("kind") for row in rebuilt["identity_index"] if isinstance(row.get("identity"), dict)},
            )

            snapshot = agent.identity_index_snapshot()
            self.assertEqual(snapshot["identity_index_count"], rebuilt["identity_index_count"])
            self.assertEqual(
                {row["source_space"] for row in snapshot["identity_index"]},
                {"approved_ontology_artifact", "proposed_graph"},
            )

    def test_rebuild_identity_index_includes_rejected_proposed_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            prior_run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="rejected-run",
                objective="existing rejected node",
                status="completed",
            )
            session.add(prior_run)
            session.flush()
            session.add(
                ProposedGraphElement(
                    run_id=prior_run.id,
                    project_id="maritime-risk",
                    element_key="proposed-graph:maritime-risk:node:rejected-hormuz",
                    element_type="node",
                    name="Strait of Hormuz",
                    payload_json=json.dumps(
                        {
                            "ontology_type": "MaritimeChokepoint",
                            "label": "Strait of Hormuz",
                            "properties": {"source_id": "MaritimeChokepoint:Strait of Hormuz"},
                        }
                    ),
                    evidence_refs_json=json.dumps(["source:rejected"]),
                    confidence=0.7,
                    status="rejected",
                )
            )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            rebuilt = agent.rebuild_identity_index()
            rejected_rows = [
                row
                for row in rebuilt["identity_index"]
                if row["node_key"] == "proposed-graph:maritime-risk:node:rejected-hormuz"
            ]
            self.assertEqual(len(rejected_rows), 1)
            self.assertEqual(rejected_rows[0]["status"], "rejected")
            self.assertEqual(rejected_rows[0]["source"], "proposed_graph")

            with agent.Session() as session:
                identity_index = agent._identity_index(session)
                candidate = agent._annotate_candidate_identity(
                    {
                        "element_type": "node",
                        "name": "Strait of Hormuz",
                        "payload": {
                            "ontology_type": "MaritimeChokepoint",
                            "label": "Strait of Hormuz",
                            "properties": {"source_id": "MaritimeChokepoint:Strait of Hormuz"},
                        },
                        "evidence_refs": ["source:new"],
                        "confidence": 0.82,
                        "iteration": 1,
                    },
                    task_id="task-a",
                    run_id="run-a",
                    frontier_id="frontier-a",
                    candidate_seq=1,
                    identity_index=identity_index,
                )

            self.assertEqual(candidate["payload"]["matched_status"], "rejected")
            self.assertEqual(candidate["payload"]["matched_collection"], "rejected_objects")
            self.assertEqual(candidate["payload"]["dedup_decision"], "needs_review")

    def test_ontology_concept_identity_index_covers_approved_proposed_and_rejected(self):
        def ontology_item(label, *, artifact_type="action", trigger="Closure Event", effect="set status to Closed"):
            return {
                "element_type": "ontology_concept",
                "name": label,
                "payload": {
                    "artifact_type": artifact_type,
                    "label": label,
                    "trigger_event": trigger,
                    "target_object_types": ["Waterway"],
                    "input_parameters": ["status"],
                    "expected_effects": [effect],
                    "guardrails": ["requires review"],
                    "description": f"{label} ontology action.",
                    "evidence_quote": "Operators update waterway status after review.",
                    "source_url": "gpt_researcher://report/ontology-dedup",
                },
                "evidence_refs": ["gpt_researcher://report/ontology-dedup"],
                "source_url": "gpt_researcher://report/ontology-dedup",
                "confidence": 0.8,
                "iteration": 1,
            }

        def payload_for(item):
            payload = dict(item["payload"])
            identity = iterative_graph_enrichment_agent._candidate_identity_payload(item)
            payload["identity"] = identity
            payload["identity_key"] = iterative_graph_enrichment_agent._identity_key("maritime-risk", identity)
            return payload

        with tempfile.TemporaryDirectory() as tmpdir:
            db_url, _ = self._seed_db(tmpdir)
            engine = create_engine(db_url)
            Session = sessionmaker(bind=engine)
            session = Session()
            upsert_artifact(
                session,
                artifact_type="action",
                natural_key="update-waterway-status-approved",
                name="Update Waterway Status Approved",
                description="Approved operational ontology action.",
                payload={
                    "artifact_type": "action",
                    "label": "Update Waterway Status Approved",
                    "trigger_event": "Closure Event",
                    "target_object_types": ["Waterway"],
                    "input_parameters": ["status"],
                    "expected_effects": ["set status to Closed"],
                    "guardrails": ["requires review"],
                },
                source_refs=["gpt_researcher://report/ontology-approved"],
                source_agent="test",
                project_id="maritime-risk",
                status="approved",
            )
            prior_run = IterativeGraphEnrichmentRun(
                project_id="maritime-risk",
                run_key="ontology-dedup-run",
                objective="existing ontology concept candidates",
                status="completed",
            )
            session.add(prior_run)
            session.flush()
            for status, label in [
                ("draft", "Update Waterway Status Proposed"),
                ("approved", "Update Waterway Status Approved Proposal"),
                ("rejected", "Update Waterway Status Rejected"),
            ]:
                item = ontology_item(label)
                session.add(
                    ProposedGraphElement(
                        run_id=prior_run.id,
                        project_id="maritime-risk",
                        element_key=f"proposed-graph:maritime-risk:ontology-concept:{status}",
                        element_type="ontology_concept",
                        name=label,
                        payload_json=json.dumps(payload_for(item)),
                        evidence_refs_json=json.dumps(item["evidence_refs"]),
                        source_url=item["source_url"],
                        confidence=0.8,
                        status=status,
                    )
                )
            session.commit()
            session.close()

            agent = IterativeGraphEnrichmentAgent(
                db_url,
                tenant="maritime-risk",
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            rebuilt = agent.rebuild_identity_index()
            ontology_rows = [
                row
                for row in rebuilt["identity_index"]
                if row.get("identity", {}).get("kind") == "ontology_concept"
            ]
            self.assertTrue(ontology_rows)
            self.assertIn("approved_ontology_artifact", {row["source"] for row in ontology_rows})
            self.assertIn("proposed_graph", {row["source"] for row in ontology_rows})
            self.assertIn("approved", {row["status"] for row in ontology_rows})
            self.assertIn("proposed", {row["status"] for row in ontology_rows})
            self.assertIn("rejected", {row["status"] for row in ontology_rows})

            with agent.Session() as session:
                identity_index = agent._identity_index(session)
                approved = agent._annotate_candidate_identity(
                    ontology_item("Update Waterway Status Approved"),
                    task_id="task-onto",
                    run_id="run-onto",
                    frontier_id="frontier-onto",
                    candidate_seq=1,
                    identity_index=identity_index,
                )
                proposed = agent._annotate_candidate_identity(
                    ontology_item("Update Waterway Status Proposed"),
                    task_id="task-onto",
                    run_id="run-onto",
                    frontier_id="frontier-onto",
                    candidate_seq=2,
                    identity_index=identity_index,
                )
                rejected = agent._annotate_candidate_identity(
                    ontology_item("Update Waterway Status Rejected"),
                    task_id="task-onto",
                    run_id="run-onto",
                    frontier_id="frontier-onto",
                    candidate_seq=3,
                    identity_index=identity_index,
                )

            self.assertEqual(approved["payload"]["dedup_decision"], "merge_existing")
            self.assertEqual(approved["payload"]["matched_collection"], "approved_objects")
            self.assertEqual(proposed["payload"]["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(proposed["payload"]["matched_collection"], "current_propose_set")
            self.assertEqual(rejected["payload"]["dedup_decision"], "needs_review")
            self.assertEqual(rejected["payload"]["matched_collection"], "rejected_objects")

    def test_rebuild_identity_index_includes_approved_schema_graph_instances(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_db_url = f"sqlite:///{Path(tmpdir) / 'metadata.db'}"
            metadata_engine = create_engine(metadata_db_url)
            ensure_artifact_schema(metadata_engine)
            Session = sessionmaker(bind=metadata_engine)
            session = Session()
            upsert_artifact(
                session,
                artifact_type="object",
                natural_key="country",
                name="Country",
                description="Country node inferred from reviewed source schema.",
                payload={
                    "object_name": "Country",
                    "mapped_table_names": ["countries"],
                    "primary_key": "iso3",
                    "properties": ["iso3"],
                    "llm_inferred": True,
                    "prompt_version": "schema_graph_modeling_v1",
                },
                source_refs=["table:countries"],
                source_agent="SchemaGraphModelingAgent",
                project_id="maritime-risk",
                status="approved",
            )
            session.commit()
            session.close()

            source_db_url = f"sqlite:///{Path(tmpdir) / 'source.db'}"
            source_engine = create_engine(source_db_url)
            with source_engine.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE countries (iso3 TEXT PRIMARY KEY)")
                conn.exec_driver_sql("INSERT INTO countries (iso3) VALUES ('USA')")

            agent = IterativeGraphEnrichmentAgent(
                metadata_db_url,
                tenant="maritime-risk",
                source_db_url=source_db_url,
                embedding_adapter=StaticTestEmbeddingAdapter(),
            )
            rebuilt = agent.rebuild_identity_index()
            instance_rows = [
                row
                for row in rebuilt["identity_index"]
                if row["source"] == "approved_graph_instance" and row["node_key"] == "Country:USA"
            ]
            self.assertEqual(len(instance_rows), 1)
            self.assertEqual(instance_rows[0]["status"], "approved")

            with agent.Session() as session:
                identity_index = agent._identity_index(session)
                candidate = {
                    "element_type": "node",
                    "name": "US",
                    "payload": {"ontology_type": "Country", "label": "US", "properties": {}},
                    "evidence_refs": ["source:us-reference"],
                }
                identity = iterative_graph_enrichment_agent._candidate_identity_payload(candidate)
                match = agent._best_identity_match(identity, identity_index, payload=candidate["payload"])

            self.assertIn(match["match_method"], {"stable_identity_key", "vector_embedding"})
            self.assertEqual(match["matched_source"], "approved_graph_instance")
            self.assertEqual(match["matched_status"], "approved")
            self.assertEqual(match["matched_node_key"], "Country:USA")
            self.assertEqual(iterative_graph_enrichment_agent._dedup_decision(match), "merge_existing")

    def test_approved_graph_exact_object_beats_wrong_vector_neighbor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_db_url = f"sqlite:///{Path(tmpdir) / 'metadata.db'}"
            metadata_engine = create_engine(metadata_db_url)
            ensure_artifact_schema(metadata_engine)
            Session = sessionmaker(bind=metadata_engine)
            session = Session()
            upsert_artifact(
                session,
                artifact_type="object",
                natural_key="maritime_chokepoint",
                name="Maritime Chokepoint",
                description="Reviewed chokepoint node.",
                payload={
                    "object_name": "Maritime Chokepoint",
                    "mapped_table_names": ["chokepoints"],
                    "primary_key": "canal",
                    "properties": ["canal"],
                    "llm_inferred": True,
                    "prompt_version": "schema_graph_modeling_v1",
                },
                source_refs=["table:chokepoints"],
                source_agent="SchemaGraphModelingAgent",
                project_id="maritime-risk",
                status="approved",
            )
            session.commit()
            session.close()

            source_db_url = f"sqlite:///{Path(tmpdir) / 'source.db'}"
            source_engine = create_engine(source_db_url)
            with source_engine.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE chokepoints (canal TEXT PRIMARY KEY)")
                conn.exec_driver_sql("INSERT INTO chokepoints (canal) VALUES ('Lombok Strait')")
                conn.exec_driver_sql("INSERT INTO chokepoints (canal) VALUES ('Strait of Hormuz')")

            agent = IterativeGraphEnrichmentAgent(
                metadata_db_url,
                tenant="maritime-risk",
                source_db_url=source_db_url,
                embedding_adapter=WrongVectorNeighborEmbeddingAdapter(),
            )
            agent.rebuild_identity_index()
            with agent.Session() as session:
                identity_index = agent._identity_index(session)
                candidate = agent._annotate_candidate_identity(
                    {
                        "element_type": "node",
                        "name": "Strait of Hormuz",
                        "payload": {
                            "ontology_type": "Maritime Chokepoint",
                            "label": "Strait of Hormuz",
                            "properties": {"source_id": "MaritimeChokepoint:Strait of Hormuz"},
                        },
                        "evidence_refs": ["source:hormuz"],
                        "confidence": 0.82,
                        "iteration": 1,
                    },
                    task_id="task-a",
                    run_id="run-a",
                    frontier_id="frontier-a",
                    candidate_seq=1,
                    identity_index=identity_index,
                )

            self.assertEqual(candidate["payload"]["dedup_decision"], "merge_existing")
            self.assertEqual(candidate["payload"]["matched_node_key"], "MaritimeChokepoint:Strait of Hormuz")
            self.assertEqual(candidate["payload"]["matched_source"], "approved_graph_instance")
            self.assertEqual(candidate["payload"]["matched_collection"], "approved_objects")

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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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
                embedding_adapter=StaticTestEmbeddingAdapter(),
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

    def test_similar_existing_proposed_node_above_threshold_is_skipped_as_duplicate(self):
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
            chokepoints = [
                item
                for item in result["proposed_graph"]
                if item["element_type"] == "node" and item["payload"].get("label") == "Bab el-Mandeb Strait"
            ]

            self.assertFalse(chokepoints)
            self.assertTrue(
                any(
                    item.get("element_type") == "node"
                    and item.get("name") == "Bab el-Mandeb Strait"
                    and item.get("dedup_decision") == "duplicate_existing_proposal"
                    and item.get("reason") == "duplicate_endpoint_node_not_proposed"
                    for item in result["run"]["skipped_sources"]
                )
            )
            edge = next(item for item in result["proposed_graph"] if item["element_type"] == "edge")
            self.assertEqual(edge["status"], "draft")
            self.assertFalse(edge["payload"]["endpoint_review_required"])
            target_evidence = edge["payload"]["endpoint_dedup_evidence"]["target"]
            self.assertEqual(target_evidence["dedup_decision"], "duplicate_existing_proposal")
            self.assertEqual(target_evidence["matched_node_key"], "proposed-graph:maritime-risk:node:ambiguous-bab")
            self.assertEqual(target_evidence["decision_reason"], "node_similarity_confidence_threshold_met")
            self.assertFalse(target_evidence["review_required"])
            self.assertFalse(target_evidence["proposed_node_created"])


if __name__ == "__main__":
    unittest.main()
