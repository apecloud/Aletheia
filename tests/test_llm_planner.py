#!/usr/bin/env python3
"""Tests for LLM-enhanced question-to-relation mapping (Phase 8, task #25).

Tests cover:
1. LLMPlanner construction and model resolution
2. PlannerMapping dataclass defaults
3. _format_relations output format
4. LLM mapping integration with _plan_question_paths
5. Fallback behavior when LLM is unavailable
6. Mock LLM returning correct relations
7. Env-var gating (ALETHEIA_LLM_PLANNER_ENABLED)

Run: python -m unittest tests/test_llm_planner
"""

import json
import os
import unittest
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

from llm_planner import LLMPlanner, PlannerMapping, RelationCandidate
from reasoning_engine import ReasoningEngine


class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

SAMPLE_LINK_CONFIG = [
    {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t_person", "fk_col": "nationality"},
    {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t_person", "fk_col": "profession"},
    {"link": "person:n:m:people_person_education", "from": "person", "to": "school", "fk_table": "t_person", "fk_col": "education"},
    {"link": "person:n:m:government_politician_government_positions_held", "from": "person", "to": "government", "fk_table": "t_person", "fk_col": "positions"},
    {"link": "location:n:m:location_location_time_zones", "from": "location", "to": "time", "fk_table": "t_location", "fk_col": "time_zones"},
    {"link": "location:n:m:location_statistical_region_population", "from": "location", "to": "location", "fk_table": "t_location", "fk_col": "population"},
]

SAMPLE_DESCRIPTIONS = {
    "person:n:m:people_person_nationality": "the nationality of a person",
    "person:n:m:people_person_profession": "the profession of a person",
    "person:n:m:people_person_education": "the education institution of a person",
    "person:n:m:government_politician_government_positions_held": "government positions held by a politician",
    "location:n:m:location_location_time_zones": "time zones for a location",
    "location:n:m:location_statistical_region_population": "population of a statistical region",
}


class TestLLMPlannerConstruction(unittest.TestCase):
    """Test LLMPlanner construction and model resolution."""

    def test_default_construction(self):
        """LLMPlanner can be constructed without arguments."""
        planner = LLMPlanner()
        self.assertIsNotNone(planner)
        self.assertIsNotNone(planner.model)
        self.assertGreater(len(planner.system_prompt), 0)

    def test_custom_model(self):
        """LLMPlanner accepts a custom model name."""
        planner = LLMPlanner(model="gpt-4o")
        self.assertEqual(planner.model, "gpt-4o")

    def test_model_resolution_openrouter_env(self):
        """Model resolves from ALETHEIA_OPENROUTER env vars."""
        old_provider = os.environ.get("ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER")
        old_model = os.environ.get("ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL")
        try:
            os.environ["ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER"] = "openrouter"
            os.environ["ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL"] = "openai/gpt-oss-20b:free"
            planner = LLMPlanner()
            self.assertEqual(planner.model, "openrouter/openai/gpt-oss-20b:free")
        finally:
            if old_provider is not None:
                os.environ["ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER"] = old_provider
            elif "ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER" in os.environ:
                del os.environ["ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER"]
            if old_model is not None:
                os.environ["ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL"] = old_model
            elif "ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL" in os.environ:
                del os.environ["ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL"]

    def test_custom_prompts(self):
        """LLMPlanner accepts custom system prompt and user template."""
        planner = LLMPlanner(
            system_prompt="Custom system prompt",
            user_template="Question: {question}\nType: {topic_type}\nRelations:\n{relations}",
        )
        self.assertEqual(planner.system_prompt, "Custom system prompt")
        self.assertIn("{question}", planner.user_template)


class TestPlannerMappingDefaults(unittest.TestCase):
    """Test PlannerMapping dataclass defaults."""

    def test_empty_defaults(self):
        """PlannerMapping has correct empty defaults."""
        m = PlannerMapping()
        self.assertEqual(m.matched_link_keys, set())
        self.assertEqual(m.matched_entity_types, set())
        self.assertEqual(m.confidence_scores, {})
        self.assertEqual(m.latency_ms, 0.0)
        self.assertEqual(m.token_usage, 0)
        self.assertFalse(m.used_fallback)
        self.assertEqual(m.error, "")
        self.assertEqual(m.error_type, "")


class TestFormatRelations(unittest.TestCase):
    """Test _format_relations output format."""

    def test_format_with_descriptions(self):
        """Relations are formatted with link_key, types, and description."""
        planner = LLMPlanner()
        text = planner._format_relations(SAMPLE_LINK_CONFIG, SAMPLE_DESCRIPTIONS)
        self.assertIn("people_person_nationality", text)
        self.assertIn("from=person", text)
        self.assertIn("to=country", text)
        self.assertIn("nationality of a person", text)

    def test_format_without_descriptions(self):
        """Relations format correctly when descriptions are empty."""
        planner = LLMPlanner()
        text = planner._format_relations(SAMPLE_LINK_CONFIG, {})
        self.assertIn("people_person_nationality", text)
        self.assertNotIn("description", text)

    def test_format_truncates_long_lists(self):
        """Relation list is truncated to max_relations."""
        planner = LLMPlanner()
        large_config = [
            {"link": f"type:n:m:rel_{i}", "from": "type", "to": "type", "fk_table": "t", "fk_col": f"c{i}"}
            for i in range(100)
        ]
        text = planner._format_relations(large_config, {}, max_relations=80)
        # Should contain rel_0 through rel_79 but not rel_80+
        self.assertIn("rel_0", text)
        self.assertIn("rel_79", text)
        self.assertNotIn("rel_80", text)


class TestLLMPlannerFallback(unittest.TestCase):
    """Test LLM planner fallback behavior."""

    def test_empty_question_returns_empty(self):
        """Empty question returns empty PlannerMapping."""
        planner = LLMPlanner()
        result = planner.map_question_to_relations(
            "", "person", SAMPLE_LINK_CONFIG, SAMPLE_DESCRIPTIONS
        )
        self.assertEqual(result.matched_link_keys, set())
        self.assertEqual(result.error, "empty question")

    def test_empty_link_config_returns_empty(self):
        """Empty link_config returns empty PlannerMapping."""
        planner = LLMPlanner()
        result = planner.map_question_to_relations(
            "what nationality", "person", [], {}
        )
        self.assertEqual(result.matched_link_keys, set())
        self.assertEqual(result.error, "empty link_config")


class TestReasoningEngineIntegration(unittest.TestCase):
    """Test LLM planner integration with ReasoningEngine._plan_question_paths."""

    def test_planner_off_by_default(self):
        """LLM planner is not activated without env var or constructor arg."""
        # Ensure env var is not set
        old_val = os.environ.pop("ALETHEIA_LLM_PLANNER_ENABLED", None)
        try:
            engine = ReasoningEngine(FakeRepo())
            self.assertIsNone(engine._get_llm_planner())
        finally:
            if old_val is not None:
                os.environ["ALETHEIA_LLM_PLANNER_ENABLED"] = old_val

    def test_planner_enabled_via_env(self):
        """LLM planner activates when env var is set."""
        os.environ["ALETHEIA_LLM_PLANNER_ENABLED"] = "1"
        try:
            engine = ReasoningEngine(FakeRepo())
            planner = engine._get_llm_planner()
            self.assertIsNotNone(planner)
            self.assertIsInstance(planner, LLMPlanner)
        finally:
            del os.environ["ALETHEIA_LLM_PLANNER_ENABLED"]

    def test_planner_via_constructor(self):
        """LLM planner activates when passed via constructor."""
        mock_planner = MagicMock(spec=LLMPlanner)
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        self.assertIs(engine._get_llm_planner(), mock_planner)

    def test_llm_mapping_merges_into_plan(self):
        """LLM-matched link keys are merged into the plan."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.95},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality of the president",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("country", plan.selected_target_types)

    def test_fallback_when_llm_returns_empty(self):
        """Keyword matching still works when LLM returns empty."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            used_fallback=True,
            error="llm unavailable",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what time zones are in this location",
            "location",
            {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # Keyword matching should still find the time zone link
        self.assertIn("location:n:m:location_location_time_zones", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)

    def test_llm_adds_relations_keyword_matching_misses(self):
        """LLM can match relations that keyword matching misses."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.9},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        # Question that keyword matching would miss (no "nationality" in link desc keywords)
        plan = engine._plan_question_paths(
            "where was the president born",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # LLM should have added the nationality link
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)


class TestLLMPlannerMockedCall(unittest.TestCase):
    """Test actual LLM call with mocked litellm."""

    def test_mocked_llm_call_returns_relations(self):
        """LLM call returns structured relation candidates."""
        planner = LLMPlanner(model="mock-model")

        # Create mock response matching LLMPlannerResult
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'person:n:m:people_person_nationality', 'confidence': 0.95, 'reasoning': 'Question asks about nationality'}], 'matched_entity_types': ['country']})
        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            result = planner.map_question_to_relations(
                "what is the nationality of the president",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertIn("person:n:m:people_person_nationality", result.matched_link_keys)
        self.assertIn("country", result.matched_entity_types)
        self.assertAlmostEqual(
            result.confidence_scores["person:n:m:people_person_nationality"], 0.95
        )
        self.assertFalse(result.used_fallback)
        self.assertGreater(result.latency_ms, 0)
        self.assertNotIn("max_tokens", mock_completion.call_args.kwargs)
        self.assertEqual(
            mock_completion.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )

    def test_openrouter_call_disables_reasoning_for_json_planner(self):
        """OpenRouter Qwen planner calls prefer final JSON over thinking output."""
        planner = LLMPlanner(model="openrouter/qwen/qwen3.6-27b")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "selected_relations": [
                {
                    "link_key": "person:n:m:people_person_nationality",
                    "confidence": 0.9,
                    "reasoning": "nationality question",
                }
            ],
            "matched_entity_types": ["country"],
        })

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertEqual(
            mock_completion.call_args.kwargs["reasoning"],
            {"effort": "none", "exclude": True},
        )
        self.assertEqual(
            mock_completion.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )

    def test_empty_content_recovers_json_from_reasoning_content(self):
        """Recover provider responses that put final JSON in reasoning_content."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.reasoning_content = json.dumps({
            "selected_relations": [
                {
                    "link_key": "person:n:m:people_person_nationality",
                    "confidence": 0.88,
                    "reasoning": "Question asks for nationality.",
                }
            ],
            "matched_entity_types": ["country"],
        })

        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertFalse(result.used_fallback)
        self.assertEqual(result.error_type, "")
        self.assertIn("person:n:m:people_person_nationality", result.matched_link_keys)
        self.assertAlmostEqual(
            result.confidence_scores["person:n:m:people_person_nationality"], 0.88,
        )

    def test_empty_content_retries_once_and_uses_valid_second_response(self):
        """Retry transient provider stop responses that contain no text."""
        planner = LLMPlanner(model="mock-model")

        empty_response = MagicMock()
        empty_response.choices = [MagicMock()]
        empty_response.choices[0].finish_reason = "stop"
        empty_response.choices[0].message.content = ""
        empty_response.choices[0].message.reasoning_content = None
        empty_response.choices[0].message.reasoning = None

        valid_response = MagicMock()
        valid_response.choices = [MagicMock()]
        valid_response.choices[0].finish_reason = "stop"
        valid_response.choices[0].message.content = json.dumps({
            "selected_relations": [
                {
                    "link_key": "person:n:m:people_person_nationality",
                    "confidence": 0.91,
                    "reasoning": "Question asks for nationality.",
                }
            ],
            "matched_entity_types": ["country"],
        })

        with patch("litellm.completion", side_effect=[empty_response, valid_response]) as mock_completion:
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertFalse(result.used_fallback)
        self.assertEqual(result.error_type, "")
        self.assertIn("person:n:m:people_person_nationality", result.matched_link_keys)
        self.assertEqual(mock_completion.call_count, 2)
        self.assertNotIn("max_tokens", mock_completion.call_args.kwargs)

    def test_empty_content_without_recoverable_json_is_runtime_invalid(self):
        """Empty final content is an invalid LLM result, not an ordinary miss."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.choices[0].message.reasoning_content = "I should think about nationality first."

        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.error_type, "runtime_invalid")
        self.assertIn("JSON", result.error)
        self.assertEqual(result.matched_link_keys, set())

    def test_malformed_non_empty_response_is_not_retried(self):
        """Do not hide deterministic parser failures by retrying non-empty text."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "stop"
        mock_response.choices[0].message.content = "not json"

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.error_type, "runtime_invalid")
        self.assertEqual(mock_completion.call_count, 1)

    def test_length_stopped_reasoning_only_response_is_runtime_invalid(self):
        """Qwen-style length stop with reasoning only must not score as miss."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].finish_reason = "length"
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.reasoning_content = (
            "We need answer with JSON, but the response budget was consumed "
            "before final content was emitted."
        )

        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is henry clay known for",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.error_type, "runtime_invalid")
        self.assertIn("finish_reason=length", result.error)
        self.assertEqual(result.matched_link_keys, set())

    def test_env_max_tokens_does_not_set_completion_cap(self):
        """LLM planner does not impose a default/env max-token completion cap."""
        old_value = os.environ.get("ALETHEIA_LLM_PLANNER_MAX_TOKENS")
        try:
            os.environ["ALETHEIA_LLM_PLANNER_MAX_TOKENS"] = "256"
            planner = LLMPlanner(model="mock-model")
            self.assertIsNone(planner.max_tokens)

            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = json.dumps({
                "selected_relations": [
                    {
                        "link_key": "person:n:m:people_person_nationality",
                        "confidence": 0.95,
                        "reasoning": "nationality question",
                    }
                ],
                "matched_entity_types": ["country"],
            })
            with patch("litellm.completion", return_value=mock_response) as mock_completion:
                planner.map_question_to_relations(
                    "what is the nationality of the president",
                    "person",
                    SAMPLE_LINK_CONFIG,
                    SAMPLE_DESCRIPTIONS,
                )

            self.assertNotIn("max_tokens", mock_completion.call_args.kwargs)
        finally:
            if old_value is None:
                os.environ.pop("ALETHEIA_LLM_PLANNER_MAX_TOKENS", None)
            else:
                os.environ["ALETHEIA_LLM_PLANNER_MAX_TOKENS"] = old_value

    def test_explicit_max_tokens_is_ignored(self):
        """Compatibility argument does not create a provider max-token cap."""
        planner = LLMPlanner(model="mock-model", max_tokens=256)
        self.assertIsNone(planner.max_tokens)

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "selected_relations": [
                {
                    "link_key": "person:n:m:people_person_nationality",
                    "confidence": 0.95,
                    "reasoning": "nationality question",
                }
            ],
            "matched_entity_types": ["country"],
        })

        with patch("litellm.completion", return_value=mock_response) as mock_completion:
            planner.map_question_to_relations(
                "what is the nationality of the president",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertNotIn("max_tokens", mock_completion.call_args.kwargs)

    def test_llm_error_triggers_fallback(self):
        """LLM error results in fallback with error message."""
        planner = LLMPlanner(model="mock-model")

        with patch("litellm.completion", side_effect=Exception("API error")):
            result = planner.map_question_to_relations(
                "what nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertIn("API error", result.error)
        self.assertEqual(result.error_type, "runtime")
        self.assertEqual(result.matched_link_keys, set())

    def test_openrouter_credit_error_is_classified_as_provider_error(self):
        """OpenRouter 402/credit errors are separated from planner misses."""
        planner = LLMPlanner(model="mock-model")

        with patch(
            "litellm.completion",
            side_effect=Exception("OpenRouter 402: requires more credits, or fewer max_tokens"),
        ):
            result = planner.map_question_to_relations(
                "what nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertEqual(result.error_type, "provider")


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Additional test fixtures by @Conan (task #25 test infrastructure)
# Focus: WebQSP-style fixtures, malformed output, timeout, observability,
# multi-relation ranking, and normalization edge cases.
# ---------------------------------------------------------------------------

# WebQSP-style fixtures: realistic Freebase relation patterns that keyword
# matching struggles with but LLM should handle.
WEBQSP_FIXTURES = [
    {
        "question": "What is the nationality of the person?",
        "topic_type": "person",
        "entity_config": {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
        "link_config": [
            {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t", "fk_col": "nat"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "prof"},
            {"link": "person:n:m:people_person_education", "from": "person", "to": "school", "fk_table": "t", "fk_col": "edu"},
        ],
        "descriptions": {
            "person:n:m:people_person_nationality": "the nationality of a person",
            "person:n:m:people_person_profession": "the profession of a person",
            "person:n:m:people_person_education": "the education institution of a person",
        },
        "expected_link_keys": {"person:n:m:people_person_nationality"},
        "expected_types": {"country"},
    },
    {
        "question": "Who directed this film?",
        "topic_type": "film",
        "entity_config": {"film": {"table": "t", "pk": "id", "artifact": "object:film"}},
        "link_config": [
            {"link": "film:n:m:film_film_directed_by", "from": "film", "to": "person", "fk_table": "t", "fk_col": "dir"},
            {"link": "film:n:m:film_film_genre", "from": "film", "to": "genre", "fk_table": "t", "fk_col": "genre"},
            {"link": "film:n:m:film_film_music", "from": "film", "to": "person", "fk_table": "t", "fk_col": "music"},
        ],
        "descriptions": {
            "film:n:m:film_film_directed_by": "the director of a film",
            "film:n:m:film_film_genre": "the genre of a film",
            "film:n:m:film_film_music": "the music composer of a film",
        },
        "expected_link_keys": {"film:n:m:film_film_directed_by"},
        "expected_types": {"person"},
    },
    {
        "question": "What language is spoken in this country?",
        "topic_type": "country",
        "entity_config": {"country": {"table": "t", "pk": "id", "artifact": "object:country"}},
        "link_config": [
            {"link": "country:n:m:location_country_languages_spoken", "from": "country", "to": "language", "fk_table": "t", "fk_col": "lang"},
            {"link": "country:n:m:location_country_capital", "from": "country", "to": "city", "fk_table": "t", "fk_col": "cap"},
            {"link": "country:n:m:location_country_currency", "from": "country", "to": "currency", "fk_table": "t", "fk_col": "cur"},
        ],
        "descriptions": {
            "country:n:m:location_country_languages_spoken": "languages spoken in a country",
            "country:n:m:location_country_capital": "the capital city of a country",
            "country:n:m:location_country_currency": "the currency used in a country",
        },
        "expected_link_keys": {"country:n:m:location_country_languages_spoken"},
        "expected_types": {"language"},
    },
    {
        "question": "What is the capital of this country?",
        "topic_type": "country",
        "entity_config": {"country": {"table": "t", "pk": "id", "artifact": "object:country"}},
        "link_config": [
            {"link": "country:n:m:location_country_languages_spoken", "from": "country", "to": "language", "fk_table": "t", "fk_col": "lang"},
            {"link": "country:n:m:location_country_capital", "from": "country", "to": "city", "fk_table": "t", "fk_col": "cap"},
            {"link": "country:n:m:location_country_currency", "from": "country", "to": "currency", "fk_table": "t", "fk_col": "cur"},
        ],
        "descriptions": {
            "country:n:m:location_country_languages_spoken": "languages spoken in a country",
            "country:n:m:location_country_capital": "the capital city of a country",
            "country:n:m:location_country_currency": "the currency used in a country",
        },
        "expected_link_keys": {"country:n:m:location_country_capital"},
        "expected_types": {"city"},
    },
    {
        "question": "What books did this author write?",
        "topic_type": "person",
        "entity_config": {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
        "link_config": [
            {"link": "person:n:m:book_author_works_written", "from": "person", "to": "book", "fk_table": "t", "fk_col": "works"},
            {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t", "fk_col": "nat"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "prof"},
        ],
        "descriptions": {
            "person:n:m:book_author_works_written": "books written by an author",
            "person:n:m:people_person_nationality": "the nationality of a person",
            "person:n:m:people_person_profession": "the profession of a person",
        },
        "expected_link_keys": {"person:n:m:book_author_works_written"},
        "expected_types": {"book"},
    },
]


class TestWebQSPStyleFixtures(unittest.TestCase):
    """WebQSP-style question fixtures with mock LLM returning expected relations.

    These fixtures simulate what the LLM planner should return for typical
    WebQSP multi-hop questions. The mock LLM returns the expected_link_keys
    for each fixture, and we verify the planner correctly merges them into
    the QuestionPathPlan.
    """

    def _run_fixture(self, fixture):
        """Run a single WebQSP fixture with a mock LLM planner."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=fixture["expected_link_keys"],
            matched_entity_types=fixture["expected_types"],
            confidence_scores={k: 0.9 for k in fixture["expected_link_keys"]},
            model="mock-qwen",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            fixture["question"],
            fixture["topic_type"],
            fixture["entity_config"],
            fixture["link_config"],
            fixture["descriptions"],
        )
        return plan

    def test_nationality_question(self):
        """WebQSP-style: nationality question selects nationality relation."""
        plan = self._run_fixture(WEBQSP_FIXTURES[0])
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("country", plan.selected_target_types)

    def test_director_question(self):
        """WebQSP-style: director question selects directed_by relation."""
        plan = self._run_fixture(WEBQSP_FIXTURES[1])
        self.assertIn("film:n:m:film_film_directed_by", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)

    def test_language_question(self):
        """WebQSP-style: language question selects languages_spoken relation."""
        plan = self._run_fixture(WEBQSP_FIXTURES[2])
        self.assertIn("country:n:m:location_country_languages_spoken", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)

    def test_capital_question(self):
        """WebQSP-style: capital question selects capital relation."""
        plan = self._run_fixture(WEBQSP_FIXTURES[3])
        self.assertIn("country:n:m:location_country_capital", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)

    def test_author_books_question(self):
        """WebQSP-style: author books question selects works_written relation."""
        plan = self._run_fixture(WEBQSP_FIXTURES[4])
        self.assertIn("person:n:m:book_author_works_written", plan.selected_link_keys)
        self.assertFalse(plan.is_full_aggregation)


