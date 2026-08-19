#!/usr/bin/env python3
"""graph_entity_resolver.resolve_or_mint_vertex_id: exact-identity-key +
embedding-near-duplicate dedup, and the ``is_literal`` carve-out that skips
the embedding tier for literal scalar values (see its docstring).

Real Postgres (GraphIdentityIndex), each test gets its own tenant_id so
state never leaks between tests, cleaned up in tearDown -- same convention
as MaterializationTest in test_hotpotqa_nebula_benchmark.py. A
FakeEmbeddingAdapter gives deterministic distances for most tests; one
test reproduces the exact real-model calibration case that surfaced this
bug (two mountains' heights merged into one vertex).

Run: python -m unittest tests.test_graph_entity_resolver
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ontology_artifacts import ensure_artifact_schema, GraphIdentityIndex
from graph_entity_resolver import resolve_or_mint_vertex_id
from tenant_registry import default_metadata_db_url


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


class ResolveOrMintVertexIdTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"graph_entity_resolver_unittest_{self._testMethodName}"
        self._next_id = 0

    def tearDown(self):
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def _mint_id(self) -> str:
        self._next_id += 1
        return f"minted-{self._next_id}"

    def test_non_literal_near_duplicate_labels_merge_via_embedding(self):
        """A named entity's spelling variant across two mentions should
        still merge -- this is the fuzzy-matching behavior is_literal=True
        deliberately opts out of, not something the fix should break."""
        adapter = FakeEmbeddingAdapter({
            "Person Stephen Covey": [1.0, 0.0],
            "Person Stephen R. Covey": [0.99, 0.02],  # near-identical, same person
        })
        vid1, method1 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="Stephen Covey",
            candidate_type="Person", evidence_qid="q1", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=False,
        )
        self.assertEqual(method1, "new_vertex")

        vid2, method2 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="Stephen R. Covey",
            candidate_type="Person", evidence_qid="q2", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=False,
        )
        self.assertEqual(method2, "vector_embedding")
        self.assertEqual(vid1, vid2)

    def test_literal_near_duplicate_values_do_not_merge(self):
        """The bug this fix closes: two DIFFERENT literal quantities that
        happen to read as near-identical text (e.g. two mountains' heights,
        "7821 m" vs "7823 m") must not be merged just because they embed
        close together -- see resolve_or_mint_vertex_id's is_literal
        docstring for the measured real-model distance (0.034, far inside
        VECTOR_DUPLICATE_DISTANCE=0.12) that caused this in production."""
        adapter = FakeEmbeddingAdapter({
            "Quantity 7821 m": [1.0, 0.0],
            "Quantity 7823 m": [0.999, 0.002],  # textually near-identical, but a DIFFERENT value
        })
        vid1, method1 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="7821 m",
            candidate_type="Quantity", evidence_qid="q1", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=True,
        )
        self.assertEqual(method1, "new_vertex")

        vid2, method2 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="7823 m",
            candidate_type="Quantity", evidence_qid="q2", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=True,
        )
        self.assertEqual(method2, "new_vertex")
        self.assertNotEqual(vid1, vid2)

    def test_literal_exact_repeat_still_dedupes_via_identity_key(self):
        """is_literal only disables the FUZZY (embedding) tier -- the exact
        identity-key tier still merges a truly repeated literal value
        (e.g. "American" mentioned as a nationality in two different
        questions should still resolve to one vertex, not one per mention)."""
        adapter = FakeEmbeddingAdapter({"Value American": [1.0, 0.0]})
        vid1, method1 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="American",
            candidate_type="Value", evidence_qid="q1", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=True,
        )
        self.assertEqual(method1, "new_vertex")

        vid2, method2 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="American",
            candidate_type="Value", evidence_qid="q2", mint_id=self._mint_id,
            embedding_adapter=adapter, is_literal=True,
        )
        self.assertEqual(method2, "exact_identity_key")
        self.assertEqual(vid1, vid2)

    def test_is_literal_defaults_to_false_for_backward_compatibility(self):
        """Existing callers that don't pass is_literal (e.g. from_title,
        always a given non-literal title) keep today's fuzzy-matching
        behavior unchanged."""
        adapter = FakeEmbeddingAdapter({
            "Team Riverside Stadium": [1.0, 0.0],
            "Team Riverside Stadiums": [0.999, 0.001],
        })
        vid1, _ = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="Riverside Stadium",
            candidate_type="Team", evidence_qid="q1", mint_id=self._mint_id, embedding_adapter=adapter,
        )
        vid2, method2 = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="Riverside Stadiums",
            candidate_type="Team", evidence_qid="q2", mint_id=self._mint_id, embedding_adapter=adapter,
        )
        self.assertEqual(method2, "vector_embedding")
        self.assertEqual(vid1, vid2)


class RealEmbeddingCalibrationTest(unittest.TestCase):
    """Reproduces the exact production case that surfaced this bug, using
    the real embedding model instead of a fake one -- two different
    mountains' heights ("7821 m" and "7823 m") must not collapse into one
    vertex when is_literal=True."""

    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"graph_entity_resolver_real_unittest_{self._testMethodName}"
        self._next_id = 0

    def tearDown(self):
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def _mint_id(self) -> str:
        self._next_id += 1
        return f"minted-{self._next_id}"

    def test_masherbrum_and_khunyang_chhish_heights_stay_distinct(self):
        vid1, _ = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="7821 m",
            candidate_type="Quantity", evidence_qid="masherbrum", mint_id=self._mint_id, is_literal=True,
        )
        vid2, _ = resolve_or_mint_vertex_id(
            self.session, tenant_id=self.tenant_id, candidate_label="7823 m",
            candidate_type="Quantity", evidence_qid="khunyang_chhish", mint_id=self._mint_id, is_literal=True,
        )
        self.assertNotEqual(vid1, vid2)


if __name__ == "__main__":
    unittest.main()
