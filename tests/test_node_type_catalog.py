#!/usr/bin/env python3
"""NodeTypeCatalog: governed node-TYPE normalization -- the "ontology
mapping" pipeline stage.

Mirrors tests/test_relation_catalog.py's structure exactly (same shared
matching algorithm, via type_catalog_matching.py) but for entity types
("Human" -> "Person") instead of relation names. Deterministic tests use
the local JSON-file mode and a mocked LLM semantic-match call -- no live
Postgres or litellm needed.

Run: python -m unittest tests.test_node_type_catalog
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from node_type_catalog import NodeTypeCatalog


def _match_response(canonical_match: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = json.dumps({"canonical_match": canonical_match})
    resp.choices[0].message.reasoning_content = None
    resp.choices[0].message.reasoning = None
    return resp


def _parent_response(suggested_parent: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = json.dumps({"suggested_parent": suggested_parent})
    resp.choices[0].message.reasoning_content = None
    resp.choices[0].message.reasoning = None
    return resp


class CheapMatchTest(unittest.TestCase):
    def test_exact_match_after_case_and_punctuation_folding(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        self.assertEqual(catalog._cheap_match("person"), "Person")
        self.assertEqual(catalog._cheap_match("PERSON"), "Person")

    def test_matches_known_alias(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": ["Human"]}})
        self.assertEqual(catalog._cheap_match("human"), "Person")

    def test_no_match_returns_none(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        self.assertIsNone(catalog._cheap_match("Organization"))


class NormalizeTest(unittest.TestCase):
    def test_first_ever_type_registers_without_llm_call(self):
        """An empty catalog has nothing to semantically compare against --
        must not call the LLM at all for the very first type."""
        catalog = NodeTypeCatalog(entries={})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("Person", evidence="A human individual.")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "Person")
        self.assertIn("Person", catalog.entries)
        self.assertEqual(catalog.new_types_this_session, 1)

    def test_cheap_match_short_circuits_llm_call(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("person")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "Person")

    def test_semantic_match_merges_into_existing_canonical(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "a human individual", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("Person")):
            canonical = catalog.normalize("Human", evidence="A person born in 1990.")

        self.assertEqual(canonical, "Person")
        self.assertIn("Human", catalog.entries["Person"]["aliases"])
        self.assertEqual(catalog.merged_this_session, 1)
        self.assertNotIn("Human", catalog.entries)

    def test_no_semantic_match_registers_as_new_canonical(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("")):
            canonical = catalog.normalize("Organization", evidence="A company.")

        self.assertEqual(canonical, "Organization")
        self.assertIn("Organization", catalog.entries)
        self.assertEqual(catalog.new_types_this_session, 1)
        self.assertEqual(catalog.merged_this_session, 0)

    def test_llm_returning_unknown_name_is_treated_as_no_match(self):
        """A hallucinated canonical_match that isn't actually in the
        catalog must not be trusted -- falls back to registering new."""
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("NotARealType")):
            canonical = catalog.normalize("Organization")

        self.assertEqual(canonical, "Organization")
        self.assertIn("Organization", catalog.entries)

    def test_llm_call_failure_degrades_to_registering_new_not_crash(self):
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        with patch("litellm.completion", side_effect=Exception("connection refused")):
            canonical = catalog.normalize("Organization")

        self.assertEqual(canonical, "Organization")
        self.assertIn("Organization", catalog.entries)

    def test_empty_type_name_passthrough(self):
        catalog = NodeTypeCatalog(entries={})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "")
        self.assertEqual(catalog.entries, {})


class SubclassSuggestionTest(unittest.TestCase):
    """normalize()'s optional second question: is a genuinely-new type a
    more specific kind of one of the tenant's already-approved types? Feeds
    graph_instance_repository.reasoning_entity_config's subclass_of walk."""

    def test_no_approved_node_types_skips_parent_suggestion_call(self):
        """Without approved_node_types, only the one semantic-match call
        happens -- no second LLM call, no "suggested_parent" key added."""
        catalog = NodeTypeCatalog(entries={"Person": {"description": "", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("")) as mock_completion:
            canonical = catalog.normalize("Dog", evidence="A domesticated animal.")

        self.assertEqual(canonical, "Dog")
        self.assertEqual(mock_completion.call_count, 1)
        self.assertIsNone(catalog.entries["Dog"]["suggested_parent"])

    def test_new_subtype_gets_parent_suggestion(self):
        catalog = NodeTypeCatalog(entries={"Dog": {"description": "a domesticated canine", "aliases": []}})
        with patch(
            "litellm.completion", side_effect=[_match_response(""), _parent_response("Dog")],
        ) as mock_completion:
            canonical = catalog.normalize(
                "GuideDog", evidence="A dog trained to assist blind people.", approved_node_types=["Dog"],
            )

        self.assertEqual(canonical, "GuideDog")
        self.assertEqual(mock_completion.call_count, 2)
        self.assertEqual(catalog.entries["GuideDog"]["suggested_parent"], "Dog")

    def test_llm_suggesting_unapproved_parent_is_ignored(self):
        """A hallucinated suggested_parent that isn't actually in
        approved_node_types must not be trusted."""
        catalog = NodeTypeCatalog(entries={"Dog": {"description": "", "aliases": []}})
        with patch(
            "litellm.completion", side_effect=[_match_response(""), _parent_response("NotApproved")],
        ):
            catalog.normalize("GuideDog", approved_node_types=["Dog"])

        self.assertIsNone(catalog.entries["GuideDog"]["suggested_parent"])

    def test_parent_suggestion_llm_failure_degrades_to_none(self):
        catalog = NodeTypeCatalog(entries={"Dog": {"description": "", "aliases": []}})
        with patch(
            "litellm.completion", side_effect=[_match_response(""), Exception("connection refused")],
        ):
            canonical = catalog.normalize("GuideDog", approved_node_types=["Dog"])

        self.assertEqual(canonical, "GuideDog")
        self.assertIsNone(catalog.entries["GuideDog"]["suggested_parent"])

    def test_empty_string_suggested_parent_means_no_parent(self):
        catalog = NodeTypeCatalog(entries={"Dog": {"description": "", "aliases": []}})
        with patch(
            "litellm.completion", side_effect=[_match_response(""), _parent_response("")],
        ):
            catalog.normalize("Cat", approved_node_types=["Dog"])

        self.assertIsNone(catalog.entries["Cat"]["suggested_parent"])


class JsonPersistenceTest(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "catalog.json"
            catalog = NodeTypeCatalog(entries={"Person": {"description": "d", "aliases": ["Human"]}})
            catalog.path = path
            catalog.save()

            reloaded = NodeTypeCatalog.load(path)
            self.assertEqual(reloaded.entries, catalog.entries)


class LivePostgresSmokeTest(unittest.TestCase):
    """Skipped (not failed) if the local Postgres metadata store isn't
    reachable -- same pattern as test_relation_catalog's own
    LivePostgresSmokeTest."""

    def test_save_and_reload_round_trip_against_real_postgres(self):
        try:
            from sqlalchemy import create_engine, text
        except ImportError:
            self.skipTest("sqlalchemy not installed")

        from tenant_registry import default_metadata_db_url

        db_url = default_metadata_db_url()
        try:
            create_engine(db_url).connect().close()
        except Exception as exc:
            self.skipTest(f"Postgres not reachable: {exc}")

        scope = "node_type_catalog_unittest"
        try:
            catalog = NodeTypeCatalog.load_from_postgres(db_url, scope=scope)
            catalog.entries["Person"] = {"description": "test entry", "aliases": ["Human"]}
            catalog.save()

            reloaded = NodeTypeCatalog.load_from_postgres(db_url, scope=scope)
            self.assertEqual(
                reloaded.entries["Person"],
                {"description": "test entry", "aliases": ["Human"]},
            )
        finally:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM node_type_catalog WHERE scope = :scope"), {"scope": scope})
                conn.commit()


if __name__ == "__main__":
    unittest.main()
