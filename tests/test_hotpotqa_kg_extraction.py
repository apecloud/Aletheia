#!/usr/bin/env python3
"""HotpotQARelationExtractor: retry-on-low-quality-parse behavior.

A response that parses as valid JSON but required falling back to a
default for a closed-world-validated field (topic_title not one of the
given titles, or every triple's from_title invalid) is treated the same
as a parse failure -- worth a retry, since provider-side non-determinism
means a repeat request at the same temperature often does better.

Run: python -m unittest tests.test_hotpotqa_kg_extraction
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from hotpotqa_kg_extraction import HotpotQARelationExtractor


def _response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = json.dumps(payload)
    resp.choices[0].message.reasoning_content = None
    resp.choices[0].message.reasoning = None
    return resp


CONTEXT = [
    ["Aly Raisman", ["Aly Raisman is an American gymnast born May 25, 1994."]],
    ["Some Distractor", ["Not the entity this question is about."]],
]


class NeedsRetryTest(unittest.TestCase):
    def test_topic_title_not_in_valid_titles_needs_retry(self):
        self.assertTrue(HotpotQARelationExtractor._needs_retry(
            {"topic_title": "Garbled Slug", "triples": []}, {"Aly Raisman", "Some Distractor"},
        ))

    def test_all_triples_invalid_from_title_needs_retry(self):
        self.assertTrue(HotpotQARelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": [{"from_title": "Nonexistent Title"}]},
            {"Aly Raisman", "Some Distractor"},
        ))

    def test_valid_topic_and_at_least_one_valid_triple_is_fine(self):
        self.assertFalse(HotpotQARelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": [{"from_title": "Aly Raisman"}]},
            {"Aly Raisman", "Some Distractor"},
        ))

    def test_valid_topic_with_no_triples_at_all_is_fine(self):
        """No triples extracted isn't inherently a quality problem -- only
        triples that exist but are ALL invalid signal a bad response."""
        self.assertFalse(HotpotQARelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": []}, {"Aly Raisman", "Some Distractor"},
        ))


class ExtractLocalGraphRetryTest(unittest.TestCase):
    def test_low_quality_first_response_retries_and_uses_better_second_response(self):
        """topic_title falls back on attempt 1 (not one of the given
        titles) -- must retry rather than silently accept the fallback,
        and use the good second response instead of the first."""
        extractor = HotpotQARelationExtractor()

        bad_response = _response({
            "triples": [],
            "topic_title": "Some Garbled Nonexistent Title",
            "entity_mentions": [],
        })
        good_response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
                "is_literal": True, "evidence": "born May 25, 1994",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", side_effect=[bad_response, good_response]) as mock_completion:
            graph = extractor.extract_local_graph("Who was born May 25, 1994?", CONTEXT)

        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(graph.topic_title, "Aly Raisman")
        self.assertEqual(len(graph.triples), 1)
        self.assertFalse(graph.used_fallback)

    def test_all_attempts_low_quality_still_returns_best_effort_not_crash(self):
        """When every retry is exhausted and still low-quality, degrade
        gracefully to the last attempt's (fallback) topic_title rather
        than erroring out."""
        extractor = HotpotQARelationExtractor()

        bad_response = _response({
            "triples": [],
            "topic_title": "Some Garbled Nonexistent Title",
            "entity_mentions": [],
        })

        with patch("litellm.completion", side_effect=[bad_response, bad_response]) as mock_completion:
            graph = extractor.extract_local_graph("Who was born May 25, 1994?", CONTEXT)

        self.assertEqual(mock_completion.call_count, 2)
        # Falls back to the first context title, same degrade-gracefully
        # behavior as an outright parse failure.
        self.assertEqual(graph.topic_title, "Aly Raisman")

    def test_good_first_response_does_not_retry(self):
        extractor = HotpotQARelationExtractor()

        good_response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
                "is_literal": True, "evidence": "born May 25, 1994",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=good_response) as mock_completion:
            graph = extractor.extract_local_graph("Who was born May 25, 1994?", CONTEXT)

        self.assertEqual(mock_completion.call_count, 1)
        self.assertEqual(graph.topic_title, "Aly Raisman")


if __name__ == "__main__":
    unittest.main()
