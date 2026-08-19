#!/usr/bin/env python3
"""HotpotQA Nebula-native benchmark: materialization + graph-hit scoring tests.

Deterministic tests (real Postgres, no live Nebula) exercise
``materialize_hotpotqa_questions`` -- including cross-question entity
dedup (``agents/graph_entity_resolver.py``) and typed node/edge
registration (``agents/graph_ontology_registry.py``) -- and
``graph_hit``/``traverse``'s row-parsing helper given fixed fake input.
One additional test connects to a real local Nebula cluster and is skipped
(not failed) if it isn't reachable -- this repo's Nebula containers are
optional local infra, not a CI dependency.

Run: python -m unittest tests.test_hotpotqa_nebula_benchmark
"""

from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from passage_relation_extraction import LocalGraph, MentionedEntity, Triple
from graph_entity_resolver import SOURCE_SPACE_DESCRIPTION  # noqa: E402
from hotpotqa_entity_ids import entity_id
from import_hotpotqa_nebula_tenant import materialize_hotpotqa_questions  # noqa: E402
from llm_planner import EntityDescriptionResult  # noqa: E402
from run_hotpotqa_nebula_e2e_benchmark import gather_facts, graph_hit  # noqa: E402
from ontology_artifacts import ensure_artifact_schema, GraphIdentityIndex, OntologyArtifact  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402


class FakeExtractor:
    def __init__(self, graphs_by_question: dict[str, LocalGraph]):
        self.graphs_by_question = graphs_by_question

    def extract_local_graph(self, question, context, approved_node_types=None):
        return self.graphs_by_question[question]


