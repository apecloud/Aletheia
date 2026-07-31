#!/usr/bin/env python3
"""Multi-center resolution support in ReasoningEngine.

General mechanism for questions naming multiple specific entities --
NOT tied to any particular question "type" (comparison, relationship-check,
joint analysis, ...). Covers the three pieces added on top of the existing
single-center `analyze()`: path-finding between two named centers
(`_find_path_between_centers`), the LLM-reasoning fallback when no path
exists (`_llm_derive_relational_answer`), and the orchestration
(`_analyze_multi_center`) that picks between them.

`_gather_center_data` (the per-center data-gathering pipeline) is mocked
rather than re-tested here -- it's a pure extraction of the existing
single-center `analyze()` body, already exercised by
`tests/test_webqsp_benchmark.py`/`tests/test_maritime_risk_benchmark.py`, and
re-testing it against a full fake SQL-backed repo would duplicate that
coverage without adding signal about the NEW multi-center logic.

Run: python -m unittest tests.test_reasoning_multi_center
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from reasoning_engine import ReasoningEngine
from llm_planner import RelationalDerivation, PlannerMapping


class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


def _center_data(center_node: str, label: str, props=None, rankings=None, nodes=None, edges=None) -> dict:
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
        "rankings": rankings or [],
        "link_stats": [],
        "value_aggs": [],
        "source_key_profile": None,
        "neighbors_by_type": {},
        "nodes": nodes or [],
        "edges": edges or [],
        "chain_results": [],
        "selected_answer_surfaces": [],
    }


class FindPathBetweenCentersTest(unittest.TestCase):
    def test_direct_edge_is_found(self):
        nodes = [{"id": "bank:a"}, {"id": "bank:b"}]
        edges = [{"source": "bank:a", "target": "bank:b", "label": "acquired"}]
        path = ReasoningEngine._find_path_between_centers("bank:a", "bank:b", nodes, edges)
        self.assertEqual(path, [{"source": "bank:a", "target": "bank:b", "label": "acquired"}])

    def test_reverse_edge_is_found(self):
        # neighborhood() edges aren't necessarily oriented center->target;
        # the BFS must also traverse edges in the reverse direction.
        edges = [{"source": "bank:b", "target": "bank:a", "label": "acquired_by"}]
        path = ReasoningEngine._find_path_between_centers("bank:a", "bank:b", [], edges)
        self.assertIsNotNone(path)
        self.assertEqual(path[0]["target"], "bank:b")

    def test_multi_hop_path_is_found(self):
        edges = [
            {"source": "bank:a", "target": "bank:mid", "label": "r1"},
            {"source": "bank:mid", "target": "bank:b", "label": "r2"},
        ]
        path = ReasoningEngine._find_path_between_centers("bank:a", "bank:b", [], edges)
        self.assertEqual(len(path), 2)
        self.assertEqual(path[-1]["target"], "bank:b")

    def test_unreachable_returns_none(self):
        edges = [{"source": "bank:a", "target": "bank:unrelated", "label": "r1"}]
        path = ReasoningEngine._find_path_between_centers("bank:a", "bank:b", [], edges)
        self.assertIsNone(path)

    def test_same_center_returns_empty_path(self):
        path = ReasoningEngine._find_path_between_centers("bank:a", "bank:a", [], [])
        self.assertEqual(path, [])


class LLMDeriveRelationalAnswerTest(unittest.TestCase):
    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def test_no_llm_planner_degrades_gracefully(self):
        with patch.object(self.engine, "_get_llm_planner", return_value=None):
            result = self.engine._llm_derive_relational_answer(
                "Which was founded first, A or B?",
                [_center_data("bank:a", "Bank A"), _center_data("bank:b", "Bank B")],
            )
        self.assertEqual(result["resolution"], "unavailable")
        self.assertIsNone(result["answer"])

    def test_centers_own_label_is_excluded_from_llm_facts(self):
        """Regression: the exact leakage bug found in the HotpotQA Nebula
        benchmark (judge matched an entity's own name instead of reasoning
        over facts) -- the label/identity must never reach the LLM call."""
        captured = {}

        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                captured["centers_facts"] = centers_facts
                return RelationalDerivation(
                    answer="1590", supporting_center_nodes=["bank:a"], reasoning="Bank A's date is earlier."
                )

        with patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            result = self.engine._llm_derive_relational_answer(
                "Which was founded first, Bank A or Bank B?",
                [
                    _center_data("bank:a", "Bank A", props=[{"col": "founded_in", "value": "1590"}]),
                    _center_data("bank:b", "Bank B", props=[{"col": "founded_in", "value": "1920"}]),
                ],
            )

        serialized = str(captured["centers_facts"])
        self.assertNotIn("Bank A", serialized)
        self.assertNotIn("Bank B", serialized)
        self.assertIn("1590", serialized)
        self.assertIn("1920", serialized)
        self.assertEqual(result["answer"], "1590")
        self.assertEqual(result["supporting_center_nodes"], ["bank:a"])
        self.assertEqual(result["supporting_labels"], ["Bank A"])

    def test_answer_can_be_supported_by_all_centers_not_just_one(self):
        """The schema must not presuppose a single "winner" -- a commonality
        question ("what pursuit did both have in common?") answers with a
        shared trait supported by every center, not a pick-one-of-N choice."""
        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                return RelationalDerivation(
                    answer="writer",
                    supporting_center_nodes=["bank:a", "bank:b"],
                    reasoning="Both centers' facts list a writing-related occupation.",
                )

        with patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            result = self.engine._llm_derive_relational_answer(
                "What pursuit did both have in common?",
                [
                    _center_data("bank:a", "Person A", props=[{"col": "occupation", "value": "author"}]),
                    _center_data("bank:b", "Person B", props=[{"col": "occupation", "value": "writer"}]),
                ],
            )

        self.assertEqual(result["answer"], "writer")
        self.assertEqual(set(result["supporting_center_nodes"]), {"bank:a", "bank:b"})
        self.assertEqual(set(result["supporting_labels"]), {"Person A", "Person B"})

    def test_llm_fallback_used_when_call_fails(self):
        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                return RelationalDerivation(used_fallback=True, error="litellm.Timeout", error_type="timeout")

        with patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            result = self.engine._llm_derive_relational_answer(
                "Which was founded first, A or B?",
                [_center_data("bank:a", "Bank A"), _center_data("bank:b", "Bank B")],
            )
        self.assertIsNone(result["answer"])
        self.assertEqual(result["error"], "litellm.Timeout")


class AnalyzeMultiCenterOrchestrationTest(unittest.TestCase):
    """_analyze_multi_center: path-found short-circuits before any LLM call;
    no-path falls back to LLM reasoning. _gather_center_data is mocked so
    these tests exercise only the NEW orchestration logic."""

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def test_path_found_skips_llm_entirely(self):
        data_a = _center_data("bank:a", "Bank A", edges=[{"source": "bank:a", "target": "bank:b", "label": "acquired"}])
        data_b = _center_data("bank:b", "Bank B")

        llm_calls = []

        class FakePlanner:
            def derive_relational_answer(self, *args, **kwargs):
                llm_calls.append(1)
                return RelationalDerivation(answer="bank:a", supporting_center_nodes=["bank:a"])

        with patch.object(self.engine, "_gather_center_data", side_effect=[data_a, data_b]), \
             patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            plan = self.engine.QuestionPathPlan(question="q")
            result = self.engine._analyze_multi_center(
                tenant=object(), center_node="bank:a", additional_center_nodes=["bank:b"],
                question="Which acquired which?", entity_config={}, link_config=[],
                path_plan=plan, depth=2, limit=200,
            )

        self.assertEqual(result["metrics"]["resolution"], "path_found")
        self.assertEqual(len(llm_calls), 0, "path_found must short-circuit before any LLM call")

    def test_no_path_falls_back_to_llm_reasoning(self):
        data_a = _center_data("bank:a", "Bank A", props=[{"col": "founded_in", "value": "1590"}])
        data_b = _center_data("bank:b", "Bank B", props=[{"col": "founded_in", "value": "1920"}])

        class FakePlanner:
            def derive_relational_answer(self, question, centers_facts):
                return RelationalDerivation(
                    answer="1590", supporting_center_nodes=["bank:a"], reasoning="1590 predates 1920."
                )

        with patch.object(self.engine, "_gather_center_data", side_effect=[data_a, data_b]), \
             patch.object(self.engine, "_get_llm_planner", return_value=FakePlanner()):
            plan = self.engine.QuestionPathPlan(question="q")
            result = self.engine._analyze_multi_center(
                tenant=object(), center_node="bank:a", additional_center_nodes=["bank:b"],
                question="Which was founded first, Bank A or Bank B?", entity_config={}, link_config=[],
                path_plan=plan, depth=2, limit=200,
            )

        self.assertEqual(result["metrics"]["resolution"], "llm_reasoning")
        self.assertEqual(result["metrics"]["answer"], "1590")
        self.assertEqual(result["metrics"]["supporting_center_nodes"], ["bank:a"])
        self.assertEqual(result["metrics"]["supporting_labels"], ["Bank A"])

    def test_missing_center_returns_unavailable_profile(self):
        with patch.object(self.engine, "_gather_center_data", side_effect=[None]):
            plan = self.engine.QuestionPathPlan(question="q")
            result = self.engine._analyze_multi_center(
                tenant=object(), center_node="bank:missing", additional_center_nodes=["bank:b"],
                question="q?", entity_config={}, link_config=[],
                path_plan=plan, depth=2, limit=200,
            )
        self.assertIn("profile unavailable", result["title"])


class PlannerCapabilitiesAndEntityMentionsFromLLMTest(unittest.TestCase):
    """Retrieval capabilities (rankings/link_stats/value_aggregation/
    source_key_profile) are selected by the LLM planner against the open
    RETRIEVAL_CAPABILITIES registry, not keyword matching or a fixed enum
    of named booleans -- verifies the plumbing from PlannerMapping into
    QuestionPathPlan. Likewise entity_mentions is a general field, not tied
    to any specific question "type" like "comparison"."""

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def _entity_config(self):
        return {"customer": {"table": "customers", "pk": "customer_id", "artifact": "object:customer"}}

    def test_llm_selected_capability_enables_value_aggs(self):
        with patch.object(
            self.engine, "_llm_map_question_to_relations",
            return_value=PlannerMapping(selected_capabilities={"value_aggregation"}),
        ):
            plan = self.engine._plan_question_paths(
                "Some vague phrasing with no keyword overlap at all",
                "customer", self._entity_config(), [], {},
            )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(plan.include_value_aggs)

    def test_llm_unavailable_stays_full_aggregation(self):
        """No keyword fallback exists anymore -- when the LLM can't judge
        capabilities, the plan stays at its safe is_full_aggregation=True
        default rather than guessing."""
        with patch.object(
            self.engine, "_llm_map_question_to_relations",
            return_value=PlannerMapping(used_fallback=True),
        ):
            plan = self.engine._plan_question_paths(
                "Some vague phrasing with no keyword overlap at all",
                "customer", self._entity_config(), [], {},
            )
        self.assertTrue(plan.is_full_aggregation)

    def test_llm_entity_mentions_get_resolved_against_candidate_labels(self):
        with patch.object(
            self.engine, "_llm_map_question_to_relations",
            return_value=PlannerMapping(entity_mentions=["Bank A", "Bank B"]),
        ):
            plan = self.engine._plan_question_paths(
                "Which was founded first, Bank A or Bank B?",
                "customer", self._entity_config(), [], {},
                candidate_labels=["Bank A", "Bank B", "Unrelated Co"],
            )
        self.assertEqual(set(plan.entity_mentions), {"Bank A", "Bank B"})
        self.assertEqual(set(plan.resolved_entity_centers), {"Bank A", "Bank B"})

    def test_no_entity_mentions_for_single_entity_question(self):
        with patch.object(
            self.engine, "_llm_map_question_to_relations",
            return_value=PlannerMapping(),
        ):
            plan = self.engine._plan_question_paths(
                "What is this customer's revenue?",
                "customer", self._entity_config(), [], {},
                candidate_labels=["Bank A", "Bank B"],
            )
        self.assertEqual(plan.entity_mentions, [])
        self.assertEqual(plan.resolved_entity_centers, [])


if __name__ == "__main__":
    unittest.main()