class TestMalformedLLMOutput(unittest.TestCase):
    """Test handling of malformed or unexpected LLM outputs."""

    def test_hallucinated_link_keys_filtered(self):
        """LLM returns link keys not in valid set; only valid ones are kept.

        The filtering happens inside LLMPlanner.map_question_to_relations()
        which validates against valid_link_keys. We mock litellm so the
        actual filtering code runs.
        """
        planner = LLMPlanner(model="mock-model")

        # LLM returns one valid and one hallucinated key
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'person:n:m:people_person_nationality', 'confidence': 0.9, 'reasoning': 'valid'}, {'link_key': 'FAKE_RELATION_KEY', 'confidence': 0.8, 'reasoning': 'hallucinated'}], 'matched_entity_types': ['country']})
        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertIn("person:n:m:people_person_nationality", result.matched_link_keys)
        self.assertNotIn("FAKE_RELATION_KEY", result.matched_link_keys)

    def test_empty_llm_response(self):
        """LLM returns empty selected_relations list."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            matched_entity_types=set(),
            confidence_scores={},
            used_fallback=True,
            error="empty response",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # Should fall back to keyword matching (nationality is in description)
        self.assertFalse(plan.is_full_aggregation)

    def test_llm_returns_only_invalid_keys(self):
        """LLM returns only hallucinated keys, no valid ones.

        LLMPlanner filters them out. Keyword matching in _plan_question_paths
        still finds the right relation via description.
        """
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'totally_fake_key', 'confidence': 0.7, 'reasoning': 'hallucinated'}], 'matched_entity_types': []})
        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        # All keys were hallucinated, so none should survive filtering
        self.assertEqual(result.matched_link_keys, set())


class TestLLMTimeout(unittest.TestCase):
    """Test LLM timeout handling."""

    def test_timeout_triggers_fallback(self):
        """Timeout exception triggers fallback to keyword matching."""
        planner = LLMPlanner(model="mock-model", timeout=0.001)

        with patch("litellm.completion", side_effect=TimeoutError("Request timed out")):
            result = planner.map_question_to_relations(
                "what nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertIn("timed out", result.error.lower())
        self.assertEqual(result.matched_link_keys, set())
        self.assertGreater(result.latency_ms, 0)

    def test_timeout_does_not_crash_planner(self):
        """Timeout is caught and engine still returns a valid plan via keyword fallback."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            used_fallback=True,
            error="timed out",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what time zones are in this location",
            "location",
            {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # Keyword fallback should still work
        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("location:n:m:location_location_time_zones", plan.selected_link_keys)


class TestObservabilityMetadata(unittest.TestCase):
    """Test that observability metadata is recorded in PlannerMapping."""

    def test_successful_call_records_model_and_latency(self):
        """Successful LLM call records model name and latency."""
        planner = LLMPlanner(model="openrouter/qwen/qwen3.6-27b")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'person:n:m:people_person_nationality', 'confidence': 0.92, 'reasoning': 'nationality question'}], 'matched_entity_types': ['country']})
        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertEqual(result.model, "openrouter/qwen/qwen3.6-27b")
        self.assertGreater(result.latency_ms, 0)
        self.assertFalse(result.used_fallback)
        self.assertEqual(result.error, "")

    def test_error_call_records_latency_and_error(self):
        """Failed LLM call still records latency and error message."""
        planner = LLMPlanner(model="mock-model")

        with patch("litellm.completion", side_effect=Exception("rate limit")):
            result = planner.map_question_to_relations(
                "what nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertIn("rate limit", result.error)
        self.assertGreater(result.latency_ms, 0)

    def test_confidence_scores_populated(self):
        """Confidence scores are populated for matched relations."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.87},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # The plan itself doesn't carry confidence scores, but the planner
        # mapping should. Verify the mock was called and returned scores.
        mock_planner.map_question_to_relations.assert_called_once()
        call_result = mock_planner.map_question_to_relations.return_value
        self.assertAlmostEqual(
            call_result.confidence_scores["person:n:m:people_person_nationality"], 0.87
        )


class TestMultiRelationRanking(unittest.TestCase):
    """Test LLM returning multiple ranked relations with varying confidence."""

    def test_multiple_relations_all_merged(self):
        """LLM returns multiple relations; all are merged into the plan."""
        multi_link_config = [
            {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t", "fk_col": "nat"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "prof"},
            {"link": "person:n:m:people_person_education", "from": "person", "to": "school", "fk_table": "t", "fk_col": "edu"},
        ]
        multi_desc = {
            "person:n:m:people_person_nationality": "the nationality of a person",
            "person:n:m:people_person_profession": "the profession of a person",
            "person:n:m:people_person_education": "the education institution of a person",
        }
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
            },
            matched_entity_types={"country", "profession"},
            confidence_scores={
                "person:n:m:people_person_nationality": 0.95,
                "person:n:m:people_person_profession": 0.72,
            },
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the background of this person",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            multi_link_config,
            multi_desc,
        )
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_profession", plan.selected_link_keys)
        self.assertIn("country", plan.selected_target_types)
        self.assertIn("profession", plan.selected_target_types)

    def test_low_confidence_relation_still_included(self):
        """Even low-confidence relations are included (current behavior: no threshold)."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.3},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)


class TestLLMConvergencePolicy(unittest.TestCase):
    """Test configurable relation fanout reduction after LLM planning."""

    def test_llm_top_k_limits_selected_relations(self):
        """Only the top-k ranked LLM relations are selected by default policy."""
        link_config = [
            {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t", "fk_col": "nat"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "prof"},
            {"link": "person:n:m:people_person_education", "from": "person", "to": "school", "fk_table": "t", "fk_col": "edu"},
            {"link": "person:n:m:people_person_place_of_birth", "from": "person", "to": "location", "fk_table": "t", "fk_col": "birthplace"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={lc["link"] for lc in link_config},
            ranked_link_keys=[
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
                "person:n:m:people_person_education",
                "person:n:m:people_person_place_of_birth",
            ],
            matched_entity_types={"country", "profession", "school", "location"},
            confidence_scores={
                "person:n:m:people_person_nationality": 0.95,
                "person:n:m:people_person_profession": 0.90,
                "person:n:m:people_person_education": 0.80,
                "person:n:m:people_person_place_of_birth": 0.70,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "2",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "0",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "tell me about this person",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertEqual(
            plan.selected_link_keys,
            {
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
            },
        )
        self.assertTrue(plan.llm_convergence_applied)
        self.assertEqual(len(plan.llm_ranked_link_keys), 4)
        self.assertEqual(len(plan.keyword_link_keys), 4)

    def test_confidence_threshold_filters_low_confidence_relations(self):
        """Configured min confidence removes lower-confidence LLM relations."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
            },
            ranked_link_keys=[
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
            ],
            matched_entity_types={"country", "profession"},
            confidence_scores={
                "person:n:m:people_person_nationality": 0.91,
                "person:n:m:people_person_profession": 0.42,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "0",
                "ALETHEIA_LLM_PLANNER_MIN_CONFIDENCE": "0.8",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "0",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what is this person's background",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertEqual(
            plan.selected_link_keys,
            {"person:n:m:people_person_nationality"},
        )
        self.assertEqual(
            plan.llm_confidence_scores["person:n:m:people_person_profession"],
            0.42,
        )

    def test_keyword_union_can_be_reenabled(self):
        """Opt-in env flag preserves old LLM-plus-keyword union behavior."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            ranked_link_keys=["person:n:m:people_person_nationality"],
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.95},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_INCLUDE_KEYWORD_UNION": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "0",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what profession and nationality does this person have",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_profession", plan.selected_link_keys)

    def test_disabling_convergence_preserves_previous_union_behavior(self):
        """Convergence can be disabled to keep all LLM and keyword links."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
                "person:n:m:people_person_education",
                "person:n:m:people_person_place_of_birth",
            },
            ranked_link_keys=[
                "person:n:m:people_person_nationality",
                "person:n:m:people_person_profession",
                "person:n:m:people_person_education",
                "person:n:m:people_person_place_of_birth",
            ],
            matched_entity_types={"country", "profession", "school", "location"},
            confidence_scores={},
            model="mock",
        )
        link_config = SAMPLE_LINK_CONFIG + [
            {"link": "person:n:m:people_person_place_of_birth", "from": "person", "to": "location", "fk_table": "t_person", "fk_col": "birthplace"},
        ]
        with patch.dict(
            os.environ,
            {"ALETHEIA_LLM_PLANNER_CONVERGENCE_ENABLED": "0"},
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "tell me about this person",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_profession", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_education", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_place_of_birth", plan.selected_link_keys)
        self.assertFalse(plan.llm_convergence_applied)

    def test_lexical_recall_guard_adds_relation_ranked_below_top_k(self):
        """A bounded lexical guard preserves recall for obvious relation keys."""
        link_config = [
            {"link": "person:n:m:government_politician_government_positions_held", "from": "person", "to": "government", "fk_table": "t", "fk_col": "pos"},
            {"link": "person:n:m:government_us_president_vice_president", "from": "person", "to": "person", "fk_table": "t", "fk_col": "vp"},
            {"link": "time:n:m:base_inaugurations_inauguration_president", "from": "time", "to": "person", "fk_table": "t", "fk_col": "pres"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={
                "person:n:m:government_politician_government_positions_held",
            },
            ranked_link_keys=[
                "person:n:m:government_politician_government_positions_held",
            ],
            matched_entity_types={"government"},
            confidence_scores={
                "person:n:m:government_politician_government_positions_held": 0.9,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "who was the president after jfk died",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertIn(
            "person:n:m:government_us_president_vice_president",
            plan.selected_link_keys,
        )

    def test_known_for_lexical_recall_prefers_profession_and_works_over_notability(self):
        """WebQTrn-152 regression: known-for should not be captured by notability only."""
        link_config = [
            {"link": "person:n:m:common_topic_notable_for", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "notable"},
            {"link": "person:n:m:freebase_notability_hints_notable_for", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "hint"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "person", "fk_table": "t", "fk_col": "profession"},
            {"link": "person:n:m:book_author_works_written", "from": "person", "to": "book", "fk_table": "t", "fk_col": "works"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:common_topic_notable_for"},
            ranked_link_keys=["person:n:m:common_topic_notable_for"],
            matched_entity_types={"entity"},
            confidence_scores={"person:n:m:common_topic_notable_for": 0.91},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "0",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what is henry clay known for",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertIn("person:n:m:people_person_profession", plan.selected_link_keys)
        self.assertIn("person:n:m:book_author_works_written", plan.selected_link_keys)
        self.assertNotIn(
            "person:n:m:freebase_notability_hints_notable_for",
            plan.lexical_recall_keys,
        )

    def test_topic_compatibility_reranks_wrong_domain_before_top_k(self):
        """Wrong-domain LLM picks should not displace topic-compatible candidates."""
        link_config = [
            {"link": "entity:n:m:base_locations_planets_countries_within", "from": "entity", "to": "location", "fk_table": "t", "fk_col": "entity_country"},
            {"link": "film:n:m:language_human_language_countries_spoken_in", "from": "film", "to": "location", "fk_table": "t", "fk_col": "film_country"},
            {"link": "location:n:m:location_country_internet_tld", "from": "location", "to": "location", "fk_table": "t", "fk_col": "tld"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={lc["link"] for lc in link_config},
            ranked_link_keys=[
                "entity:n:m:base_locations_planets_countries_within",
                "film:n:m:language_human_language_countries_spoken_in",
                "location:n:m:location_country_internet_tld",
            ],
            matched_entity_types={"location"},
            confidence_scores={
                "entity:n:m:base_locations_planets_countries_within": 0.96,
                "film:n:m:language_human_language_countries_spoken_in": 0.95,
                "location:n:m:location_country_internet_tld": 0.91,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "0",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what other countries does canada trade with",
                "location",
                {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
                link_config,
                {},
            )

        self.assertEqual(
            plan.selected_link_keys,
            {"location:n:m:location_country_internet_tld"},
        )
        self.assertEqual(
            plan.llm_top_k_link_keys,
            ["location:n:m:location_country_internet_tld"],
        )

    def test_topic_compatibility_suppresses_wrong_domain_government_relation(self):
        """WebQTrn-349-style wrong-domain government relation should lose to person-domain relation."""
        link_config = [
            {"link": "government:n:m:government_government_position_held_office_holder", "from": "government", "to": "person", "fk_table": "t", "fk_col": "holder"},
            {"link": "person:n:m:organization_organization_founder_organizations_founded", "from": "person", "to": "organization", "fk_table": "t", "fk_col": "founded"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={lc["link"] for lc in link_config},
            ranked_link_keys=[
                "government:n:m:government_government_position_held_office_holder",
                "person:n:m:organization_organization_founder_organizations_founded",
            ],
            matched_entity_types={"government", "organization"},
            confidence_scores={
                "government:n:m:government_government_position_held_office_holder": 0.96,
                "person:n:m:organization_organization_founder_organizations_founded": 0.88,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "0",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what kind of government did benito mussolini have",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertEqual(
            plan.selected_link_keys,
            {"person:n:m:organization_organization_founder_organizations_founded"},
        )

    def test_currency_question_lexical_recall_prefers_country_currency_used(self):
        """WebQTrn-1150-style currency questions should recover currency_used."""
        link_config = [
            {"link": "location:n:m:location_country_currency_used", "from": "location", "to": "currency", "fk_table": "t", "fk_col": "currency"},
            {"link": "location:n:m:location_country_currency_formerly_used", "from": "location", "to": "currency", "fk_table": "t", "fk_col": "old_currency"},
            {"link": "entity:n:m:finance_currency_countries_formerly_used", "from": "entity", "to": "location", "fk_table": "t", "fk_col": "former"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"location:n:m:location_country_currency_formerly_used"},
            ranked_link_keys=["location:n:m:location_country_currency_formerly_used"],
            matched_entity_types={"currency"},
            confidence_scores={"location:n:m:location_country_currency_formerly_used": 0.6},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "what currency does brazil use",
                "location",
                {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
                link_config,
                {},
            )

        self.assertIn("location:n:m:location_country_currency_used", plan.selected_link_keys)
        self.assertIn("location:n:m:location_country_currency_used", plan.lexical_recall_keys)

    def test_person_hometown_lexical_recall_prefers_place_of_birth(self):
        """WebQTrn-429-style hometown questions should recover birthplace."""
        link_config = [
            {"link": "person:n:m:people_person_places_lived", "from": "person", "to": "location", "fk_table": "t", "fk_col": "lived"},
            {"link": "person:n:m:people_person_place_of_birth", "from": "person", "to": "location", "fk_table": "t", "fk_col": "birth"},
            {"link": "person:n:m:freebase_valuenotation_is_reviewed", "from": "person", "to": "review", "fk_table": "t", "fk_col": "reviewed"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_places_lived"},
            ranked_link_keys=["person:n:m:people_person_places_lived"],
            matched_entity_types={"location"},
            confidence_scores={"person:n:m:people_person_places_lived": 0.95},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "where is abraham lincoln hometown",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertIn("person:n:m:people_person_place_of_birth", plan.selected_link_keys)
        self.assertIn("person:n:m:people_person_place_of_birth", plan.lexical_recall_keys)

    def test_person_where_live_hint_recovers_residence_links(self):
        """WebQTrn-891-style live questions should recover residence relations."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        bad_links = [
            "architecture:n:m:architecture_occupancy_occupant",
            "architecture:n:m:people_deceased_person_place_of_death",
            "architecture:n:m:people_person_nationality",
            "architecture:n:m:people_person_place_of_birth",
        ]
        link_config = [
            {"link": bad_links[0], "from": "architecture", "to": "person", "fk_table": "t", "fk_col": "occupant"},
            {"link": bad_links[1], "from": "architecture", "to": "person", "fk_table": "t", "fk_col": "death_place"},
            {"link": bad_links[2], "from": "architecture", "to": "person", "fk_table": "t", "fk_col": "nationality"},
            {"link": bad_links[3], "from": "architecture", "to": "person", "fk_table": "t", "fk_col": "birth_place"},
            {"link": "person:n:m:people_person_places_lived", "from": "person", "to": "location", "fk_table": "t", "fk_col": "places_lived"},
            {"link": "person:n:m:people_place_lived_location", "from": "person", "to": "location", "fk_table": "t", "fk_col": "place_lived_location"},
            {"link": "person:n:m:people_place_lived_person", "from": "location", "to": "person", "fk_table": "t", "fk_col": "place_lived_person"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(bad_links),
            ranked_link_keys=list(bad_links),
            matched_entity_types={"location"},
            confidence_scores={key: 0.6 for key in bad_links},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "4",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            plan = engine._plan_question_paths(
                "where did john howard live",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        expected_residence = {
            "person:n:m:people_person_places_lived",
            "person:n:m:people_place_lived_location",
        }
        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(set(plan.lexical_recall_keys), expected_residence)
        self.assertTrue(expected_residence.issubset(plan.selected_link_keys))
        self.assertEqual(len(plan.lexical_recall_keys), 2)
        self.assertLessEqual(len(plan.selected_link_keys), 6)

    def test_where_live_hint_does_not_capture_nationality_question(self):
        """Nationality controls should not pick residence links without live intent."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        link_config = [
            {"link": "person:n:m:people_person_nationality", "from": "person", "to": "country", "fk_table": "t", "fk_col": "nationality"},
            {"link": "person:n:m:people_person_places_lived", "from": "person", "to": "location", "fk_table": "t", "fk_col": "places_lived"},
            {"link": "person:n:m:people_place_lived_location", "from": "person", "to": "location", "fk_table": "t", "fk_col": "place_lived_location"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            plan = engine._plan_question_paths(
                "what is nina dobrev nationality",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertFalse(plan.is_full_aggregation)
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        self.assertNotIn("person:n:m:people_person_places_lived", plan.selected_link_keys)
        self.assertNotIn("person:n:m:people_place_lived_location", plan.selected_link_keys)

    def test_where_live_hint_does_not_capture_architecture_occupancy_question(self):
        """Live wording outside person-centered questions should preserve occupancy."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        link_config = [
            {"link": "architecture:n:m:architecture_occupancy_occupant", "from": "architecture", "to": "person", "fk_table": "t", "fk_col": "occupant"},
            {"link": "person:n:m:people_person_places_lived", "from": "person", "to": "location", "fk_table": "t", "fk_col": "places_lived"},
            {"link": "person:n:m:people_place_lived_location", "from": "person", "to": "location", "fk_table": "t", "fk_col": "place_lived_location"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            plan = engine._plan_question_paths(
                "where does this building occupant live",
                "architecture",
                {"architecture": {"table": "t", "pk": "id", "artifact": "object:architecture"}},
                link_config,
                {},
            )

        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(plan.lexical_recall_keys, ["architecture:n:m:architecture_occupancy_occupant"])
        self.assertEqual(plan.selected_link_keys, {"architecture:n:m:architecture_occupancy_occupant"})

    def test_empty_llm_rank_still_allows_lexical_recall(self):
        """WebQTrn-418-style clean empty LLM rank should not force full aggregation."""
        link_config = [
            {"link": "person:n:m:people_deceased_person_place_of_death", "from": "person", "to": "location", "fk_table": "t", "fk_col": "death_place"},
            {"link": "person:n:m:music_artist_album", "from": "person", "to": "album", "fk_table": "t", "fk_col": "album"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "where was john lennon standing when he was shot",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertFalse(plan.is_full_aggregation)
        self.assertEqual(
            plan.selected_link_keys,
            {"person:n:m:people_deceased_person_place_of_death"},
        )
        self.assertEqual(
            plan.lexical_recall_keys,
            ["person:n:m:people_deceased_person_place_of_death"],
        )

    def test_webqsp_hint_config_recovers_empty_llm_relation_intents(self):
        """Reviewable WebQSP hints should cover common relation intents without qid rules."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        link_config = [
            {"link": "tv:n:m:film_film_character_portrayed_in_films", "from": "tv", "to": "character", "fk_table": "t", "fk_col": "portrayed"},
            {"link": "music:n:m:film_actor_film", "from": "music", "to": "film", "fk_table": "t", "fk_col": "film"},
            {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "profession"},
            {"link": "language:n:m:language_human_language_countries_spoken_in", "from": "language", "to": "location", "fk_table": "t", "fk_col": "countries"},
            {"link": "sports:n:m:organization_organization_founders", "from": "sports", "to": "person", "fk_table": "t", "fk_col": "founders"},
            {"link": "person:n:m:base_formula1_formula_1_driver_team_member", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "team"},
            {"link": "person:n:m:people_marriage_spouse", "from": "person", "to": "person", "fk_table": "t", "fk_col": "spouse"},
            {"link": "person:n:m:law_inventor_inventions", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "invention"},
            {"link": "sports:n:m:sports_sports_team_championships", "from": "sports", "to": "event", "fk_table": "t", "fk_col": "championship"},
            {"link": "person:n:m:people_deceased_person_cause_of_death", "from": "person", "to": "cause", "fk_table": "t", "fk_col": "cause"},
            {"link": "location:n:m:government_governmental_jurisdiction_governing_officials", "from": "location", "to": "person", "fk_table": "t", "fk_col": "officials"},
        ]
        cases = [
            ("who did the actor play in the film", "tv", "tv:n:m:film_film_character_portrayed_in_films"),
            ("who was the singer in the movie", "music", "music:n:m:film_actor_film"),
            ("who is the public figure", "person", "person:n:m:people_person_profession"),
            ("who speaks farsi", "language", "language:n:m:language_human_language_countries_spoken_in"),
            ("who was the first leader of the league", "sports", "sports:n:m:organization_organization_founders"),
            ("who did the driver drive for", "person", "person:n:m:base_formula1_formula_1_driver_team_member"),
            ("who did the person marry", "person", "person:n:m:people_marriage_spouse"),
            ("what else did the inventor invent", "person", "person:n:m:law_inventor_inventions"),
            ("when did the team win the superbowl", "sports", "sports:n:m:sports_sports_team_championships"),
            ("what did the king die of", "person", "person:n:m:people_deceased_person_cause_of_death"),
            ("who is prime minister of the country", "location", "location:n:m:government_governmental_jurisdiction_governing_officials"),
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )

        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            for question, object_type, expected in cases:
                with self.subTest(question=question):
                    plan = engine._plan_question_paths(
                        question,
                        object_type,
                        {object_type: {"table": "t", "pk": "id", "artifact": f"object:{object_type}"}},
                        link_config,
                        {},
                    )
                    self.assertFalse(plan.is_full_aggregation)
                    self.assertIn(expected, plan.selected_link_keys)
                    self.assertIn(expected, plan.lexical_recall_keys)
                    self.assertLessEqual(len(plan.selected_link_keys), 2)

    def test_language_centered_country_speaker_hint_prefers_forward_language_relation(self):
        """Empty LLM output should keep language->country relation over to-language aliases."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        link_config = [
            {"link": "film:n:m:location_country_languages_spoken", "from": "film", "to": "language", "fk_table": "t", "fk_col": "languages"},
            {"link": "film:n:m:location_country_official_language", "from": "film", "to": "language", "fk_table": "t", "fk_col": "official"},
            {"link": "organization:n:m:location_country_languages_spoken", "from": "organization", "to": "language", "fk_table": "t", "fk_col": "languages"},
            {"link": "language:n:m:language_language_family_member_of_language_families", "from": "language", "to": "language", "fk_table": "t", "fk_col": "family_member"},
            {"link": "language:n:m:language_human_language_countries_spoken_in", "from": "language", "to": "location", "fk_table": "t", "fk_col": "countries"},
        ]
        descriptions = {
            "film:n:m:location_country_languages_spoken": "languages spoken in a country",
            "film:n:m:location_country_official_language": "official language of a country",
            "organization:n:m:location_country_languages_spoken": "languages spoken in a country",
            "language:n:m:language_language_family_member_of_language_families": "language family for this language",
            "language:n:m:language_human_language_countries_spoken_in": "countries where this language is spoken",
        }
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )

        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            plan = engine._plan_question_paths(
                "what countries around the world speak french",
                "language",
                {"language": {"table": "t", "pk": "id", "artifact": "object:language"}},
                link_config,
                descriptions,
            )

        self.assertFalse(plan.is_full_aggregation)
        self.assertIn(
            "language:n:m:language_human_language_countries_spoken_in",
            plan.lexical_recall_keys,
        )
        self.assertIn(
            "language:n:m:language_human_language_countries_spoken_in",
            plan.selected_link_keys,
        )
        self.assertLessEqual(len(plan.lexical_recall_keys), 2)

    def test_phase29_relation_config_hints_recover_bounded_lexical_candidates(self):
        """Phase 29 hints should recover below-recall gold links without broad fanout."""
        hint_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "config",
            "reasoning_lexical_hints.webqsp_freebase.json",
        )
        with open(hint_path, encoding="utf-8") as fh:
            hint_config = json.load(fh)

        cases = [
            {
                "question": "what language group does polish belong to",
                "object_type": "language",
                "links": [
                    {"link": "language:n:m:language_language_dialect_language", "from": "language", "to": "language", "fk_table": "t", "fk_col": "dialect"},
                    {"link": "language:n:m:language_language_family_member_of_language_families", "from": "language", "to": "language", "fk_table": "t", "fk_col": "member"},
                    {"link": "language:n:m:base_rosetta_languoid_languoid_class", "from": "language", "to": "entity", "fk_table": "t", "fk_col": "class"},
                    {"link": "language:n:m:base_rosetta_languoid_parent", "from": "language", "to": "entity", "fk_table": "t", "fk_col": "parent"},
                    {"link": "language:n:m:language_human_language_language_family", "from": "language", "to": "language", "fk_table": "t", "fk_col": "family"},
                ],
                "expected": {
                    "language:n:m:base_rosetta_languoid_languoid_class",
                    "language:n:m:base_rosetta_languoid_parent",
                },
                "not_expected": {
                    "language:n:m:language_language_dialect_language",
                    "language:n:m:language_language_family_member_of_language_families",
                },
            },
            {
                "question": "what type of music does this artist play",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:music_music_video_performances", "from": "person", "to": "music_video", "fk_table": "t", "fk_col": "video"},
                    {"link": "person:n:m:music_special_music_video_appearances", "from": "person", "to": "music_video", "fk_table": "t", "fk_col": "special_video"},
                    {"link": "person:n:m:music_artist_genre", "from": "person", "to": "music", "fk_table": "t", "fk_col": "genre"},
                    {"link": "person:n:m:film_music_contributor_film", "from": "person", "to": "film", "fk_table": "t", "fk_col": "film"},
                ],
                "expected": {
                    "person:n:m:music_artist_genre",
                    "person:n:m:film_music_contributor_film",
                },
                "not_expected": {
                    "person:n:m:music_music_video_performances",
                    "person:n:m:music_special_music_video_appearances",
                },
            },
            {
                "question": "what clubs has this player played for",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:film_actor_film", "from": "person", "to": "film", "fk_table": "t", "fk_col": "film"},
                    {"link": "person:n:m:film_film_character_portrayed", "from": "person", "to": "character", "fk_table": "t", "fk_col": "character"},
                    {"link": "person:n:m:sports_pro_athlete_teams", "from": "person", "to": "sports", "fk_table": "t", "fk_col": "teams"},
                    {"link": "sports:n:m:sports_sports_team_roster_player", "from": "sports", "to": "person", "fk_table": "t", "fk_col": "player"},
                    {"link": "person:n:m:sports_pro_athlete_sports_played_professionally", "from": "person", "to": "sports", "fk_table": "t", "fk_col": "sports"},
                ],
                "expected": {
                    "sports:n:m:sports_sports_team_roster_player",
                    "person:n:m:sports_pro_athlete_sports_played_professionally",
                },
                "not_expected": {
                    "person:n:m:film_actor_film",
                    "person:n:m:film_film_character_portrayed",
                },
            },
            {
                "question": "who does this athlete play for",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:film_actor_film", "from": "person", "to": "film", "fk_table": "t", "fk_col": "film"},
                    {"link": "person:n:m:film_film_character_portrayed_in_films", "from": "person", "to": "film", "fk_table": "t", "fk_col": "portrayed"},
                    {"link": "person:n:m:sports_pro_athlete_teams", "from": "person", "to": "sports", "fk_table": "t", "fk_col": "teams"},
                    {"link": "sports:n:m:sports_sports_team_roster_player", "from": "sports", "to": "person", "fk_table": "t", "fk_col": "player"},
                    {"link": "person:n:m:base_schemastaging_athlete_extra_salary", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "salary"},
                ],
                "expected": {
                    "person:n:m:sports_pro_athlete_teams",
                    "sports:n:m:sports_sports_team_roster_player",
                },
                "not_expected": {
                    "person:n:m:film_actor_film",
                    "person:n:m:film_film_character_portrayed_in_films",
                },
            },
            {
                "question": "what movies has this actor been in",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:film_actor_film", "from": "person", "to": "film", "fk_table": "t", "fk_col": "film"},
                    {"link": "film:n:m:film_performance_actor", "from": "film", "to": "person", "fk_table": "t", "fk_col": "actor"},
                    {"link": "person:n:m:sports_pro_athlete_teams", "from": "person", "to": "sports", "fk_table": "t", "fk_col": "teams"},
                    {"link": "sports:n:m:sports_sports_team_roster_player", "from": "sports", "to": "person", "fk_table": "t", "fk_col": "player"},
                ],
                "expected": {
                    "person:n:m:film_actor_film",
                    "film:n:m:film_performance_actor",
                },
                "not_expected": {
                    "person:n:m:sports_pro_athlete_teams",
                    "sports:n:m:sports_sports_team_roster_player",
                },
            },
            {
                "question": "what was this actor's character in star wars",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:base_webvideo_internet_video_character_videos_this_character_appeared_in", "from": "person", "to": "entity", "fk_table": "t", "fk_col": "video"},
                    {"link": "fictional:n:m:fictional_universe_fictional_character_character_created_by", "from": "fictional", "to": "person", "fk_table": "t", "fk_col": "creator"},
                    {"link": "person:n:m:film_actor_film", "from": "person", "to": "film", "fk_table": "t", "fk_col": "film"},
                    {"link": "film:n:m:film_performance_actor", "from": "film", "to": "person", "fk_table": "t", "fk_col": "actor"},
                    {"link": "person:n:m:tv_tv_actor_starring_roles", "from": "person", "to": "tv", "fk_table": "t", "fk_col": "role"},
                    {"link": "tv:n:m:tv_regular_tv_appearance_actor", "from": "tv", "to": "person", "fk_table": "t", "fk_col": "actor"},
                    {"link": "person:n:m:sports_pro_athlete_teams", "from": "person", "to": "sports", "fk_table": "t", "fk_col": "teams"},
                    {"link": "sports:n:m:sports_sports_team_roster_player", "from": "sports", "to": "person", "fk_table": "t", "fk_col": "player"},
                ],
                "expected": {
                    "person:n:m:film_actor_film",
                    "film:n:m:film_performance_actor",
                },
                "not_expected": {
                    "person:n:m:base_webvideo_internet_video_character_videos_this_character_appeared_in",
                    "fictional:n:m:fictional_universe_fictional_character_character_created_by",
                    "person:n:m:sports_pro_athlete_teams",
                    "sports:n:m:sports_sports_team_roster_player",
                },
            },
        ]

        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )

        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config=hint_config,
            )
            for case in cases:
                with self.subTest(question=case["question"]):
                    plan = engine._plan_question_paths(
                        case["question"],
                        case["object_type"],
                        {case["object_type"]: {"table": "t", "pk": "id", "artifact": f"object:{case['object_type']}"}},
                        case["links"],
                        {},
                    )

                    self.assertFalse(plan.is_full_aggregation)
                    self.assertEqual(set(plan.lexical_recall_keys), case["expected"])
                    self.assertTrue(case["expected"].issubset(plan.selected_link_keys))
                    self.assertFalse(case["not_expected"] & plan.selected_link_keys)
                    self.assertLessEqual(len(plan.lexical_recall_keys), 2)

    def test_voice_character_hint_is_loaded_from_config(self):
        """Configured voice/character hints should recover character relations."""
        hint_config = {
            "term_expansions": [
                {"when_any": ["voice", "played"], "add": ["character"]},
            ],
            "relation_bonuses": [
                {
                    "when_any": ["voice", "played"],
                    "link_contains_any": ["film_performance_character"],
                    "bonus": 10,
                },
                {
                    "when_any": ["voice", "played"],
                    "link_contains_any": ["tv_tv_actor_starring_roles"],
                    "bonus": -3,
                },
            ],
        }
        link_config = [
            {"link": "film:n:m:film_performance_character", "from": "person", "to": "character", "fk_table": "t", "fk_col": "character"},
            {"link": "tv:n:m:tv_tv_actor_starring_roles", "from": "person", "to": "show", "fk_table": "t", "fk_col": "role"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"tv:n:m:tv_tv_actor_starring_roles"},
            ranked_link_keys=["tv:n:m:tv_tv_actor_starring_roles"],
            matched_entity_types={"show"},
            confidence_scores={"tv:n:m:tv_tv_actor_starring_roles": 0.8},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner, lexical_hint_config=hint_config)
            plan = engine._plan_question_paths(
                "who played the voice of this character",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertIn("film:n:m:film_performance_character", plan.lexical_recall_keys)
        self.assertIn("film:n:m:film_performance_character", plan.selected_link_keys)

    def test_city_contains_hint_is_loaded_from_config(self):
        """Configured city/contains hints should recover containing-area links."""
        hint_config = {
            "term_expansions": [
                {"when_any": ["city"], "add": ["contains", "capital"]},
            ],
            "relation_bonuses": [
                {
                    "when_any": ["city"],
                    "link_contains_any": ["location_location_partially_contains", "location_us_state_capital"],
                    "bonus": 8,
                },
                {
                    "when_any": ["city"],
                    "link_contains_any": ["containedby", "administrative_parent"],
                    "bonus": -3,
                },
            ],
        }
        link_config = [
            {"link": "location:n:m:location_location_containedby", "from": "location", "to": "location", "fk_table": "t", "fk_col": "parent"},
            {"link": "location:n:m:location_location_partially_contains", "from": "location", "to": "location", "fk_table": "t", "fk_col": "contains"},
            {"link": "location:n:m:location_us_state_capital", "from": "location", "to": "location", "fk_table": "t", "fk_col": "capital"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"location:n:m:location_location_containedby"},
            ranked_link_keys=["location:n:m:location_location_containedby"],
            matched_entity_types={"location"},
            confidence_scores={"location:n:m:location_location_containedby": 0.7},
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner, lexical_hint_config=hint_config)
            plan = engine._plan_question_paths(
                "what city is the state capital",
                "location",
                {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
                link_config,
                {},
            )

        self.assertEqual(plan.lexical_recall_keys, ["location:n:m:location_us_state_capital"])
        self.assertIn("location:n:m:location_us_state_capital", plan.selected_link_keys)

    def test_empty_hint_config_keeps_lexical_recall_bounded(self):
        """No configured hints should not crash or expand unrelated fanout."""
        link_config = [
            {"link": "location:n:m:location_location_partially_contains", "from": "location", "to": "location", "fk_table": "t", "fk_col": "contains"},
            {"link": "location:n:m:location_us_state_capital", "from": "location", "to": "location", "fk_table": "t", "fk_col": "capital"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config={"term_expansions": [], "relation_bonuses": []},
            )
            plan = engine._plan_question_paths(
                "what city is this in",
                "location",
                {"location": {"table": "t", "pk": "id", "artifact": "object:location"}},
                link_config,
                {},
            )

        self.assertEqual(plan.lexical_recall_keys, [])
        self.assertLessEqual(len(plan.selected_link_keys), 2)

    def test_parent_question_lexical_recall_ignores_stopword_is_reviewed_trap(self):
        """WebQTrn-249-style parent questions should not prefer is_reviewed."""
        link_config = [
            {"link": "person:n:m:people_person_parents", "from": "person", "to": "person", "fk_table": "t", "fk_col": "parents"},
            {"link": "book:n:m:freebase_valuenotation_is_reviewed", "from": "book", "to": "person", "fk_table": "t", "fk_col": "book_reviewed"},
            {"link": "government:n:m:freebase_valuenotation_is_reviewed", "from": "government", "to": "person", "fk_table": "t", "fk_col": "gov_reviewed"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys=set(),
            ranked_link_keys=[],
            matched_entity_types=set(),
            confidence_scores={},
            model="mock",
            used_fallback=False,
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "2",
            },
            clear=False,
        ):
            engine = ReasoningEngine(
                FakeRepo(),
                llm_planner=mock_planner,
                lexical_hint_config={"term_expansions": [], "relation_bonuses": []},
            )
            plan = engine._plan_question_paths(
                "who is tia and tamera mowry parents",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertIn("person:n:m:people_person_parents", plan.selected_link_keys)
        self.assertNotIn("book:n:m:freebase_valuenotation_is_reviewed", plan.selected_link_keys)
        self.assertNotIn("government:n:m:freebase_valuenotation_is_reviewed", plan.selected_link_keys)

    def test_topic_forward_preference_recovers_wrong_namespace_suffix_alias(self):
        """Forward topic links should outrank reverse/wrong-namespace suffix aliases."""
        link_config = [
            {"link": "person:n:m:people_person_parents", "from": "person", "to": "person", "fk_table": "t", "fk_col": "parents"},
            {"link": "film:n:m:people_person_parents", "from": "film", "to": "person", "fk_table": "t", "fk_col": "film_parents"},
            {"link": "music:n:m:people_person_parents", "from": "music", "to": "person", "fk_table": "t", "fk_col": "music_parents"},
        ]
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={
                "film:n:m:people_person_parents",
                "music:n:m:people_person_parents",
            },
            ranked_link_keys=[
                "film:n:m:people_person_parents",
                "music:n:m:people_person_parents",
            ],
            matched_entity_types=set(),
            confidence_scores={
                "film:n:m:people_person_parents": 0.9,
                "music:n:m:people_person_parents": 0.8,
            },
            model="mock",
        )
        with patch.dict(
            os.environ,
            {
                "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
            },
            clear=False,
        ):
            engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
            plan = engine._plan_question_paths(
                "who is tia and tamera mowry parents",
                "person",
                {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
                link_config,
                {},
            )

        self.assertEqual(plan.lexical_recall_keys, ["person:n:m:people_person_parents"])
        self.assertIn("person:n:m:people_person_parents", plan.selected_link_keys)

    def test_relation_family_config_recovers_inverse_language_and_known_for_relations(self):
        """Configured family hints recover exact gold links from inverse aliases."""
        scenarios = [
            {
                "question": "what language do you speak in iran",
                "object_type": "location",
                "links": [
                    {"link": "location:n:m:location_country_languages_spoken", "from": "location", "to": "language", "fk_table": "t", "fk_col": "languages"},
                    {"link": "location:n:m:location_country_official_language", "from": "location", "to": "language", "fk_table": "t", "fk_col": "official_language"},
                    {"link": "language:n:m:language_human_language_countries_spoken_in", "from": "language", "to": "location", "fk_table": "t", "fk_col": "countries"},
                    {"link": "location:n:m:language_human_language_language_family", "from": "location", "to": "language", "fk_table": "t", "fk_col": "family"},
                ],
                "ranked": [
                    "language:n:m:language_human_language_countries_spoken_in",
                    "location:n:m:language_human_language_language_family",
                ],
                "expected_any": [
                    "location:n:m:location_country_languages_spoken",
                    "location:n:m:location_country_official_language",
                ],
            },
            {
                "question": "what is henry clay known for",
                "object_type": "person",
                "links": [
                    {"link": "person:n:m:people_person_profession", "from": "person", "to": "profession", "fk_table": "t", "fk_col": "profession"},
                    {"link": "person:n:m:book_author_works_written", "from": "person", "to": "book", "fk_table": "t", "fk_col": "works"},
                    {"link": "person:n:m:people_profession_people_with_this_profession", "from": "profession", "to": "person", "fk_table": "t", "fk_col": "people"},
                    {"link": "person:n:m:base_lightweight_profession_job_count_regional", "from": "person", "to": "metric", "fk_table": "t", "fk_col": "metric"},
                ],
                "ranked": [
                    "person:n:m:people_profession_people_with_this_profession",
                    "person:n:m:base_lightweight_profession_job_count_regional",
                ],
                "expected_any": ["person:n:m:people_person_profession"],
            },
        ]

        for scenario in scenarios:
            with self.subTest(question=scenario["question"]):
                mock_planner = MagicMock(spec=LLMPlanner)
                mock_planner.map_question_to_relations.return_value = PlannerMapping(
                    matched_link_keys=set(scenario["ranked"]),
                    ranked_link_keys=list(scenario["ranked"]),
                    matched_entity_types=set(),
                    confidence_scores={key: 0.8 for key in scenario["ranked"]},
                    model="mock",
                )
                with patch.dict(
                    os.environ,
                    {
                        "ALETHEIA_LLM_PLANNER_TOP_K": "1",
                        "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
                    },
                    clear=False,
                ):
                    engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
                    plan = engine._plan_question_paths(
                        scenario["question"],
                        scenario["object_type"],
                        {scenario["object_type"]: {"table": "t", "pk": "id", "artifact": f"object:{scenario['object_type']}"}},
                        scenario["links"],
                        {},
                    )

            self.assertTrue(set(scenario["expected_any"]) & set(plan.lexical_recall_keys))
            self.assertTrue(set(scenario["expected_any"]) & set(plan.selected_link_keys))

    def test_topic_forward_preference_keeps_legitimate_reverse_relations(self):
        """Reverse/to-topic relations still work when they are the lexical match."""
        scenarios = [
            {
                "question": "what books did this author write",
                "object_type": "person",
                "link": "book:n:m:book_written_work_author",
                "from": "book",
                "to": "person",
            },
            {
                "question": "what awards won include this actor",
                "object_type": "person",
                "link": "award:n:m:award_award_winner_awards_won",
                "from": "award",
                "to": "person",
            },
            {
                "question": "what languages are spoken in this country",
                "object_type": "location",
                "link": "language:n:m:language_human_language_countries_spoken_in",
                "from": "language",
                "to": "location",
            },
            {
                "question": "what teams include this player",
                "object_type": "person",
                "link": "sports:n:m:sports_sports_team_roster_player",
                "from": "sports_team",
                "to": "person",
            },
        ]

        for scenario in scenarios:
            with self.subTest(link=scenario["link"]):
                link_config = [
                    {
                        "link": scenario["link"],
                        "from": scenario["from"],
                        "to": scenario["to"],
                        "fk_table": "t",
                        "fk_col": "target",
                    },
                    {
                        "link": f"{scenario['object_type']}:n:m:unrelated_forward_metric",
                        "from": scenario["object_type"],
                        "to": "metric",
                        "fk_table": "t",
                        "fk_col": "metric",
                    },
                ]
                mock_planner = MagicMock(spec=LLMPlanner)
                mock_planner.map_question_to_relations.return_value = PlannerMapping(
                    matched_link_keys=set(),
                    ranked_link_keys=[],
                    matched_entity_types=set(),
                    confidence_scores={},
                    model="mock",
                    used_fallback=False,
                )
                with patch.dict(
                    os.environ,
                    {
                        "ALETHEIA_LLM_PLANNER_TOP_K": "10",
                        "ALETHEIA_LLM_PLANNER_LEXICAL_RECALL_K": "1",
                    },
                    clear=False,
                ):
                    engine = ReasoningEngine(
                        FakeRepo(),
                        llm_planner=mock_planner,
                        lexical_hint_config={"term_expansions": [], "relation_bonuses": []},
                    )
                    plan = engine._plan_question_paths(
                        scenario["question"],
                        scenario["object_type"],
                        {scenario["object_type"]: {"table": "t", "pk": "id", "artifact": f"object:{scenario['object_type']}"}},
                        link_config,
                        {},
                    )

            self.assertEqual(plan.lexical_recall_keys, [scenario["link"]])
            self.assertIn(scenario["link"], plan.selected_link_keys)


class TestNormalizationEdgeCases(unittest.TestCase):
    """Test normalization edge cases as flagged by @Karpathy.

    If the LLM returns link keys that differ in case, spacing, or separator
    from the link_config entries, the planner should handle them gracefully.
    Currently the planner filters against valid_link_keys in
    LLMPlanner.map_question_to_relations(), so only exact matches pass.
    These tests document that behavior and guard against silent breakage
    if normalization is added later.
    """

    def test_exact_match_accepted(self):
        """Exact link_key match from LLM is accepted."""
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"country"},
            confidence_scores={"person:n:m:people_person_nationality": 0.9},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)

    def test_case_mismatch_filtered_out(self):
        """Link key with different case is filtered by LLMPlanner.

        LLMPlanner.map_question_to_relations validates against exact
        link_config keys. Uppercase variants are silently dropped.
        """
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'PERSON:N:M:PEOPLE_PERSON_NATIONALITY', 'confidence': 0.9, 'reasoning': 'uppercase variant'}], 'matched_entity_types': ['country']})
        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        # Uppercase key doesn't match any link_config entry, so it's dropped
        self.assertNotIn("PERSON:N:M:PEOPLE_PERSON_NATIONALITY", result.matched_link_keys)
        self.assertEqual(result.matched_link_keys, set())

    def test_whitespace_variant_filtered_out(self):
        """Link key with spaces instead of underscores is filtered out.

        LLMPlanner validates against exact link_config keys. Space-variant
        keys are silently dropped.
        """
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({'selected_relations': [{'link_key': 'person:n:m:people person nationality', 'confidence': 0.85, 'reasoning': 'space variant'}], 'matched_entity_types': ['country']})
        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertNotIn("person:n:m:people person nationality", result.matched_link_keys)
        self.assertEqual(result.matched_link_keys, set())

    def test_llm_entity_types_normalized_by_planner(self):
        """LLM-returned entity types are used for chain enumeration.

        The planner lowercases entity types when merging into matched_types.
        If the LLM returns 'Country' (uppercase), the planner should still
        work because _plan_question_paths lowercases known_types and
        matched_types.
        """
        mock_planner = MagicMock(spec=LLMPlanner)
        mock_planner.map_question_to_relations.return_value = PlannerMapping(
            matched_link_keys={"person:n:m:people_person_nationality"},
            matched_entity_types={"Country"},  # uppercase
            confidence_scores={"person:n:m:people_person_nationality": 0.9},
            model="mock",
        )
        engine = ReasoningEngine(FakeRepo(), llm_planner=mock_planner)
        plan = engine._plan_question_paths(
            "what is the nationality",
            "person",
            {"person": {"table": "t", "pk": "id", "artifact": "object:person"}},
            SAMPLE_LINK_CONFIG,
            SAMPLE_DESCRIPTIONS,
        )
        # The link key should still be selected (from LLM matched_link_keys)
        self.assertIn("person:n:m:people_person_nationality", plan.selected_link_keys)
        # Entity types in the plan are lowercased by the planner
        self.assertIn("country", plan.selected_target_types)


