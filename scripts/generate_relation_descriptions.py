#!/usr/bin/env python3
"""Generate natural language descriptions from Freebase relation names.

Freebase relation names follow a dotted convention like
``people.person.nationality`` or ``film.film.starring``. This module converts
them into readable descriptions (e.g. "the nationality of a person") so that
the keyword-matching planner can bridge the semantic gap between natural
language questions and relation names without requiring an LLM.

The conversion is fully rule-based and deterministic — no LLM dependency.
"""

from __future__ import annotations

import re
from typing import Any


# Known domain-to-natural-language mappings for common Freebase domains.
DOMAIN_NL: dict[str, str] = {
    "people": "person",
    "film": "film",
    "music": "music",
    "book": "book",
    "location": "location",
    "organization": "organization",
    "business": "business",
    "government": "government",
    "education": "education",
    "award": "award",
    "sports": "sport",
    "aviation": "aviation",
    "broadcast": "broadcast",
    "military": "military",
    "medicine": "medicine",
    "transportation": "transportation",
    "travel": "travel",
    "architecture": "architecture",
    "biology": "biology",
    "language": "language",
    "tv": "TV show",
    "internet": "website",
    "computer": "computer",
    "religion": "religion",
    "chemistry": "chemistry",
    "physics": "physics",
    "astronomy": "astronomy",
    "metropolitan_transit": "transit",
    "fictional_universe": "fictional universe",
    "media_common": "media",
    "operating_system": "operating system",
    "ice_hockey": "ice hockey",
    "amusement_parks": "amusement park",
    "time": "time",
}

# Common relation suffix patterns and their natural language templates.
# The key is the last segment of the Freebase relation name; the value is a
# template where {domain} is replaced by the natural-language domain word.
RELATION_SUFFIX_TEMPLATES: dict[str, str] = {
    "nationality": "the nationality of a {domain}",
    "nationalities": "the nationalities of a {domain}",
    "profession": "the profession of a {domain}",
    "professions": "the professions of a {domain}",
    "gender": "the gender of a {domain}",
    "date_of_birth": "the date of birth of a {domain}",
    "date_of_death": "the date of death of a {domain}",
    "place_of_birth": "the place of birth of a {domain}",
    "place_of_death": "the place of death of a {domain}",
    "age": "the age of a {domain}",
    "height": "the height of a {domain}",
    "weight": "the weight of a {domain}",
    "name": "the name of a {domain}",
    "full_name": "the full name of a {domain}",
    "display_name": "the display name of a {domain}",
    "starring": "the actors starring in a {domain}",
    "directed_by": "the director of a {domain}",
    "produced_by": "the producer of a {domain}",
    "written_by": "the writer of a {domain}",
    "release_date": "the release date of a {domain}",
    "runtime": "the runtime of a {domain}",
    "genre": "the genre of a {domain}",
    "genres": "the genres of a {domain}",
    "language": "the language of a {domain}",
    "languages": "the languages of a {domain}",
    "country": "the country of a {domain}",
    "countries": "the countries of a {domain}",
    "location": "the location of a {domain}",
    "locations": "the locations of a {domain}",
    "address": "the address of a {domain}",
    "parent_company": "the parent company of a {domain}",
    "subsidiary": "the subsidiaries of a {domain}",
    "subsidiaries": "the subsidiaries of a {domain}",
    "founder": "the founder of a {domain}",
    "founders": "the founders of a {domain}",
    "founded": "the founding date of a {domain}",
    "founded_date": "the founding date of a {domain}",
    "founded_by": "the founders of a {domain}",
    "headquarters": "the headquarters of a {domain}",
    "industry": "the industry of a {domain}",
    "employees": "the number of employees of a {domain}",
    "revenue": "the revenue of a {domain}",
    "website": "the website of a {domain}",
    "member_of": "the membership of a {domain}",
    "members": "the members of a {domain}",
    "spouse": "the spouse of a {domain}",
    "spouses": "the spouses of a {domain}",
    "children": "the children of a {domain}",
    "parents": "the parents of a {domain}",
    "sibling": "the sibling of a {domain}",
    "siblings": "the siblings of a {domain}",
    "relative": "the relative of a {domain}",
    "relatives": "the relatives of a {domain}",
    "education": "the education of a {domain}",
    "alma_mater": "the alma mater of a {domain}",
    "employer": "the employer of a {domain}",
    "employment": "the employment of a {domain}",
    "award": "the award of a {domain}",
    "awards": "the awards of a {domain}",
    "nominated_for": "the nominations of a {domain}",
    "winner": "the winner of a {domain}",
    "winners": "the winners of a {domain}",
    "candidate": "the candidate of a {domain}",
    "party": "the political party of a {domain}",
    "religion": "the religion of a {domain}",
    "religions": "the religions of a {domain}",
    "ethnicity": "the ethnicity of a {domain}",
    "cause_of_death": "the cause of death of a {domain}",
    "description": "the description of a {domain}",
    "overview": "the overview of a {domain}",
    "type": "the type of a {domain}",
    "types": "the types of a {domain}",
    "label": "the label of a {domain}",
    "labels": "the labels of a {domain}",
    "publisher": "the publisher of a {domain}",
    "author": "the author of a {domain}",
    "authors": "the authors of a {domain}",
    "isbn": "the ISBN of a {domain}",
    "pages": "the page count of a {domain}",
    "edition": "the edition of a {domain}",
    "publication_date": "the publication date of a {domain}",
    "artist": "the artist of a {domain}",
    "album": "the album of a {domain}",
    "track": "the track of a {domain}",
    "tracks": "the tracks of a {domain}",
    "band": "the band of a {domain}",
    "composer": "the composer of a {domain}",
    "lyrics": "the lyrics of a {domain}",
    "record_label": "the record label of a {domain}",
    "capital": "the capital of a {domain}",
    "currency": "the currency of a {domain}",
    "population": "the population of a {domain}",
    "area": "the area of a {domain}",
    "gdp": "the GDP of a {domain}",
    "imports": "the imports of a {domain}",
    "exports": "the exports of a {domain}",
    "neighbors": "the neighboring {domain}s",
    "bordering_countries": "the bordering countries of a {domain}",
    "contains": "the areas contained in a {domain}",
    "contained_by": "the container of a {domain}",
    "near": "the nearby {domain}s",
    "adjoining": "the adjoining {domain}s",
    "sport": "the sport of a {domain}",
    "team": "the team of a {domain}",
    "teams": "the teams of a {domain}",
    "league": "the league of a {domain}",
    "championship": "the championship of a {domain}",
    "championships": "the championships of a {domain}",
    "season": "the season of a {domain}",
    "position": "the position of a {domain}",
    "draft": "the draft of a {domain}",
    "number": "the number of a {domain}",
    "owner": "the owner of a {domain}",
    "owners": "the owners of a {domain}",
    "operator": "the operator of a {domain}",
    "manufacturer": "the manufacturer of a {domain}",
    "model": "the model of a {domain}",
    "code": "the code of a {domain}",
    "icao": "the ICAO code of a {domain}",
    "iata": "the IATA code of a {domain}",
    "callsign": "the callsign of a {domain}",
    "hub": "the hub of a {domain}",
    "hubs": "the hubs of a {domain}",
    "destinations": "the destinations of a {domain}",
    "fleet": "the fleet of a {domain}",
    "airport": "the airport of a {domain}",
    "airports": "the airports of a {domain}",
}


