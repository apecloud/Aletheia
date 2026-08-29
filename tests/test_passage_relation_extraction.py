#!/usr/bin/env python3
"""PassageRelationExtractor: retry-on-low-quality-parse behavior.

A response that parses as valid JSON but required falling back to a
default for a closed-world-validated field (topic_title not one of the
given titles, or every triple's from_title invalid) is treated the same
as a parse failure -- worth a retry, since provider-side non-determinism
means a repeat request at the same temperature often does better.

Run: python -m unittest tests.test_passage_relation_extraction
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# tests/__init__.py normally puts scripts/ on sys.path, but unittest
# discover's file-based module loading doesn't reliably run it before an
# early-imported module needs it -- see tests/__init__.py's docstring.
# Self-contained fallback so passage_relation_extraction resolves
# regardless of discovery order.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "scripts") not in sys.path:
    sys.path.append(str(_ROOT / "scripts"))

from passage_relation_extraction import PassageRelationExtractor


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
        self.assertTrue(PassageRelationExtractor._needs_retry(
            {"topic_title": "Garbled Slug", "triples": []}, {"Aly Raisman", "Some Distractor"},
        ))

    def test_all_triples_invalid_from_title_needs_retry(self):
        self.assertTrue(PassageRelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": [{"from_title": "Nonexistent Title"}]},
            {"Aly Raisman", "Some Distractor"},
        ))

    def test_valid_topic_and_at_least_one_valid_triple_is_fine(self):
        self.assertFalse(PassageRelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": [{"from_title": "Aly Raisman"}]},
            {"Aly Raisman", "Some Distractor"},
        ))

    def test_valid_topic_with_no_triples_at_all_is_fine(self):
        """No triples extracted isn't inherently a quality problem -- only
        triples that exist but are ALL invalid signal a bad response."""
        self.assertFalse(PassageRelationExtractor._needs_retry(
            {"topic_title": "Aly Raisman", "triples": []}, {"Aly Raisman", "Some Distractor"},
        ))

    def test_self_referential_triple_needs_retry(self):
        """The model echoing from_title back as to_value (e.g. confusing a
        father/son pair sharing a surname and differing only by a
        generational ordinal) is a specific, real extraction failure mode,
        not a "some entity is its own parent" edge case to accept as-is."""
        self.assertTrue(PassageRelationExtractor._needs_retry(
            {
                "topic_title": "Aly Raisman",
                "triples": [{
                    "from_title": "Aly Raisman", "to_value": "Aly Raisman",
                    "relation": "father", "is_literal": False,
                }],
            },
            {"Aly Raisman", "Some Distractor"},
        ))

    def test_self_referential_literal_triple_does_not_need_retry(self):
        """is_literal=true triples are never checked against valid_titles at
        all -- from_title equaling a literal to_value (e.g. a name that
        happens to match a literal span) isn't the same failure mode."""
        self.assertFalse(PassageRelationExtractor._needs_retry(
            {
                "topic_title": "Aly Raisman",
                "triples": [{
                    "from_title": "Aly Raisman", "to_value": "Aly Raisman",
                    "relation": "nickname", "is_literal": True,
                }],
            },
            {"Aly Raisman", "Some Distractor"},
        ))


