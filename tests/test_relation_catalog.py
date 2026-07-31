#!/usr/bin/env python3
"""RelationCatalog: governed relation-type normalization.

Separates WHICH relations exist (this catalog -- governance, meant to live
in onto's Postgres metadata store) from HOW edge data is physically stored
(Nebula's single EDGE type, untouched). Deterministic tests use the local
JSON-file mode and a mocked LLM semantic-match call -- no live Postgres or
litellm needed.

Run: python -m unittest tests.test_relation_catalog
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from relation_catalog import RelationCatalog  # noqa: E402


def _match_response(canonical_match: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].finish_reason = "stop"
    resp.choices[0].message.content = json.dumps({"canonical_match": canonical_match})
    resp.choices[0].message.reasoning_content = None
    resp.choices[0].message.reasoning = None
    return resp


class CheapMatchTest(unittest.TestCase):
    def test_exact_match_after_case_and_punctuation_folding(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        self.assertEqual(catalog._cheap_match("Founded In"), "founded_in")
        self.assertEqual(catalog._cheap_match("founded-in"), "founded_in")

    def test_matches_known_alias(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": ["established_in"]}})
        self.assertEqual(catalog._cheap_match("Established In"), "founded_in")

    def test_no_match_returns_none(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        self.assertIsNone(catalog._cheap_match("nationality"))


class NormalizeTest(unittest.TestCase):
    def test_first_ever_relation_registers_without_llm_call(self):
        """An empty catalog has nothing to semantically compare against --
        must not call the LLM at all for the very first relation."""
        catalog = RelationCatalog(entries={})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("founded_in", evidence="Founded in 1590.")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "founded_in")
        self.assertIn("founded_in", catalog.entries)
        self.assertEqual(catalog.new_relations_this_session, 1)

    def test_cheap_match_short_circuits_llm_call(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("Founded_In")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "founded_in")

    def test_semantic_match_merges_into_existing_canonical(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "when established", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("founded_in")):
            canonical = catalog.normalize("established_in", evidence="Established in 1590.")

        self.assertEqual(canonical, "founded_in")
        self.assertIn("established_in", catalog.entries["founded_in"]["aliases"])
        self.assertEqual(catalog.merged_this_session, 1)
        self.assertNotIn("established_in", catalog.entries)

    def test_no_semantic_match_registers_as_new_canonical(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("")):
            canonical = catalog.normalize("nationality", evidence="Is French.")

        self.assertEqual(canonical, "nationality")
        self.assertIn("nationality", catalog.entries)
        self.assertEqual(catalog.new_relations_this_session, 1)
        self.assertEqual(catalog.merged_this_session, 0)

    def test_llm_returning_unknown_name_is_treated_as_no_match(self):
        """A hallucinated canonical_match that isn't actually in the
        catalog must not be trusted -- falls back to registering new."""
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        with patch("litellm.completion", return_value=_match_response("not_a_real_entry")):
            canonical = catalog.normalize("nationality")

        self.assertEqual(canonical, "nationality")
        self.assertIn("nationality", catalog.entries)

    def test_llm_call_failure_degrades_to_registering_new_not_crash(self):
        catalog = RelationCatalog(entries={"founded_in": {"description": "", "aliases": []}})
        with patch("litellm.completion", side_effect=Exception("connection refused")):
            canonical = catalog.normalize("nationality")

        self.assertEqual(canonical, "nationality")
        self.assertIn("nationality", catalog.entries)

    def test_empty_relation_name_passthrough(self):
        catalog = RelationCatalog(entries={})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.normalize("")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "")
        self.assertEqual(catalog.entries, {})


class RegisterIdentityTest(unittest.TestCase):
    def test_new_relation_registers_as_its_own_canonical_no_llm_call(self):
        catalog = RelationCatalog(entries={"people.person.nationality": {"description": "", "aliases": []}})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.register_identity("film.film.starring", description="A film's cast.")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "film.film.starring")
        self.assertIn("film.film.starring", catalog.entries)

    def test_repeated_relation_collapses_to_existing_entry_no_llm_call(self):
        catalog = RelationCatalog(entries={"people.person.nationality": {"description": "", "aliases": []}})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.register_identity("people.person.nationality")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "people.person.nationality")
        self.assertEqual(len(catalog.entries), 1)

    def test_empty_relation_name_passthrough(self):
        catalog = RelationCatalog(entries={})
        with patch("litellm.completion") as mock_completion:
            canonical = catalog.register_identity("")
        mock_completion.assert_not_called()
        self.assertEqual(canonical, "")
        self.assertEqual(catalog.entries, {})


class JsonPersistenceTest(unittest.TestCase):
    def test_save_and_load_round_trip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "catalog.json"
            catalog = RelationCatalog(entries={"founded_in": {"description": "d", "aliases": ["established_in"]}})
            catalog.path = path
            catalog.save()

            reloaded = RelationCatalog.load(path)
            self.assertEqual(reloaded.entries, catalog.entries)


class AsLinkConfigTest(unittest.TestCase):
    def test_shape_matches_backend_agnostic_link_config(self):
        catalog = RelationCatalog(entries={
            "founded_in": {"description": "when founded", "aliases": ["established_in"]},
        })
        config = catalog.as_link_config()
        self.assertEqual(config, [{"link": "founded_in", "description": "when founded", "from": "*", "to": "*"}])


class LivePostgresSmokeTest(unittest.TestCase):
    """Skipped (not failed) if the local Postgres metadata store isn't
    reachable -- this repo's Postgres container is optional local infra,
    not a CI dependency (same pattern as test_hotpotqa_nebula_benchmark's
    LiveNebulaSmokeTest)."""

    def test_save_and_reload_round_trip_against_real_postgres(self):
        try:
            from sqlalchemy import create_engine, text
        except ImportError:
            self.skipTest("sqlalchemy not installed")

        sys.path.insert(0, str(ROOT / "agents"))
        from tenant_registry import default_metadata_db_url

        db_url = default_metadata_db_url()
        try:
            create_engine(db_url).connect().close()
        except Exception as exc:
            self.skipTest(f"Postgres not reachable: {exc}")

        scope = "relation_catalog_unittest"
        try:
            catalog = RelationCatalog.load_from_postgres(db_url, scope=scope)
            catalog.entries["founded_in"] = {"description": "test entry", "aliases": ["established_in"]}
            catalog.save()

            reloaded = RelationCatalog.load_from_postgres(db_url, scope=scope)
            self.assertEqual(
                reloaded.entries["founded_in"],
                {"description": "test entry", "aliases": ["established_in"]},
            )
        finally:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM relation_catalog WHERE scope = :scope"), {"scope": scope})
                conn.commit()


if __name__ == "__main__":
    unittest.main()