if __name__ == "__main__":
    unittest.main()


class TestMalformedJSONResponse(unittest.TestCase):
    """Test handling when LLM returns non-JSON text instead of structured output.

    This reproduces the error @dullboy reported: the LLM returns free-form
    text (e.g. model description prose) instead of the expected pydantic
    schema. The instructor library should raise an error that gets caught
    by the try/except in map_question_to_relations(), triggering fallback.
    """

    def test_non_json_text_response_triggers_fallback(self):
        """LLM returns plain text instead of structured JSON; planner falls back."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            "I think the answer is about nationality and "
            "tailored writing to improve relevance & readability..."
        )

        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what is the nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertIn("JSON", result.error)
        self.assertEqual(result.error_type, "runtime_invalid")
        self.assertEqual(result.matched_link_keys, set())
        self.assertGreater(result.latency_ms, 0)

    def test_partial_json_response_triggers_fallback(self):
        """LLM returns incomplete JSON; planner falls back gracefully."""
        planner = LLMPlanner(model="mock-model")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"selected_relations": [{"link_key": "person:n:m:people_per'
        )

        with patch("litellm.completion", return_value=mock_response):
            result = planner.map_question_to_relations(
                "what nationality",
                "person",
                SAMPLE_LINK_CONFIG,
                SAMPLE_DESCRIPTIONS,
            )

        self.assertTrue(result.used_fallback)
        self.assertIn("JSON", result.error)
        self.assertEqual(result.error_type, "runtime_invalid")
        self.assertEqual(result.matched_link_keys, set())
