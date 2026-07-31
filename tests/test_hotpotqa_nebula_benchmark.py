#!/usr/bin/env python3
"""HotpotQA Nebula-native benchmark: materialization + graph-hit scoring tests.

Deterministic tests (no live Nebula) exercise ``materialize_hotpotqa_questions``
and ``graph_hit``/``traverse``'s row-parsing helper given fixed fake input.
One additional test connects to a real local Nebula cluster and is skipped
(not failed) if it isn't reachable -- this repo's Nebula containers are
optional local infra, not a CI dependency.

Run: python -m unittest tests.test_hotpotqa_nebula_benchmark
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agents"))
sys.path.insert(0, str(ROOT / "scripts"))

from hotpotqa_kg_extraction import LocalGraph, Triple  # noqa: E402
from hotpotqa_entity_ids import entity_id  # noqa: E402
from import_hotpotqa_nebula_tenant import materialize_hotpotqa_questions  # noqa: E402
from run_hotpotqa_nebula_e2e_benchmark import gather_facts, graph_hit  # noqa: E402


class FakeExtractor:
    def __init__(self, graphs_by_question: dict[str, LocalGraph]):
        self.graphs_by_question = graphs_by_question

    def extract_local_graph(self, question, context):
        return self.graphs_by_question[question]


class MaterializationTest(unittest.TestCase):
    def _case(self, qid: str, question: str, answer: str) -> dict:
        return {
            "qid": qid,
            "question": question,
            "answer": answer,
            "type": "bridge",
            "level": "hard",
            "context": [["A", ["A sentence."]], ["B", ["B sentence."]]],
        }

    def test_vertex_ids_are_qid_scoped(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

        ids = {row["id"] for row in materialized["vertex_rows"]}
        self.assertEqual(ids, {entity_id("q1", "A"), entity_id("q1", "B")})

    def test_all_vertex_rows_share_identical_keys(self):
        # NebulaGraphClient.insert_vertices builds ONE fixed column list from
        # rows[0].keys() -- every row must have exactly the same keys or the
        # generated INSERT VERTEX statement silently misaligns values.
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

        key_sets = {frozenset(row.keys()) for row in materialized["vertex_rows"]}
        self.assertEqual(len(key_sets), 1)
        self.assertEqual(set(next(iter(key_sets))), {"id", "label"})

    def test_all_edge_rows_share_identical_keys(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

        key_sets = {frozenset(row.keys()) for row in materialized["edge_rows"]}
        self.assertEqual(len(key_sets), 1)
        self.assertEqual(set(next(iter(key_sets))), {"source_id", "target_id", "relation_label", "evidence"})

    def test_two_questions_with_same_relation_label_produce_disjoint_edges(self):
        q1 = self._case("q1", "Q1?", "answer1")
        q2 = self._case("q2", "Q2?", "answer2")
        graph1 = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        graph2 = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph1, "Q2?": graph2})

        materialized = materialize_hotpotqa_questions([q1, q2], extractor)

        pairs = {(r["source_id"], r["target_id"]) for r in materialized["edge_rows"]}
        self.assertEqual(
            pairs,
            {
                (entity_id("q1", "A"), entity_id("q1", "B")),
                (entity_id("q2", "A"), entity_id("q2", "B")),
            },
        )

    def test_center_node_uses_topic_title(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="B", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

        self.assertEqual(materialized["cases"][0]["center_node"], entity_id("q1", "B"))

    def test_extraction_error_yields_no_center_node_but_no_crash(self):
        q1 = self._case("q1", "Q1?", "answer1")
        failed_graph = LocalGraph(error="parse failed", used_fallback=True, error_type="runtime_invalid")
        extractor = FakeExtractor({"Q1?": failed_graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

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

        materialized = materialize_hotpotqa_questions([q1], extractor)

        case = materialized["cases"][0]
        self.assertEqual(
            set(case["additional_center_nodes"]),
            {entity_id("q1", "Dain Rauscher Wessels")},
        )

    def test_case_without_entity_mentions_gets_no_additional_center_nodes(self):
        q1 = self._case("q1", "Q1?", "answer1")
        graph = LocalGraph(topic_title="A", triples=[Triple("A", "rel", "B", is_literal=False)])
        extractor = FakeExtractor({"Q1?": graph})

        materialized = materialize_hotpotqa_questions([q1], extractor)

        self.assertEqual(materialized["cases"][0]["additional_center_nodes"], [])


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
            space="hotpotqa_kg_unittest",
        )
        try:
            client.connect()
        except Exception as exc:
            self.skipTest(f"Nebula not reachable: {exc}")

        try:
            from import_hotpotqa_nebula_tenant import ensure_schema, _insert_with_schema_retry, TAG_NAME, EDGE_TYPE
            from run_hotpotqa_nebula_e2e_benchmark import traverse

            ensure_schema(client)
            _insert_with_schema_retry(lambda: client.insert_vertices(TAG_NAME, [
                {"id": "t:a", "label": "Test A"},
                {"id": "t:b", "label": "Test B"},
            ]))
            _insert_with_schema_retry(lambda: client.insert_edges(EDGE_TYPE, [
                {"source_id": "t:a", "target_id": "t:b", "relation_label": "rel", "evidence": "test"},
            ]))
            import time
            time.sleep(2)

            neighbors = traverse(client, "t:a", depth=1)
            labels = {n["label"] for n in neighbors}
            self.assertIn("Test B", labels)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