class MaterializationTest(unittest.TestCase):
    """Real Postgres (for the ontology registry + identity index) -- no
    live Nebula. Each test gets its own tenant_id so dedup/type-registration
    state never leaks between tests, cleaned up in tearDown."""

    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"hotpotqa_materialize_unittest_{self._testMethodName}"

    def tearDown(self):
        self.session.query(OntologyArtifact).filter_by(project_id=self.tenant_id).delete()
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def _materialize(self, cases, extractor, relation_catalog=None, **kwargs):
        # build_description_embeddings defaults off here -- covered by its
        # own dedicated tests below (DescriptionEmbeddingTest) with a fake
        # planner, so the rest of this class's tests (unrelated to that
        # feature) don't pay for the extra embedding-adapter calls.
        kwargs.setdefault("build_description_embeddings", False)
        return materialize_hotpotqa_questions(
            self.session, cases, extractor, relation_catalog, tenant_id=self.tenant_id, **kwargs
        )

    def _case(self, qid: str, question: str, answer: str) -> dict:
        return {
            "qid": qid,
            "question": question,
            "answer": answer,
            "type": "bridge",
            "level": "hard",
            "context": [["Riverside Stadium", ["Riverside Stadium sentence."]], ["Lincoln Tigers", ["Lincoln Tigers sentence."]]],
        }

    def _all_vertex_rows(self, materialized) -> list[dict]:
        rows = []
        for group in materialized["vertex_rows_by_type"].values():
            rows.extend(group)
        return rows

    def _all_edge_rows(self, materialized) -> list[dict]:
        rows = []
        for group in materialized["edge_rows_by_type"].values():
            rows.extend(group)
        return rows

    def test_vertex_ids_are_qid_scoped(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        ids = {row["id"] for row in self._all_vertex_rows(materialized)}
        self.assertEqual(ids, {entity_id("q1", "Riverside Stadium"), entity_id("q1", "Lincoln Tigers")})

    def test_all_vertex_rows_share_identical_keys(self):
        # NebulaGraphClient.insert_vertices builds ONE fixed column list from
        # rows[0].keys() -- every row (within one type's insert call) must
        # have exactly the same keys or the generated INSERT VERTEX
        # statement silently misaligns values.
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        for rows in materialized["vertex_rows_by_type"].values():
            key_sets = {frozenset(row.keys()) for row in rows}
            self.assertEqual(len(key_sets), 1)
            self.assertEqual(set(next(iter(key_sets))), {"id", "label"})

    def test_all_edge_rows_share_identical_keys(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        for rows in materialized["edge_rows_by_type"].values():
            key_sets = {frozenset(row.keys()) for row in rows}
            self.assertEqual(len(key_sets), 1)
            self.assertEqual(set(next(iter(key_sets))), {"source_id", "target_id", "evidence"})

    def test_two_questions_with_same_relation_label_but_different_entities_stay_disjoint(self):
        """Reusing the same relation NAME ("rel") across two questions must
        not, by itself, cause any vertex/edge collision -- entity identity
        merging (a separate, deliberate behavior; see
        test_same_entity_across_two_questions_dedups_to_one_vertex) is keyed
        on entity label+type, not on which relation names happen to repeat."""
        q1 = self._case("q1", "Q1?", "answer1")
        q2 = self._case("q2", "Q2?", "answer2")
        graph1 = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        graph2 = LocalGraph(topic_title="Sunset Arena", triples=[Triple("Sunset Arena", "rel", "Maple Hawks", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph1, "Q2?": graph2})

        materialized = self._materialize([q1, q2], extractor)

        pairs = {(r["source_id"], r["target_id"]) for r in self._all_edge_rows(materialized)}
        self.assertEqual(
            pairs,
            {
                (entity_id("q1", "Riverside Stadium"), entity_id("q1", "Lincoln Tigers")),
                (entity_id("q2", "Sunset Arena"), entity_id("q2", "Maple Hawks")),
            },
        )

    def test_center_node_uses_topic_title(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Lincoln Tigers", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        self.assertEqual(materialized["cases"][0]["center_node"], entity_id("q1", "Lincoln Tigers"))

    def test_extraction_error_yields_no_center_node_but_no_crash(self):
        q1 = self._case("q1", "Q1?", "answer1")
        failed_graph = LocalGraph(error="parse failed", used_fallback=True, error_type="runtime_invalid")
        extractor = FakeExtractor({"Q1?": failed_graph})

        materialized = self._materialize([q1], extractor)

        self.assertEqual(materialized["extraction_errors"], 1)
        self.assertEqual(materialized["cases"][0]["triple_count"], 0)

    def test_case_with_entity_mentions_gets_additional_center_nodes(self):
        """additional_center_nodes comes from the extraction LLM's own
        entity_mentions judgment -- not gated on case["type"], since
        HotpotQA's own bridge/comparison label doesn't reliably reflect
        whether the question names multiple specific subjects."""
        q1 = {
            "qid": "q1",
            "question": "Which was founded first, Dain Rauscher Wessels or Berenberg Bank?",
            "answer": "Dain Rauscher Wessels",
            "type": "bridge",  # deliberately NOT "comparison" -- must not matter
            "level": "hard",
            "context": [
                ["Dain Rauscher Wessels", ["Founded in 1990."]],
                ["Berenberg Bank", ["Founded in 1590."]],
                ["Unrelated Title", ["Not mentioned in the question."]],
            ],
        }
        graph = LocalGraph(
            topic_title="Berenberg Bank",
            entity_mentions=["Dain Rauscher Wessels"],
            triples=[Triple("Berenberg Bank", "founded_in", "1590", is_literal=True)],
        )
        extractor = FakeExtractor({q1["question"]: graph})

        materialized = self._materialize([q1], extractor)

        case = materialized["cases"][0]
        self.assertEqual(
            set(case["additional_center_nodes"]),
            {entity_id("q1", "Dain Rauscher Wessels")},
        )

    def test_case_without_entity_mentions_gets_no_additional_center_nodes(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        self.assertEqual(materialized["cases"][0]["additional_center_nodes"], [])

    def test_same_entity_across_two_questions_dedups_to_one_vertex(self):
        """Cross-question entity merging: the same label+type mentioned
        under two different qids resolves to one vertex id (the first
        qid's), not two -- moving HotpotQA from closed-world per-question
        subgraphs to a shared, deduplicated graph."""
        q1 = self._case("q1", "Q1?", "answer1")
        q2 = self._case("q2", "Q2?", "answer2")
        graph1 = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel", "Barry Switzer", is_literal=False, from_type="Team", to_type="Person")])
        graph2 = LocalGraph(topic_title="Riverside Stadium", triples=[Triple("Riverside Stadium", "rel2", "Barry Switzer", is_literal=False, from_type="Team", to_type="Person")])
        extractor = FakeExtractor({"Q1?": graph1, "Q2?": graph2})

        materialized = self._materialize([q1, q2], extractor)

        person_ids = {row["id"] for row in materialized["vertex_rows_by_type"].get("Person", [])}
        self.assertEqual(len(person_ids), 1, f"expected one deduplicated Person vertex, got {person_ids}")
        self.assertEqual(person_ids, {entity_id("q1", "Barry Switzer")})

    def test_node_and_edge_types_registered_in_ontology_registry(self):
        import graph_ontology_registry as registry

        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple("Riverside Stadium", "coached", "Lincoln Tigers", is_literal=False, from_type="Team", to_type="Person")],
        )
        extractor = FakeExtractor({"Q1?": graph})

        self._materialize([q1], extractor)

        node_types = {n["name"] for n in registry.get_approved_node_types(self.session, self.tenant_id)}
        edge_types = {e["name"] for e in registry.get_approved_edge_types(self.session, self.tenant_id)}
        self.assertEqual(node_types, {"Team", "Person"})
        self.assertEqual(edge_types, {"coached"})
        coached = registry.get_edge_type(self.session, self.tenant_id, "coached")
        self.assertEqual(coached["domain"], ["Team"])
        self.assertEqual(coached["range"], ["Person"])

    def test_mentioned_entity_not_in_any_triple_still_gets_a_vertex(self):
        """The case mentioned_entities exists for: an entity named only in
        passing (never a triple's from_title/to_value) still becomes its
        own vertex -- see passage_relation_extraction.MentionedEntity's
        docstring (borrowed from GraphRAG's own extraction pipeline)."""
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)],
            mentioned_entities=[MentionedEntity(name="Some Coach", entity_type="Person", description="Coached the team.")],
        )
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor)

        person_ids = {row["id"] for row in materialized["vertex_rows_by_type"].get("Person", [])}
        self.assertEqual(person_ids, {entity_id("q1", "Some Coach")})

    def test_mentioned_entities_across_two_questions_dedupe_like_topic_titles(self):
        """mentioned_entities use the same fuzzy-dedup-eligible path as
        topic titles (is_literal=False default) -- the SAME real person
        named across two questions' mentioned_entities resolves to one
        vertex, not two."""
        q1 = self._case("q1", "Q1?", "answer1")
        q2 = self._case("q2", "Q2?", "answer2")
        graph1 = LocalGraph(topic_title="Riverside Stadium", mentioned_entities=[
            MentionedEntity(name="Some Coach", entity_type="Person", description="First mention."),
        ])
        graph2 = LocalGraph(topic_title="Riverside Stadium", mentioned_entities=[
            MentionedEntity(name="Some Coach", entity_type="Person", description="Second mention."),
        ])
        extractor = FakeExtractor({"Q1?": graph1, "Q2?": graph2})

        materialized = self._materialize([q1, q2], extractor)

        person_ids = {row["id"] for row in materialized["vertex_rows_by_type"].get("Person", [])}
        self.assertEqual(len(person_ids), 1, f"expected one deduplicated Person vertex, got {person_ids}")

    def test_include_mentioned_entities_false_skips_them(self):
        """--skip-mentioned-entities reproduces the pre-mentioned_entities
        checkpoint config exactly -- see materialize_hotpotqa_questions's
        include_mentioned_entities docstring note (measured 83% vs.
        net-neutral-to-negative on the 100-question HotpotQA benchmark)."""
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)],
            mentioned_entities=[MentionedEntity(name="Some Coach", entity_type="Person", description="Coached the team.")],
        )
        extractor = FakeExtractor({"Q1?": graph})

        materialized = self._materialize([q1], extractor, include_mentioned_entities=False)

        self.assertNotIn("Person", materialized["vertex_rows_by_type"])


