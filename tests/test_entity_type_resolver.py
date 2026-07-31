import unittest

from reasoning_engine import ReasoningEngine


class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


class EntityTypeResolverTest(unittest.TestCase):
    """Tests for _resolve_entity_types_from_question and synonym-driven chain discovery."""

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())
        self.entity_config = {
            "chokepoint": {"table": "t", "pk": "id", "artifact": "object:chokepoint"},
            "country": {"table": "t", "pk": "id", "artifact": "object:country"},
            "trade_dependency": {"table": "t", "pk": "id", "artifact": "object:trade_dependency"},
            "risk_indicator": {"table": "t", "pk": "id", "artifact": "object:risk_indicator"},
            "risk_finding": {"table": "t", "pk": "id", "artifact": "object:risk_finding"},
            "mitigation_action": {"table": "t", "pk": "id", "artifact": "object:mitigation_action"},
            "systemic_risk_result": {"table": "t", "pk": "id", "artifact": "object:systemic_risk_result"},
        }
        self.link_config = [
            {"link": "mitigation_action:n:1:risk_finding", "from": "mitigation_action", "to": "risk_finding", "fk_table": "t", "fk_col": "id"},
            {"link": "risk_finding:n:m:evidence", "from": "risk_finding", "to": "risk_indicator", "fk_table": "t", "fk_col": "id"},
            {"link": "chokepoint:1:n:risk_indicator", "from": "chokepoint", "to": "risk_indicator", "fk_table": "t", "fk_col": "id"},
            {"link": "country:n:m:chokepoint_dependency", "from": "country", "to": "chokepoint", "fk_table": "t", "fk_col": "id"},
        ]
        self.descriptions = {
            "mitigation_action:n:1:risk_finding": "Mitigation action for risk finding.",
            "risk_finding:n:m:evidence": "Risk finding supported by evidence.",
            "chokepoint:1:n:risk_indicator": "Chokepoint risk indicators.",
            "country:n:m:chokepoint_dependency": "Country depends on chokepoint.",
        }

    def _known_types(self):
        known = {k.lower() for k in self.entity_config}
        for lc in self.link_config:
            known.add(lc["to"].lower())
            known.add(lc["from"].lower())
        return known

    def test_synonym_evidence_resolves_to_risk_indicator(self):
        result = self.engine._resolve_entity_types_from_question(
            "What evidence supports this finding?",
            self._known_types(),
        )
        self.assertIn("risk_indicator", result)

    def test_synonym_barrier_resolves_to_chokepoint(self):
        result = self.engine._resolve_entity_types_from_question(
            "Which barrier affects trade?",
            self._known_types(),
        )
        self.assertIn("chokepoint", result)

    def test_synonym_recommendation_resolves_to_mitigation_action(self):
        result = self.engine._resolve_entity_types_from_question(
            "What recommendation is given for this risk?",
            self._known_types(),
        )
        self.assertIn("mitigation_action", result)

    def test_custom_synonym_map_overrides_default(self):
        custom_map = {
            "vulnerability": ["chokepoint"],
            "safeguard": ["mitigation_action"],
        }
        result = self.engine._resolve_entity_types_from_question(
            "What vulnerability affects this safeguard?",
            self._known_types(),
            synonym_map=custom_map,
        )
        self.assertIn("chokepoint", result)
        self.assertIn("mitigation_action", result)
        # Default synonyms should NOT apply when custom map is provided
        self.assertNotIn("risk_indicator", result)

    def test_q19_original_wording_triggers_chain_discovery(self):
        """Q19 original: 'What evidence supports the risk finding behind this mitigation action?'"""
        plan = self.engine._plan_question_paths(
            "What evidence supports the risk finding behind this mitigation action?",
            "mitigation_action",
            self.entity_config,
            self.link_config,
            self.descriptions,
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertTrue(len(plan.admissible_chains) >= 1)
        chain = plan.admissible_chains[0]
        self.assertEqual(chain[0], "mitigation_action:n:1:risk_finding")
        self.assertEqual(chain[1], "risk_finding:n:m:evidence")

    def test_synonym_nation_resolves_to_country_and_triggers_link(self):
        plan = self.engine._plan_question_paths(
            "Which nations depend on this barrier?",
            "chokepoint",
            self.entity_config,
            self.link_config,
            self.descriptions,
        )
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("country:n:m:chokepoint_dependency", plan.selected_link_keys)

    def test_no_synonym_match_returns_empty_set(self):
        result = self.engine._resolve_entity_types_from_question(
            "Tell me about this entity",
            self._known_types(),
        )
        self.assertEqual(result, set())


if __name__ == "__main__":
    unittest.main()
