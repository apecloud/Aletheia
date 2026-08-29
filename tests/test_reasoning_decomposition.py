#!/usr/bin/env python3
"""ReasoningEngine.analyze_decomposed -- question-decomposition support
borrowed from StepChain GraphRAG (arXiv:2510.02827). Entity linking (which
sub-questions a question splits into, and resolving each to graph ids)
stays the CALLER's job (see scripts/run_hotpotqa_nebula_via_analyze_
benchmark.py's _resolve_decomposed_centers) -- this module only tests the
per-sub-question gather + partial-answer + final-merge orchestration.

`_gather_center_data`/`_entity_config`/`_link_config`/`_artifact_descriptions`/
`_plan_question_paths` are mocked, same convention as
tests/test_reasoning_multi_center.py's AnalyzeMultiCenterOrchestrationTest.

Run: python -m unittest tests.test_reasoning_decomposition
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from aletheia.reasoning.engine import ReasoningEngine
from aletheia.llms.planner import RelationalDerivation, MergedAnswerResult


class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


def _center_data(center_node: str, label: str, props=None, nodes=None, edges=None) -> dict:
    return {
        "center_node": center_node,
        "object_type": center_node.split(":", 1)[0],
        "instance_id": center_node.split(":", 1)[1],
        "label": label,
        "cfg": {},
        "entity_desc": "",
        "descriptions": {},
        "props": props or [],
        "self_refs": {},
        "neighbors_by_type": {},
        "nodes": nodes or [],
        "edges": edges or [],
    }


class AnalyzeDecomposedTest(unittest.TestCase):
    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())
        # Common patches every test needs -- entity_config just needs a
        # truthy entry for whatever object_type prefix test center_node ids
        # use ("person"), link_config/artifact_descriptions/path-plan are
        # irrelevant to this orchestration logic, so kept minimal/inert.
        self._patches = [
            patch.object(self.engine, "_entity_config", return_value={"person": {"artifact": "object:person"}}),
            patch.object(self.engine, "_link_config", return_value=[]),
            patch.object(self.engine, "_artifact_descriptions", return_value={}),
            patch.object(self.engine, "_plan_question_paths", return_value=None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_no_llm_planner_returns_none(self):
        with patch.object(self.engine, "_get_llm_planner", return_value=None):
            result = self.engine.analyze_decomposed(
                tenant=object(), question="q?",
                sub_question_centers=[{"sub_question": "q1?", "center_node": "person:a"}],
            )
        self.assertIsNone(result)

    def test_two_sub_questions_merge_into_final_answer(self):
        data_a = _center_data("person:beethoven", "Ludwig van Beethoven", props=[{"col": "published_in", "value": "1801"}])
        data_b = _center_data("person:fries", "Count Moritz von Fries", props=[{"col": "dedicated_work", "value": "Violin Sonata No. 4"}])

        derive_calls = []

        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                derive_calls.append(question)
                return RelationalDerivation(answer="Violin Sonata No. 4", supporting_center_nodes=[centers_facts[0]["center_node"]])

            def merge_partial_answers(self, question, sub_answers):
                self.merge_sub_answers = sub_answers
                return MergedAnswerResult(answer="Violin Sonata No. 4", reasoning="Both sub-questions agree.")

        planner = FakePlanner()
        with patch.object(self.engine, "_gather_center_data", side_effect=[data_a, data_b]), \
             patch.object(self.engine, "_get_llm_planner", return_value=planner):
            result = self.engine.analyze_decomposed(
                tenant=object(),
                question="Which piece did Ludwig van Beethoven publish in 1801 that was dedicated to Count Moritz von Fries?",
                sub_question_centers=[
                    {"sub_question": "What did Beethoven publish in 1801?", "center_node": "person:beethoven"},
                    {"sub_question": "What work was dedicated to Fries?", "center_node": "person:fries"},
                ],
            )

        self.assertEqual(len(derive_calls), 2, "one derive_relational_answer call per resolved sub-question")
        self.assertEqual(result["metrics"]["resolution"], "llm_reasoning")
        self.assertEqual(result["metrics"]["answer"], "Violin Sonata No. 4")
        self.assertEqual(set(result["metrics"]["supporting_center_nodes"]), {"person:beethoven", "person:fries"})

    def test_unresolved_sub_question_is_included_not_dropped(self):
        """A sub-question with no center_node (its entity never resolved)
        still gets an entry in the merge call (answer=None), and the
        overall result still succeeds from whatever DID resolve."""
        data_a = _center_data("person:beethoven", "Ludwig van Beethoven", props=[{"col": "published_in", "value": "1801"}])

        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                return RelationalDerivation(answer="Violin Sonata No. 4", supporting_center_nodes=[centers_facts[0]["center_node"]])

            def merge_partial_answers(self, question, sub_answers):
                self.received = sub_answers
                return MergedAnswerResult(answer="Violin Sonata No. 4", reasoning="")

        planner = FakePlanner()
        with patch.object(self.engine, "_gather_center_data", side_effect=[data_a]), \
             patch.object(self.engine, "_get_llm_planner", return_value=planner):
            result = self.engine.analyze_decomposed(
                tenant=object(), question="q?",
                sub_question_centers=[
                    {"sub_question": "What did Beethoven publish in 1801?", "center_node": "person:beethoven"},
                    {"sub_question": "unresolved clue", "center_node": ""},
                ],
            )

        self.assertEqual(len(planner.received), 2)
        unresolved_entry = next(e for e in planner.received if e["sub_question"] == "unresolved clue")
        self.assertIsNone(unresolved_entry["answer"])
        self.assertEqual(result["metrics"]["answer"], "Violin Sonata No. 4")

    def test_all_sub_questions_unresolved_returns_unavailable_profile(self):
        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                raise AssertionError("must not be called -- no sub-question center resolved")

            def merge_partial_answers(self, question, sub_answers):
                raise AssertionError("must not be called -- no sub-question center resolved")

        with patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            result = self.engine.analyze_decomposed(
                tenant=object(), question="q?",
                sub_question_centers=[
                    {"sub_question": "s1", "center_node": ""},
                    {"sub_question": "s2", "center_node": ""},
                ],
            )
        self.assertIn("profile unavailable", result["title"])

    def test_merge_fallback_yields_unresolved_result_not_crash(self):
        data_a = _center_data("person:beethoven", "Ludwig van Beethoven")

        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                return RelationalDerivation(answer="some partial", supporting_center_nodes=[centers_facts[0]["center_node"]])

            def merge_partial_answers(self, question, sub_answers):
                return MergedAnswerResult(used_fallback=True, error="boom")

        with patch.object(self.engine, "_gather_center_data", side_effect=[data_a]), \
             patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            result = self.engine.analyze_decomposed(
                tenant=object(), question="q?",
                sub_question_centers=[{"sub_question": "s1", "center_node": "person:beethoven"}],
            )

        self.assertIsNone(result["metrics"]["answer"])
        self.assertIn("unresolved", result["title"])


if __name__ == "__main__":
    unittest.main()