class FakePlanner:
    """Duck-typed stand-in for LLMPlanner.summarize_entity_description --
    deterministic, no network call, so DescriptionEmbeddingTest doesn't
    depend on a live LLM provider."""

    def __init__(self):
        self.calls: list[tuple[str, str, list[str]]] = []

    def summarize_entity_description(self, label, entity_type, evidence):
        self.calls.append((label, entity_type, list(evidence)))
        return EntityDescriptionResult(description=f"{label} is a {entity_type}. " + " ".join(evidence))


class DescriptionEmbeddingTest(unittest.TestCase):
    """materialize_hotpotqa_questions's description-embedding post-pass
    (see llm_planner.EntityDescriptionResult's docstring) -- borrowed from
    GraphRAG's own construction pipeline. Real Postgres (for the identity
    index) and the real embedding model (same convention already used by
    MaterializationTest's underlying dedup path), but a fake planner so
    this doesn't depend on a live LLM provider."""

    def setUp(self):
        engine = create_engine(default_metadata_db_url())
        ensure_artifact_schema(engine)
        self.session = sessionmaker(bind=engine)()
        self.tenant_id = f"hotpotqa_description_unittest_{self._testMethodName}"

    def tearDown(self):
        self.session.query(OntologyArtifact).filter_by(project_id=self.tenant_id).delete()
        self.session.query(GraphIdentityIndex).filter_by(project_id=self.tenant_id).delete()
        self.session.commit()
        self.session.close()

    def _case(self, qid: str, question: str, answer: str) -> dict:
        return {
            "qid": qid,
            "question": question,
            "answer": answer,
            "type": "bridge",
            "level": "hard",
            "context": [["Riverside Stadium", ["Riverside Stadium sentence."]], ["Lincoln Tigers", ["Lincoln Tigers sentence."]]],
        }

    def test_vertex_with_evidence_gets_llm_description_synced(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple(
                "Riverside Stadium", "coached_by", "Barry Switzer", is_literal=True,
                from_type="Team", to_type="Person", evidence="Barry Switzer coached Riverside Stadium's team.",
            )],
        )
        extractor = FakeExtractor({"Q1?": graph})
        planner = FakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=True, llm_planner=planner,
        )

        self.assertEqual(len(planner.calls), 2)  # Riverside Stadium + Barry Switzer both have evidence
        self.assertGreater(materialized["description_embedding_count"], 0)

        person_id = next(row["id"] for row in materialized["vertex_rows_by_type"]["Person"])
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=person_id)
            .first()
        )
        self.assertIsNotNone(row)
        self.assertIn("Barry Switzer coached", row.dedup_text)

    def test_generic_literal_object_gets_no_description_and_is_not_indexed(self):
        """Regression: a triple's evidence sentence is written from the
        SUBJECT's point of view and used to be attached to BOTH endpoints
        (see add_evidence in the triple loop) -- so a literal object with no
        real entity type (to_type left empty, e.g. "2006 independent
        documentary" describing what Finding Kraftland IS, not a separate
        entity) used to get its own LLM-summarized description nearly
        identical to its subject's, making the two indistinguishable (even
        embedding-distance-tied) to live entity-linking's candidate search --
        the exact bug behind the Finding Kraftland miss case. Fixed by
        excluding GENERIC_LITERAL_TYPE ("Value") vertices from both the LLM
        description call and the description-embedding index entirely."""
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Finding Kraftland",
            triples=[Triple(
                "Finding Kraftland", "is_a", "2006 independent documentary", is_literal=True,
                from_type="WorkOfArt", to_type="",
                evidence="Finding Kraftland is a 2006 independent documentary.",
            )],
        )
        extractor = FakeExtractor({"Q1?": graph})
        planner = FakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=True, llm_planner=planner,
        )

        # Only the subject (WorkOfArt) got summarized -- not the Value literal.
        self.assertEqual(len(planner.calls), 1)
        self.assertEqual(planner.calls[0][1], "WorkOfArt")

        value_id = next(row["id"] for row in materialized["vertex_rows_by_type"]["Value"])
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=value_id)
            .first()
        )
        self.assertIsNone(row, "Value-typed literal must not be a live entity-linking candidate")

    def test_literal_with_non_value_type_but_near_duplicate_description_is_not_indexed(self):
        """Regression: the Penobscot Marine Museum miss case. Rule 9 asks
        the extraction LLM to leave to_type empty for a categorical phrase
        (e.g. "Maine's oldest maritime museum" describing what "Penobscot
        Marine Museum" IS), but compliance isn't 100% -- some import runs
        give it a plausible non-empty type instead (observed: "Description"),
        which evades the GENERIC_LITERAL_TYPE filter entirely. This is the
        structural backstop: regardless of what to_type the LLM assigned, a
        literal whose own description is a near-duplicate of its subject's
        (real embedding distance 0.023 for this exact pair, calibrated in
        import_hotpotqa_nebula_tenant.py's LITERAL_NEAR_DUPLICATE_MAX_DISTANCE
        comment) must not be indexed for live entity-linking -- it would
        otherwise tie with or beat the real entity in candidate search."""
        class NearDuplicateFakePlanner:
            def __init__(self):
                self.calls = []

            def summarize_entity_description(self, label, entity_type, evidence):
                self.calls.append((label, entity_type, list(evidence)))
                text = {
                    "Penobscot Marine Museum": (
                        "The Penobscot Marine Museum is Maine's oldest maritime museum, "
                        "located in Searsport, Maine."
                    ),
                    "Maine's oldest maritime museum": (
                        "The Penobscot Marine Museum, located in Searsport, Maine, is the "
                        "state's oldest maritime museum."
                    ),
                }[label]
                return EntityDescriptionResult(description=text)

        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Penobscot Marine Museum",
            triples=[Triple(
                "Penobscot Marine Museum", "is_a", "Maine's oldest maritime museum", is_literal=True,
                from_type="Organization", to_type="Description",
                evidence="The Penobscot Marine Museum is Maine's oldest maritime museum.",
            )],
        )
        extractor = FakeExtractor({"Q1?": graph})
        planner = NearDuplicateFakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=True, llm_planner=planner,
        )

        museum_id = next(row["id"] for row in materialized["vertex_rows_by_type"]["Organization"])
        description_id = next(row["id"] for row in materialized["vertex_rows_by_type"]["Description"])

        museum_row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=museum_id)
            .first()
        )
        near_dup_row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=description_id)
            .first()
        )
        self.assertIsNotNone(museum_row, "the real entity must still be indexed")
        self.assertIsNone(near_dup_row, "the near-duplicate categorical-phrase vertex must not be indexed")

    def test_mentioned_entity_description_feeds_the_description_pass(self):
        """A mentioned_entity's own extraction-time description flows into
        the SAME evidence-accumulation mechanism as triple evidence -- no
        separate code path, just richer input to the existing LLM
        description-summarization call."""
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple("Riverside Stadium", "rel", "Lincoln Tigers", is_literal=False)],
            mentioned_entities=[MentionedEntity(
                name="Some Coach", entity_type="Person",
                description="Some Coach led Riverside Stadium's team to victory.",
            )],
        )
        extractor = FakeExtractor({"Q1?": graph})
        planner = FakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=True, llm_planner=planner,
        )

        coach_id = next(row["id"] for row in materialized["vertex_rows_by_type"]["Person"])
        call = next(c for c in planner.calls if c[0] == "Some Coach")
        self.assertIn("Some Coach led Riverside Stadium's team to victory.", call[2])
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=coach_id)
            .first()
        )
        self.assertIsNotNone(row)

    def test_vertex_without_evidence_gets_no_llm_call_but_still_indexed(self):
        """A vertex minted from topic_title/entity_mentions promotion with
        zero accumulated evidence (never a triple endpoint) shouldn't waste
        an LLM call -- it still gets indexed under the description
        source_space, just falling back to the bare label."""
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="Riverside Stadium", triples=[])  # topic_title never a triple endpoint
        extractor = FakeExtractor({"Q1?": graph})
        planner = FakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=True, llm_planner=planner,
        )

        self.assertEqual(planner.calls, [])
        topic_id = materialized["cases"][0]["center_node"]
        row = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION, source_key=topic_id)
            .first()
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.dedup_text, "Entity Riverside Stadium")

    def test_build_description_embeddings_false_skips_pass_entirely(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(
            topic_title="Riverside Stadium",
            triples=[Triple(
                "Riverside Stadium", "coached_by", "Barry Switzer", is_literal=True,
                from_type="Team", to_type="Person", evidence="Barry Switzer coached Riverside Stadium's team.",
            )],
        )
        extractor = FakeExtractor({"Q1?": graph})
        planner = FakePlanner()

        materialized = materialize_hotpotqa_questions(
            self.session, [q1], extractor, tenant_id=self.tenant_id,
            build_description_embeddings=False, llm_planner=planner,
        )

        self.assertEqual(planner.calls, [])
        self.assertEqual(materialized["description_embedding_count"], 0)
        count = (
            self.session.query(GraphIdentityIndex)
            .filter_by(project_id=self.tenant_id, source_space=SOURCE_SPACE_DESCRIPTION)
            .count()
        )
        self.assertEqual(count, 0)


class GatherFactsTest(unittest.TestCase):
    """gather_facts() merges traverse()+fetch_label() across multiple
    center nodes -- needed for questions naming multiple specific subjects,
    where facts about all of them must be available to the judge, not just
    whichever one became the primary center_node."""

    def test_merges_facts_from_multiple_centers_without_duplicates(self):
        from unittest.mock import patch
        import run_hotpotqa_nebula_e2e_benchmark as mod

        def fake_traverse(client, center, *, depth):
            return {
                "q1:a": [{"id": "q1:shared", "label": "Shared", "rel": "r"}],
                "q1:b": [{"id": "q1:shared", "label": "Shared", "rel": "r"}, {"id": "q1:only_b", "label": "Only B", "rel": "r2"}],
            }[center]

        def fake_fetch_label(client, vid):
            return {"q1:a": "A", "q1:b": "B"}[vid]

        with patch.object(mod, "traverse", side_effect=fake_traverse), \
             patch.object(mod, "fetch_label", side_effect=fake_fetch_label):
            facts = mod.gather_facts(object(), ["q1:a", "q1:b"], depth=2)

        labels = {f["label"] for f in facts}
        self.assertEqual(labels, {"Shared", "Only B", "A", "B"})
        # "Shared" reached from both centers must appear only once.
        self.assertEqual(sum(1 for f in facts if f["label"] == "Shared"), 1)


class GraphHitTest(unittest.TestCase):
    def test_hit_when_a_neighbor_label_matches_gold(self):
        neighbors = [{"id": "q1:yoruba_people", "label": "Yoruba people", "rel": "used_by"}]
        self.assertTrue(graph_hit(neighbors, "The Yoruba"))

    def test_miss_when_no_neighbor_matches(self):
        neighbors = [{"id": "q1:ida_sword", "label": "Ida (sword)", "rel": "used_by"}]
        self.assertFalse(graph_hit(neighbors, "The Yoruba"))

    def test_miss_when_no_neighbors(self):
        self.assertFalse(graph_hit([], "The Yoruba"))

    def test_yes_no_answer_matches_exactly(self):
        neighbors = [{"id": "q1:x", "label": "yes", "rel": "is_true"}]
        self.assertTrue(graph_hit(neighbors, "yes"))

    def test_hit_when_only_the_centers_own_label_matches(self):
        # Regression: run_case() must also pass the center node's own label
        # (via fetch_label) into the checked set -- traverse() only visits
        # vertices *reached from* the center, so a question like "which
        # group ... use the Ida?" whose answer is literally the center's
        # own label (center="Yoruba people", gold="The Yoruba") would
        # otherwise always miss even with a perfectly correct graph.
        checked = [{"id": "q1:yoruba_people", "label": "Yoruba people", "rel": "__self__"}]
        self.assertTrue(graph_hit(checked, "The Yoruba"))


class LiveNebulaSmokeTest(unittest.TestCase):
    """Skipped (not failed) if the local Nebula cluster isn't reachable."""

    def test_insert_and_traverse_round_trip(self):
        try:
            from graph_db_client import NebulaGraphClient
        except ImportError:
            self.skipTest("nebula3-python not installed")

        client = NebulaGraphClient(
            ip="127.0.0.1", port=9669, user="root", password="nebula",
            space="hotpotqa_kg_unittest_typed",
        )
        try:
            client.connect()
        except Exception as exc:
            self.skipTest(f"Nebula not reachable: {exc}")

        try:
            from import_hotpotqa_nebula_tenant import _insert_with_schema_retry
            # traverse()/fetch_label() in run_hotpotqa_nebula_e2e_benchmark
            # still hardcode the pre-typed-model TAG_NAME="HotpotEntity"/
            # EDGE_TYPE="RELATION" fallback constants (see that module's
            # docstring) -- match them here, not this repo's real typed model.
            from run_hotpotqa_nebula_e2e_benchmark import traverse, TAG_NAME, EDGE_TYPE

            client.execute_query(f"CREATE TAG IF NOT EXISTS {TAG_NAME}(label string);")
            client.execute_query(f"CREATE EDGE IF NOT EXISTS {EDGE_TYPE}(relation_label string, evidence string);")
            import time
            time.sleep(11)
            _insert_with_schema_retry(lambda: client.insert_vertices(TAG_NAME, [
                {"id": "t:a", "label": "Test A"},
                {"id": "t:b", "label": "Test B"},
            ]))
            _insert_with_schema_retry(lambda: client.insert_edges(EDGE_TYPE, [
                {"source_id": "t:a", "target_id": "t:b", "relation_label": "rel", "evidence": "test"},
            ]))
            time.sleep(2)

            neighbors = traverse(client, "t:a", depth=1)
            labels = {n["label"] for n in neighbors}
            self.assertIn("Test B", labels)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
