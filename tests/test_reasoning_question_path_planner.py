import unittest
from unittest.mock import patch

from aletheia.reasoning.engine import ReasoningEngine
from aletheia.llms.planner import PlannerMapping


class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


class QuestionPathPlannerTest(unittest.TestCase):
    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def _entity_config(self):
        return {
            "customer": {"table": "customers", "pk": "customer_id", "artifact": "object:customer"},
            "order": {"table": "orders", "pk": "order_id", "artifact": "object:order"},
            "product": {"table": "products", "pk": "product_id", "artifact": "object:product"},
        }

    def _link_config(self):
        return [
            {"link": "link:customer:places:order", "from": "customer", "to": "order", "fk_table": "orders", "fk_col": "customer_id"},
            {"link": "link:order:contains:product", "from": "order", "to": "product", "fk_table": "order_details", "fk_col": "order_id"},
        ]

    def _descriptions(self):
        return {
            "link:customer:places:order": "Customer places orders for products.",
            "link:order:contains:product": "Order contains product line items.",
        }

    def test_no_question_falls_back_to_full_aggregation(self):
        plan = self.engine._plan_question_paths(
            None, "customer", self._entity_config(), self._link_config(), self._descriptions()
        )
        self.assertTrue(plan.is_full_aggregation)
        self.assertEqual(plan.selected_link_keys, set())

    def test_empty_question_falls_back_to_full_aggregation(self):
        plan = self.engine._plan_question_paths(
            "  ", "customer", self._entity_config(), self._link_config(), self._descriptions()
        )
        self.assertTrue(plan.is_full_aggregation)

    def test_question_targeting_order_selects_order_paths(self):
        plan = self.engine._plan_question_paths(
            "How many orders does this customer have?",
            "customer",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("link:customer:places:order", plan.selected_link_keys)
        self.assertIn("order", plan.selected_target_types)

    def test_question_targeting_value_enables_value_aggs(self):
        # Capability selection is judged by the LLM planner (llm_planner.py)
        # against the RETRIEVAL_CAPABILITIES registry, not keyword matching
        # -- mock it here since this test has no live LLM.
        with patch.object(
            self.engine, "_llm_map_question_to_relations",
            return_value=PlannerMapping(selected_capabilities={"value_aggregation"}),
        ):
            plan = self.engine._plan_question_paths(
                "What is the total revenue for this customer?",
                "customer",
                self._entity_config(),
                self._link_config(),
                self._descriptions(),
            )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(plan.include_value_aggs)

    def test_different_questions_select_different_paths(self):
        order_plan = self.engine._plan_question_paths(
            "How many orders does this customer have?",
            "customer",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        # Simulate a question about products from the order entity
        product_plan = self.engine._plan_question_paths(
            "What products are in this order?",
            "order",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertIn("link:customer:places:order", order_plan.selected_link_keys)
        self.assertIn("link:order:contains:product", product_plan.selected_link_keys)
        self.assertNotIn("link:order:contains:product", order_plan.selected_link_keys)

    def test_risk_question_enables_source_key_profile(self):
        plan = self.engine._plan_question_paths(
            "What is the risk exposure for this customer?",
            "customer",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(plan.include_source_key_profile)

    def test_vague_question_with_no_matching_types_falls_back(self):
        plan = self.engine._plan_question_paths(
            "Tell me about this entity",
            "customer",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertTrue(plan.is_full_aggregation)


if __name__ == "__main__":
    unittest.main()

class TwoHopChainPlannerTest(unittest.TestCase):
    """Tests for Phase 2: entity-type pair extraction and 2-hop chain enumeration."""

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def _multi_entity_config(self):
        return {
            "chokepoint": {"table": "chokepoints", "pk": "chokepoint_id", "artifact": "object:chokepoint"},
            "trade_route": {"table": "trade_routes", "pk": "route_id", "artifact": "object:trade_route"},
            "country": {"table": "countries", "pk": "country_id", "artifact": "object:country"},
        }

    def _multi_link_config(self):
        return [
            {"link": "link:chokepoint:affects:trade_route", "from": "chokepoint", "to": "trade_route", "fk_table": "trade_routes", "fk_col": "chokepoint_id"},
            {"link": "link:trade_route:impacts:country", "from": "trade_route", "to": "country", "fk_table": "country_routes", "fk_col": "route_id"},
        ]

    def _multi_descriptions(self):
        return {
            "link:chokepoint:affects:trade_route": "Chokepoint affects trade routes.",
            "link:trade_route:impacts:country": "Trade route impacts countries.",
        }

    def test_two_entity_question_builds_2hop_chain(self):
        plan = self.engine._plan_question_paths(
            "Which countries are exposed to disruptions at this chokepoint?",
            "chokepoint",
            self._multi_entity_config(),
            self._multi_link_config(),
            self._multi_descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(len(plan.admissible_chains) >= 1)
        chain = plan.admissible_chains[0]
        self.assertEqual(chain[0], "link:chokepoint:affects:trade_route")
        self.assertEqual(chain[1], "link:trade_route:impacts:country")

    def test_single_entity_question_stays_1hop(self):
        plan = self.engine._plan_question_paths(
            "How many trade routes does this chokepoint affect?",
            "chokepoint",
            self._multi_entity_config(),
            self._multi_link_config(),
            self._multi_descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(len(plan.admissible_chains), 0)
        self.assertIn("link:chokepoint:affects:trade_route", plan.selected_link_keys)

    def test_structurally_limited_schema_graceful_fallback(self):
        """creditcardfraud-like schema: all links point to Transaction leaf, no outgoing."""
        limited_entity_config = {
            "cardholder": {"table": "cardholders", "pk": "cardholder_id", "artifact": "object:cardholder"},
            "transaction": {"table": "transactions", "pk": "transaction_id", "artifact": "object:transaction"},
        }
        limited_link_config = [
            {"link": "link:cardholder:makes:transaction", "from": "cardholder", "to": "transaction", "fk_table": "transactions", "fk_col": "cardholder_id"},
        ]
        descriptions = {
            "link:cardholder:makes:transaction": "Cardholder makes transactions.",
        }
        plan = self.engine._plan_question_paths(
            "How many transactions does this cardholder have?",
            "cardholder",
            limited_entity_config,
            limited_link_config,
            descriptions,
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(len(plan.admissible_chains), 0)
        self.assertIn("link:cardholder:makes:transaction", plan.selected_link_keys)

    def test_two_entity_question_no_chain_falls_back_to_1hop(self):
        """Two entity types mentioned but no connecting chain exists."""
        plan = self.engine._plan_question_paths(
            "How does this cardholder relate to this transaction?",
            "cardholder",
            {
                "cardholder": {"table": "cardholders", "pk": "cardholder_id"},
                "transaction": {"table": "transactions", "pk": "transaction_id"},
            },
            [
                {"link": "link:cardholder:makes:transaction", "from": "cardholder", "to": "transaction", "fk_table": "transactions", "fk_col": "cardholder_id"},
            ],
            {},
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(len(plan.admissible_chains), 0)
        self.assertIn("link:cardholder:makes:transaction", plan.selected_link_keys)


class MaritimeRiskBidirectionalTest(unittest.TestCase):
    """Tests for Phase 5: bidirectional chain enumeration using @Altman's maritime-risk fixtures.

    Schema link_config:
        chokepoint:1:n:risk_indicator       from=chokepoint to=risk_indicator
        country:n:m:chokepoint_dependency   from=country to=chokepoint
        country:1:n:systemic_risk_result    from=country to=systemic_risk_result
        trade_dependency:n:1:country        from=trade_dependency to=country
        trade_dependency:n:1:chokepoint      from=trade_dependency to=chokepoint
        risk_finding:1:n:risk_indicator     from=risk_finding to=risk_indicator
        mitigation_action:1:n:risk_finding  from=mitigation_action to=risk_finding
    """

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())

    def _entity_config(self):
        return {
            "chokepoint": {"table": "chokepoints", "pk": "chokepoint_id", "artifact": "object:chokepoint"},
            "risk_indicator": {"table": "risk_indicators", "pk": "risk_indicator_id", "artifact": "object:risk_indicator"},
            "country": {"table": "countries", "pk": "country_id", "artifact": "object:country"},
            "systemic_risk_result": {"table": "systemic_risk_results", "pk": "risk_result_id", "artifact": "object:systemic_risk_result"},
            "trade_dependency": {"table": "trade_dependencies", "pk": "dependency_id", "artifact": "object:trade_dependency"},
            "risk_finding": {"table": "risk_findings", "pk": "finding_id", "artifact": "object:risk_finding"},
            "mitigation_action": {"table": "mitigation_actions", "pk": "action_id", "artifact": "object:mitigation_action"},
        }

    def _link_config(self):
        return [
            {"link": "chokepoint:1:n:risk_indicator", "from": "chokepoint", "to": "risk_indicator", "fk_table": "risk_indicators", "fk_col": "chokepoint_id"},
            {"link": "country:n:m:chokepoint_dependency", "from": "country", "to": "chokepoint", "fk_table": "chokepoint_country_deps", "fk_col": "country_id", "target_fk": "chokepoint_id"},
            {"link": "country:1:n:systemic_risk_result", "from": "country", "to": "systemic_risk_result", "fk_table": "systemic_risk_results", "fk_col": "country_id"},
            {"link": "trade_dependency:n:1:country", "from": "trade_dependency", "to": "country", "fk_table": "trade_dependencies", "fk_col": "dependency_id"},
            {"link": "trade_dependency:n:1:chokepoint", "from": "trade_dependency", "to": "chokepoint", "fk_table": "trade_dependencies", "fk_col": "dependency_id"},
            {"link": "risk_finding:1:n:risk_indicator", "from": "risk_finding", "to": "risk_indicator", "fk_table": "risk_indicators", "fk_col": "finding_id"},
            {"link": "mitigation_action:1:n:risk_finding", "from": "mitigation_action", "to": "risk_finding", "fk_table": "risk_findings", "fk_col": "action_id"},
        ]

    def _descriptions(self):
        return {
            "chokepoint:1:n:risk_indicator": "Chokepoint has risk indicators.",
            "country:n:m:chokepoint_dependency": "Country depends on chokepoint.",
            "country:1:n:systemic_risk_result": "Country has systemic risk results.",
            "trade_dependency:n:1:country": "Trade dependency links to country.",
            "trade_dependency:n:1:chokepoint": "Trade dependency links to chokepoint.",
            "risk_finding:1:n:risk_indicator": "Risk finding relates to risk indicator.",
            "mitigation_action:1:n:risk_finding": "Mitigation action addresses risk finding.",
        }

    def test_q1_1hop_forward_works(self):
        """Q1: 1-hop forward — chokepoint asks about risk_indicator."""
        plan = self.engine._plan_question_paths(
            "What risk indicators does this chokepoint have?",
            "chokepoint",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("chokepoint:1:n:risk_indicator", plan.selected_link_keys)
        self.assertEqual(len(plan.admissible_chains), 0)

    def test_q2_1hop_reverse_selects_reverse_link(self):
        """Q2: 1-hop reverse — chokepoint asks about countries that depend on it.

        The correct link is country:n:m:chokepoint_dependency (reverse: center is
        on the 'to' side). Must NOT fall back to forward chokepoint:1:n:risk_indicator.
        """
        plan = self.engine._plan_question_paths(
            "Which countries depend on this chokepoint?",
            "chokepoint",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("country:n:m:chokepoint_dependency", plan.selected_link_keys)
        # Must not pick the wrong forward link
        self.assertNotIn("chokepoint:1:n:risk_indicator", plan.selected_link_keys)

    def test_q3_2hop_forward_chain_works(self):
        """Q3: 2-hop forward — country asks about risk_indicator through chokepoint."""
        plan = self.engine._plan_question_paths(
            "What is the risk indicator for the chokepoint this country depends on?",
            "country",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(len(plan.admissible_chains) >= 1)
        chain = plan.admissible_chains[0]
        self.assertEqual(chain[0], "country:n:m:chokepoint_dependency")
        self.assertEqual(chain[1], "chokepoint:1:n:risk_indicator")

    def test_q4_2hop_bidirectional_discovers_chain(self):
        """Q4: 2-hop bidirectional — chokepoint asks about countries exposed via trade_dependency.

        Real path: chokepoint <- trade_dependency -> country (reverse+forward).
        The planner should discover the chain through trade_dependency.
        """
        plan = self.engine._plan_question_paths(
            "Which countries are exposed to disruptions at this chokepoint?",
            "chokepoint",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        # Should discover at least one path to country
        self.assertTrue(
            "country:n:m:chokepoint_dependency" in plan.selected_link_keys
            or "trade_dependency:n:1:chokepoint" in plan.selected_link_keys
            or any("trade_dependency" in link for link in plan.selected_link_keys),
            f"Expected reverse path to country not found. selected_link_keys={plan.selected_link_keys}"
        )
        # Should not just pick the forward risk_indicator link
        self.assertNotIn("chokepoint:1:n:risk_indicator", plan.selected_link_keys)

    def test_q5_3hop_stretch_known_limitation(self):
        """Q5: 3-hop — chokepoint asks about mitigation_action.

        Real path: chokepoint -> risk_indicator <- risk_finding <- mitigation_action.
        This requires N-hop bidirectional BFS which is deferred. Acceptable to
        not discover the full chain, but should at least not pick wrong forward link.
        """
        plan = self.engine._plan_question_paths(
            "What mitigation action is recommended for the risk finding of this chokepoint?",
            "chokepoint",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        # Should not incorrectly select only the forward risk_indicator link
        # when mitigation_action and risk_finding are the actual targets
        # (It's OK if it doesn't find the full 3-hop chain, but it should
        # at least recognize the question is about risk_finding/mitigation_action)
        self.assertIn("risk_finding", plan.selected_target_types)
        self.assertIn("mitigation_action", plan.selected_target_types)

    def test_underscore_to_space_type_matching(self):
        """Underscore-to-space normalization: 'risk indicator' (space) should match 'risk_indicator'."""
        plan = self.engine._plan_question_paths(
            "What risk indicators does this chokepoint have?",
            "chokepoint",
            self._entity_config(),
            self._link_config(),
            self._descriptions(),
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("risk_indicator", plan.selected_target_types)
        self.assertIn("chokepoint:1:n:risk_indicator", plan.selected_link_keys)


class ChainRetrievalRetiredTest(unittest.TestCase):
    """_chain_retrieval and _link_deep_stats were SQL-join-only 2-hop
    reconstruction (raw joins over fk_table/fk_col) with no graph-native
    replacement -- retired along with the rest of the SQL retrieval core.
    Real multi-hop traversal now happens directly against graph edges via
    ``self.repo.neighborhood``/``_find_path_between_centers``, so no
    separate "chain" feature is needed."""

    def test_chain_retrieval_method_no_longer_exists(self):
        self.assertFalse(hasattr(ReasoningEngine, "_chain_retrieval"))

    def test_link_deep_stats_method_no_longer_exists(self):
        self.assertFalse(hasattr(ReasoningEngine, "_link_deep_stats"))

    def test_gather_center_data_no_longer_produces_chain_results(self):
        """chain_results/selected_answer_surfaces were always-empty stubs
        threaded through _gather_center_data -> _compose purely for a
        retired SQL feature -- removed entirely rather than kept as
        permanently-empty dead weight."""
        import inspect
        gather_source = inspect.getsource(ReasoningEngine._gather_center_data)
        self.assertNotIn("chain_results", gather_source)
        self.assertNotIn("selected_answer_surfaces", gather_source)


class PathPlanNarrativeTest(unittest.TestCase):
    """_build_narrative/_compose accept path_plan (still meaningful --
    drives the question-focused key_fact/sentence) but no longer accept
    rankings/link_stats/value_aggs/source_key_profile/chain_results/
    selected_answer_surfaces -- all permanently-empty since Phase 0's SQL
    retrieval retirement, so the dead parameters were removed rather than
    kept as no-ops."""

    def test_build_narrative_accepts_path_plan_but_not_retired_params(self):
        import inspect
        sig = inspect.signature(ReasoningEngine._build_narrative)
        self.assertIn("path_plan", sig.parameters)
        for retired in ("rankings", "link_stats", "value_aggs", "source_key_profile", "chain_results"):
            self.assertNotIn(retired, sig.parameters)

    def test_compose_accepts_path_plan_but_not_retired_params(self):
        import inspect
        sig = inspect.signature(ReasoningEngine._compose)
        self.assertIn("path_plan", sig.parameters)
        for retired in (
            "rankings", "link_stats", "value_aggs", "source_key_profile",
            "chain_results", "selected_answer_surfaces",
        ):
            self.assertNotIn(retired, sig.parameters)
