import unittest
import sys
from pathlib import Path

# Add scripts dir to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_relation_descriptions import (
    generate_relation_description,
    generate_descriptions_for_link_config,
    DOMAIN_NL,
    RELATION_SUFFIX_TEMPLATES,
)


class RelationDescriptionGeneratorTest(unittest.TestCase):
    """Tests for rule-based Freebase relation name to natural language description conversion."""

    def test_people_person_nationality(self):
        desc = generate_relation_description("people.person.nationality")
        self.assertEqual(desc, "the nationality of a person")

    def test_film_film_directed_by(self):
        desc = generate_relation_description("film.film.directed_by")
        self.assertEqual(desc, "the director of a film")

    def test_film_film_starring(self):
        desc = generate_relation_description("film.film.starring")
        self.assertEqual(desc, "the actors starring in a film")

    def test_location_location_contains(self):
        desc = generate_relation_description("location.location.contains")
        self.assertEqual(desc, "the areas contained in a location")

    def test_organization_founded_by(self):
        desc = generate_relation_description("organization.organization.founded_by")
        self.assertEqual(desc, "the founders of a organization")

    def test_unknown_suffix_falls_back_to_generic_pattern(self):
        desc = generate_relation_description("people.person.some_unknown_field")
        self.assertIn("some unknown field", desc)
        self.assertIn("person", desc)

    def test_single_segment_relation(self):
        desc = generate_relation_description("nationality")
        self.assertEqual(desc, "nationality")

    def test_empty_relation_returns_empty(self):
        self.assertEqual(generate_relation_description(""), "")

    def test_underscore_domain_is_converted(self):
        desc = generate_relation_description("ice_hockey.player.position")
        self.assertIn("ice hockey", desc)

    def test_generate_descriptions_for_link_config(self):
        link_config = [
            {"link": "person:n:m:nationality", "from": "person", "to": "entity"},
            {"link": "film:n:m:directed_by", "from": "film", "to": "entity"},
        ]
        relations = ["people.person.nationality", "film.film.directed_by"]

        desc = generate_descriptions_for_link_config(link_config, relations)

        self.assertIn("the nationality of a person", desc["person:n:m:nationality"])
        self.assertIn("the director of a film", desc["film:n:m:directed_by"])

    def test_generate_descriptions_without_relations_infers_from_link_key(self):
        link_config = [
            {"link": "person:n:m:nationality", "from": "person", "to": "entity"},
        ]
        desc = generate_descriptions_for_link_config(link_config)
        self.assertIn("nationality", desc["person:n:m:nationality"])
        self.assertIn("person", desc["person:n:m:nationality"])

    def test_domain_nl_covers_common_freebase_domains(self):
        for domain in ["people", "film", "music", "book", "location", "organization", "sports"]:
            self.assertIn(domain, DOMAIN_NL)

    def test_suffix_templates_cover_key_relation_patterns(self):
        for suffix in ["nationality", "directed_by", "starring", "contains", "founded_by", "author", "spouse"]:
            self.assertIn(suffix, RELATION_SUFFIX_TEMPLATES)


if __name__ == "__main__":
    unittest.main()