class ExtractLocalGraphRetryTest(unittest.TestCase):
    def test_low_quality_first_response_retries_and_uses_better_second_response(self):
        """topic_title falls back on attempt 1 (not one of the given
        titles) -- must retry rather than silently accept the fallback,
        and use the good second response instead of the first."""
        extractor = PassageRelationExtractor(max_gleanings=0)  # isolates retry behavior from gleaning's own extra call

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
        extractor = PassageRelationExtractor(max_gleanings=0)  # isolates retry behavior from gleaning's own extra call

        bad_response = _response({
            "triples": [],
            "topic_title": "Some Garbled Nonexistent Title",
            "entity_mentions": [],
        })

        # max_attempts = 1 + empty-response retries (default 1) + transient-
        # error retries (default 2) = 4 -- one bad_response per attempt.
        with patch("litellm.completion", side_effect=[bad_response] * 4) as mock_completion:
            graph = extractor.extract_local_graph("Who was born May 25, 1994?", CONTEXT)

        self.assertEqual(mock_completion.call_count, 4)
        # Falls back to the first context title, same degrade-gracefully
        # behavior as an outright parse failure.
        self.assertEqual(graph.topic_title, "Aly Raisman")

    def test_self_referential_triple_dropped_even_after_retries_exhausted(self):
        """A self-referential triple triggers retries (test above), but if
        every attempt still produces one, the final graph must never
        materialize it -- not just "prefer a better attempt if we get one"."""
        extractor = PassageRelationExtractor()

        self_referential_response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "father",
                "to_value": "Aly Raisman", "is_literal": False,
                "evidence": "she was the daughter of Some Distractor",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=self_referential_response):
            graph = extractor.extract_local_graph("Who is Aly Raisman's father?", CONTEXT)

        self.assertEqual(graph.triples, [])

    def test_good_first_response_does_not_retry(self):
        extractor = PassageRelationExtractor(max_gleanings=0)  # isolates retry behavior from gleaning's own extra call

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

    def test_approved_node_types_are_included_in_prompt(self):
        """approved_node_types nudges the model to reuse an established type
        vocabulary instead of inventing near-duplicates across independent,
        stateless calls for the same tenant (see module docstring)."""
        extractor = PassageRelationExtractor()

        good_response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
                "is_literal": True, "evidence": "born May 25, 1994",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=good_response) as mock_completion:
            extractor.extract_local_graph(
                "Who was born May 25, 1994?", CONTEXT, approved_node_types=["Person", "Organization"],
            )

        user_message = mock_completion.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Organization", user_message)
        self.assertIn("Person", user_message)

    def test_literal_object_named_entity_type_is_preserved(self):
        """A literal span that itself names a real entity (a composer's
        name mentioned as a literal fact, not one of the given paragraph
        titles) should keep its LLM-classified type rather than collapsing
        to "Value" -- see passage_relation_extraction.py rule 9."""
        extractor = PassageRelationExtractor()

        response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "composed_by",
                "to_value": "Ludwig van Beethoven", "is_literal": True,
                "to_type": "Person", "evidence": "composed by Ludwig van Beethoven",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(graph.triples[0].to_type, "Person")

    def test_literal_object_without_type_falls_back_to_value(self):
        """A scalar literal (a date, here) with no natural entity type still
        defaults to "Value" when the LLM leaves to_type blank."""
        extractor = PassageRelationExtractor()

        response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
                "is_literal": True, "to_type": "", "evidence": "born May 25, 1994",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(graph.triples[0].to_type, "Value")

    def test_no_approved_node_types_omits_suffix(self):
        """Without approved_node_types, the prompt is unchanged from before
        this feature existed -- no empty/misleading type-list section."""
        extractor = PassageRelationExtractor()

        good_response = _response({
            "triples": [],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=good_response) as mock_completion:
            extractor.extract_local_graph("Who was born May 25, 1994?", CONTEXT)

        user_message = mock_completion.call_args.kwargs["messages"][1]["content"]
        self.assertNotIn("already has these established entity types", user_message)


class GleaningTest(unittest.TestCase):
    """Gleaning follow-up turns after a successful main extraction -- see
    GLEANING_CONTINUE_PROMPT's docstring for why this exists (borrowed from
    GraphRAG's own extraction pipeline)."""

    MAIN_RESPONSE = _response({
        "triples": [{
            "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
            "is_literal": True, "evidence": "born May 25, 1994", "from_type": "Person", "to_type": "",
        }],
        "topic_title": "Aly Raisman",
        "entity_mentions": [],
    })

    def _gleaning_response(self, triples: list[dict]) -> MagicMock:
        return _response({"triples": triples})

    def _yn_response(self, answer: str) -> MagicMock:
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = answer
        resp.choices[0].message.reasoning_content = None
        resp.choices[0].message.reasoning = None
        return resp

    def test_gleaning_adds_missed_triples(self):
        extractor = PassageRelationExtractor(max_gleanings=1)
        gleaned = self._gleaning_response([{
            "from_title": "Aly Raisman", "relation": "nationality", "to_value": "American",
            "is_literal": True, "evidence": "American gymnast", "from_type": "Person", "to_type": "",
        }])

        with patch("litellm.completion", side_effect=[self.MAIN_RESPONSE, gleaned]) as mock_completion:
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(mock_completion.call_count, 2)  # main + 1 gleaning round, no loop-check (max_gleanings=1)
        relations = {t.relation for t in graph.triples}
        self.assertEqual(relations, {"born_on", "nationality"})

    def test_gleaning_disabled_when_max_gleanings_zero(self):
        extractor = PassageRelationExtractor(max_gleanings=0)

        with patch("litellm.completion", return_value=self.MAIN_RESPONSE) as mock_completion:
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(mock_completion.call_count, 1)
        self.assertEqual({t.relation for t in graph.triples}, {"born_on"})

    def test_gleaning_dedupes_triple_already_extracted(self):
        """The model re-listing a triple it already extracted in the main
        turn (rather than something genuinely new) must not duplicate it."""
        extractor = PassageRelationExtractor(max_gleanings=1)
        repeated = self._gleaning_response([{
            "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
            "is_literal": True, "evidence": "born May 25, 1994", "from_type": "Person", "to_type": "",
        }])

        with patch("litellm.completion", side_effect=[self.MAIN_RESPONSE, repeated]):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(len(graph.triples), 1)

    def test_gleaning_stops_on_empty_response(self):
        extractor = PassageRelationExtractor(max_gleanings=2)
        empty = self._gleaning_response([])

        with patch("litellm.completion", side_effect=[self.MAIN_RESPONSE, empty]) as mock_completion:
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        # Stops immediately on an empty gleaning response -- no loop-check
        # call wasted asking whether to continue when the model already
        # signaled nothing more via an empty triples list.
        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(len(graph.triples), 1)

    def test_gleaning_loop_continues_across_multiple_rounds(self):
        extractor = PassageRelationExtractor(max_gleanings=2)
        glean1 = self._gleaning_response([{
            "from_title": "Aly Raisman", "relation": "nationality", "to_value": "American",
            "is_literal": True, "evidence": "American gymnast", "from_type": "Person", "to_type": "",
        }])
        glean2 = self._gleaning_response([{
            "from_title": "Aly Raisman", "relation": "occupation", "to_value": "gymnast",
            "is_literal": True, "evidence": "American gymnast", "from_type": "Person", "to_type": "",
        }])

        with patch(
            "litellm.completion", side_effect=[self.MAIN_RESPONSE, glean1, self._yn_response("Y"), glean2],
        ) as mock_completion:
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(mock_completion.call_count, 4)  # main + glean1 + loop-check + glean2 (last round, no check)
        self.assertEqual({t.relation for t in graph.triples}, {"born_on", "nationality", "occupation"})

    def test_gleaning_stops_when_loop_answers_no(self):
        extractor = PassageRelationExtractor(max_gleanings=2)
        glean1 = self._gleaning_response([{
            "from_title": "Aly Raisman", "relation": "nationality", "to_value": "American",
            "is_literal": True, "evidence": "American gymnast", "from_type": "Person", "to_type": "",
        }])

        with patch(
            "litellm.completion", side_effect=[self.MAIN_RESPONSE, glean1, self._yn_response("N")],
        ) as mock_completion:
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(mock_completion.call_count, 3)
        self.assertEqual({t.relation for t in graph.triples}, {"born_on", "nationality"})

    def test_gleaning_failure_does_not_crash_main_result(self):
        extractor = PassageRelationExtractor(max_gleanings=1)

        with patch("litellm.completion", side_effect=[self.MAIN_RESPONSE, Exception("boom")]):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual({t.relation for t in graph.triples}, {"born_on"})
        self.assertFalse(graph.used_fallback)


class MentionedEntitiesTest(unittest.TestCase):
    """mentioned_entities: entities mentioned anywhere in the passages get
    their own type + description regardless of whether they're a triple's
    subject/object -- see MentionedEntity's docstring (borrowed from
    GraphRAG's own extraction pipeline)."""

    def test_mentioned_entity_not_covered_by_any_triple_is_still_captured(self):
        """The core case this exists for: an entity named only in passing
        (never a triple's from_title/to_value) still gets captured, with no
        closed-world check against the given titles -- unlike triple
        subjects, it's explicitly allowed to not be one of them."""
        extractor = PassageRelationExtractor(max_gleanings=0)
        response = _response({
            "triples": [{
                "from_title": "Aly Raisman", "relation": "born_on", "to_value": "May 25, 1994",
                "is_literal": True, "evidence": "born May 25, 1994", "from_type": "Person", "to_type": "",
            }],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
            "mentioned_entities": [{
                "name": "Some Coach", "type": "Person",
                "description": "A coach mentioned in passing as having trained Aly Raisman.",
            }],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(len(graph.mentioned_entities), 1)
        entity = graph.mentioned_entities[0]
        self.assertEqual(entity.name, "Some Coach")
        self.assertEqual(entity.entity_type, "Person")
        self.assertEqual(entity.description, "A coach mentioned in passing as having trained Aly Raisman.")

    def test_absent_mentioned_entities_key_is_backward_compatible(self):
        """A response without "mentioned_entities" at all (e.g. from before
        this feature existed, or any fixture that omits it) is still a
        valid, fully-accepted response -- not part of the strict parse gate."""
        extractor = PassageRelationExtractor(max_gleanings=0)
        response = _response({
            "triples": [],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(graph.mentioned_entities, [])
        self.assertFalse(graph.used_fallback)

    def test_duplicate_names_are_deduped(self):
        extractor = PassageRelationExtractor(max_gleanings=0)
        response = _response({
            "triples": [],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
            "mentioned_entities": [
                {"name": "Some Coach", "type": "Person", "description": "First mention."},
                {"name": "Some Coach", "type": "Person", "description": "Repeated mention."},
            ],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(len(graph.mentioned_entities), 1)

    def test_entity_with_no_name_is_dropped(self):
        extractor = PassageRelationExtractor(max_gleanings=0)
        response = _response({
            "triples": [],
            "topic_title": "Aly Raisman",
            "entity_mentions": [],
            "mentioned_entities": [{"name": "", "type": "Person", "description": "No name given."}],
        })

        with patch("litellm.completion", return_value=response):
            graph = extractor.extract_local_graph("irrelevant question", CONTEXT)

        self.assertEqual(graph.mentioned_entities, [])


if __name__ == "__main__":
    unittest.main()