def _domain_to_nl(domain: str) -> str:
    """Convert a Freebase domain segment to a natural language word."""
    if domain in DOMAIN_NL:
        return DOMAIN_NL[domain]
    return domain.replace("_", " ")


def _suffix_to_nl(suffix: str, domain_nl: str) -> str:
    """Convert a relation suffix to a natural language description."""
    if suffix in RELATION_SUFFIX_TEMPLATES:
        return RELATION_SUFFIX_TEMPLATES[suffix].format(domain=domain_nl)
    # Fallback: convert underscores to spaces and prepend "the"
    spaced = suffix.replace("_", " ")
    return f"the {spaced} of a {domain_nl}"


def generate_relation_description(relation: str) -> str:
    """Generate a natural language description from a Freebase relation name.

    Examples:
        >>> generate_relation_description("people.person.nationality")
        'the nationality of a person'
        >>> generate_relation_description("film.film.directed_by")
        'the director of a film'
        >>> generate_relation_description("location.location.contains")
        'the areas contained in a location'
    """
    if not relation:
        return ""

    parts = relation.split(".")
    if len(parts) == 1:
        return parts[0].replace("_", " ")

    domain = parts[0]
    suffix = parts[-1]
    domain_nl = _domain_to_nl(domain)

    return _suffix_to_nl(suffix, domain_nl)


def generate_descriptions_for_link_config(
    link_config: list[dict[str, Any]],
    relations: list[str] | None = None,
) -> dict[str, str]:
    """Generate descriptions for all links in a link_config.

    If ``relations`` is provided (mapping link keys to Freebase relation names),
    use the full relation name for better accuracy. Otherwise, infer the
    relation from the link key's last segment.
    """
    descriptions: dict[str, str] = {}
    relation_map: dict[str, str] = {}
    if relations:
        for r in relations:
            r_norm = r.replace(".", "_")
            relation_map[r_norm] = r

    for lc in link_config:
        link_key = lc["link"]
        r_norm = link_key.rsplit(":", 1)[-1] if ":" in link_key else link_key
        full_relation = relation_map.get(r_norm)
        if full_relation:
            descriptions[link_key] = generate_relation_description(full_relation)
        else:
            # Infer domain from the 'from' type
            from_type = lc.get("from", "entity")
            domain_nl = _domain_to_nl(from_type)
            descriptions[link_key] = _suffix_to_nl(r_norm, domain_nl)

    return descriptions


if __name__ == "__main__":
    import sys

    test_relations = [
        "people.person.nationality",
        "film.film.directed_by",
        "film.film.starring",
        "location.location.contains",
        "organization.organization.founded_by",
        "music.recording.artist",
        "book.written_work.author",
        "sports.sports_team.championships",
    ]
    for r in test_relations:
        desc = generate_relation_description(r)
        print(f"{r} -> {desc}")
