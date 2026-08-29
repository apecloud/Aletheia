#!/usr/bin/env python3
"""ontology_label_embeddings: question-to-node embedding lookup.

Real Postgres (GraphIdentityIndex), each test gets its own tenant_id so
state never leaks between tests, cleaned up in tearDown -- same convention
as MaterializationTest in test_hotpotqa_nebula_benchmark.py. Most tests
mock the embedding adapter for determinism; one test uses the real model
to reproduce the exact calibration case DEFAULT_MAX_DISTANCE was picked
against (see that constant's own comment).

Run: python -m unittest tests.test_ontology_label_embeddings
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aletheia.ontology.store import ensure_artifact_schema, GraphIdentityIndex
from aletheia.ontology.label_embeddings import (
    SOURCE_SPACE, find_nearest_label, find_nearest_labels, label_embedding_count, sync_label_embeddings,
)
from aletheia.core.tenant_registry import default_metadata_db_url


class FakeEmbeddingAdapter:
    """Deterministic stand-in: embeds a string to a fixed small vector
    keyed by an exact-match dict, so distance is trivially predictable."""

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors

    def embed(self, text: str) -> dict:
        vector = self.vectors.get(text)
        if vector is None:
            return {"status": "degraded", "reason": "no_fixture", "model": "fake", "vector": None, "dim": None}
        return {"status": "ready", "model": "fake", "vector": vector, "dim": len(vector)}


class OntologyLabelEmbeddingsTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"ontology_label_embeddings_unittest_{self._testMethodName}"

    def tearDown(self):
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def test_sync_then_find_nearest_returns_closest_node(self):
        nodes = [
            {"id": "Waterway:red_sea", "type": "Waterway", "label": "Red Sea"},
            {"id": "Waterway:gulf_of_aden", "type": "Waterway", "label": "Gulf of Aden"},
        ]
        adapter = FakeEmbeddingAdapter({
            "Waterway Red Sea": [1.0, 0.0],
            "Waterway Gulf of Aden": [0.0, 1.0],
            "question about red sea": [0.9, 0.1],
        })
        synced = sync_label_embeddings(self.session, self.tenant_id, nodes, embedding_adapter=adapter)
        self.assertEqual(synced, 2)

        matched = find_nearest_label(
            self.session, self.tenant_id, "question about red sea",
            embedding_adapter=adapter, max_distance=0.5,
        )
        self.assertEqual(matched, "Waterway:red_sea")

    def test_find_nearest_labels_returns_k_sorted_by_distance(self):
        nodes = [
            {"id": "Waterway:red_sea", "type": "Waterway", "label": "Red Sea"},
            {"id": "Waterway:gulf_of_aden", "type": "Waterway", "label": "Gulf of Aden"},
            {"id": "Waterway:suez_canal", "type": "Waterway", "label": "Suez Canal"},
        ]
        adapter = FakeEmbeddingAdapter({
            "Waterway Red Sea": [1.0, 0.0, 0.0],
            "Waterway Gulf of Aden": [0.9, 0.1, 0.0],
            "Waterway Suez Canal": [0.0, 0.0, 1.0],
            "red sea question": [1.0, 0.05, 0.0],
        })
        sync_label_embeddings(self.session, self.tenant_id, nodes, embedding_adapter=adapter)

        matches = find_nearest_labels(
            self.session, self.tenant_id, "red sea question",
            embedding_adapter=adapter, k=2, max_distance=0.9,
        )
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches[0]["node_id"], "Waterway:red_sea")
        self.assertEqual(matches[1]["node_id"], "Waterway:gulf_of_aden")
        self.assertLess(matches[0]["distance"], matches[1]["distance"])

    def test_find_nearest_returns_none_beyond_max_distance(self):
        nodes = [{"id": "Waterway:red_sea", "type": "Waterway", "label": "Red Sea"}]
        adapter = FakeEmbeddingAdapter({
            "Waterway Red Sea": [1.0, 0.0],
            "unrelated summary question": [0.0, 1.0],  # cosine distance 1.0 from [1,0]
        })
        sync_label_embeddings(self.session, self.tenant_id, nodes, embedding_adapter=adapter)

        matched = find_nearest_label(
            self.session, self.tenant_id, "unrelated summary question",
            embedding_adapter=adapter, max_distance=0.4,
        )
        self.assertIsNone(matched)

    def test_find_nearest_on_empty_index_returns_none(self):
        adapter = FakeEmbeddingAdapter({"anything": [1.0, 0.0]})
        matched = find_nearest_label(self.session, self.tenant_id, "anything", embedding_adapter=adapter)
        self.assertIsNone(matched)

    def test_description_is_preferred_over_type_and_label_when_present(self):
        """A node carrying an LLM-generated "description" (see
        llm_planner.EntityDescriptionResult) embeds that text instead of
        the bare "{type} {label}" convention -- richer semantic content
        for query-time entity linking (see module docstring)."""
        node = {
            "id": "Person:beethoven", "type": "Person", "label": "Ludwig van Beethoven",
            "description": "A composer who wrote Symphony No. 7, dedicated to Count Moritz von Fries.",
        }
        adapter = FakeEmbeddingAdapter({
            "A composer who wrote Symphony No. 7, dedicated to Count Moritz von Fries.": [1.0, 0.0],
            "Person Ludwig van Beethoven": [0.0, 1.0],
            "a question about the beethoven symphony dedication": [0.95, 0.05],
        })
        sync_label_embeddings(self.session, self.tenant_id, [node], embedding_adapter=adapter)

        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE, source_key="Person:beethoven")
            .first()
        )
        self.assertEqual(row.dedup_text, node["description"])

        matched = find_nearest_label(
            self.session, self.tenant_id, "a question about the beethoven symphony dedication",
            embedding_adapter=adapter, max_distance=0.5,
        )
        self.assertEqual(matched, "Person:beethoven")

    def test_missing_or_empty_description_falls_back_to_type_and_label(self):
        adapter = FakeEmbeddingAdapter({"Person Ludwig van Beethoven": [1.0, 0.0]})
        node_no_key = {"id": "Person:beethoven", "type": "Person", "label": "Ludwig van Beethoven"}
        node_blank_key = {**node_no_key, "description": "   "}

        sync_label_embeddings(self.session, self.tenant_id, [node_no_key], embedding_adapter=adapter)
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE, source_key="Person:beethoven")
            .first()
        )
        self.assertEqual(row.dedup_text, "Person Ludwig van Beethoven")

        sync_label_embeddings(self.session, self.tenant_id, [node_blank_key], embedding_adapter=adapter)
        self.session.refresh(row)
        self.assertEqual(row.dedup_text, "Person Ludwig van Beethoven")

    def test_sync_is_idempotent_and_updates_changed_label(self):
        node = {"id": "Waterway:red_sea", "type": "Waterway", "label": "Red Sea"}
        adapter = FakeEmbeddingAdapter({
            "Waterway Red Sea": [1.0, 0.0],
            "Waterway The Red Sea": [0.0, 1.0],
        })
        sync_label_embeddings(self.session, self.tenant_id, [node], embedding_adapter=adapter)
        self.assertEqual(label_embedding_count(self.session, self.tenant_id), 1)

        # Re-sync with an unchanged label should not add a duplicate row.
        sync_label_embeddings(self.session, self.tenant_id, [node], embedding_adapter=adapter)
        self.assertEqual(label_embedding_count(self.session, self.tenant_id), 1)

        # A relabeled node updates the existing row's embedding in place.
        renamed = {"id": "Waterway:red_sea", "type": "Waterway", "label": "The Red Sea"}
        sync_label_embeddings(self.session, self.tenant_id, [renamed], embedding_adapter=adapter)
        self.assertEqual(label_embedding_count(self.session, self.tenant_id), 1)
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE, source_key="Waterway:red_sea")
            .first()
        )
        self.assertEqual(row.dedup_text, "Waterway The Red Sea")


class RealEmbeddingCalibrationTest(unittest.TestCase):
    """Reproduces the exact calibration case DEFAULT_MAX_DISTANCE was
    picked against (see that constant's comment in ontology_label_
    embeddings.py) -- the same fixture as tests/test_continuous_enrichment_
    frontier.py's graph_rag_query_context assertions, using the real
    embedding model instead of a fake one."""

    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"ontology_label_embeddings_real_unittest_{self._testMethodName}"

    def tearDown(self):
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def test_paraphrased_question_matches_red_sea_not_summary_question(self):
        nodes = [
            {"id": "Waterway:red_sea", "type": "Waterway", "label": "Red Sea"},
            {"id": "Waterway:gulf_of_aden", "type": "Waterway", "label": "Gulf of Aden"},
        ]
        sync_label_embeddings(self.session, self.tenant_id, nodes)

        self.assertEqual(
            find_nearest_label(self.session, self.tenant_id, "What is connected to the Red Sea?"),
            "Waterway:red_sea",
        )
        self.assertIsNone(
            find_nearest_label(self.session, self.tenant_id, "Summarize the maritime graph.")
        )


if __name__ == "__main__":
    unittest.main()
