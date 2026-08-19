"""Extract a closed-world local knowledge graph from one question's candidate passages.

Dataset-agnostic: any source that hands over a question plus a fixed list of
candidate ``(title, sentences)`` passages can use this -- proven by reuse,
unchanged, across HotpotQA (``import_hotpotqa_nebula_tenant.py``) and
2WikiMultihopQA (``twowikimultihopqa_frozen_sample.py``) benchmarks this
session. Unlike WebQSP/Mintaka (whose entities and typed relations come from
a real external KG -- Freebase/Wikidata), these datasets ship only raw
Wikipedia paragraphs. To build a real graph tenant without any live external
fetch, this module asks an LLM to read a *single* question's own passages and
extract typed (subject, relation, object) triples restricted to a closed
world: the subject/object of every triple must be either one of the
question's own passage titles, or a literal span copied from the text (a
date, name, nationality, yes/no, etc.). No entity linking to Wikidata/
Wikipedia happens anywhere in this module -- the only network call is the
LLM completion itself, same category already used throughout this project.

Entity-type governance: callers may pass ``approved_node_types`` (the
tenant's already-established type vocabulary, e.g. from
``agents/graph_ontology_registry.py``) into ``extract_local_graph`` --
included in the prompt so the model prefers reusing an existing type name
over inventing a near-duplicate (e.g. "Person" vs "Human") when extracting
across many independent, stateless calls for the same tenant. This module
does not itself enforce/gate types against that list (no draft/approved
lifecycle here) -- it only nudges consistency; the caller (e.g. the batch
importer) still owns the actual registration policy.

Usage:
    from passage_relation_extraction import PassageRelationExtractor
    extractor = PassageRelationExtractor()
    graph = extractor.extract_local_graph(question, context)
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any

from llm_planner import LLMPlanner

logger = logging.getLogger("PassageRelationExtractor")


@dataclass
class Triple:
    """One extracted fact: from_title --relation--> to_value."""
    from_title: str
    relation: str
    to_value: str
    is_literal: bool
    evidence: str = ""
    # Node type for from_title/to_value (e.g. "Person", "Organization",
    # "Location", "WorkOfArt", "Event", "Concept") -- used to assign each
    # extracted entity to a real Nebula TAG instead of one flat entity tag.
    # to_type defaults to "Value" for literal spans (dates/numbers/short
    # phrases), since those aren't classified against the same open-ended
    # entity-type vocabulary as paragraph-title subjects/objects.
    from_type: str = "Entity"
    to_type: str = "Entity"


@dataclass
class MentionedEntity:
    """A named real-world entity mentioned somewhere in the passages, given
    its own type + grounded description regardless of whether it also ends
    up as a triple's subject/object -- see the module docstring's gleaning/
    mentioned_entities design note for why this exists (borrowed from
    GraphRAG's own extraction pipeline, which produces entity and
    relationship records independently from the same pass)."""
    name: str
    entity_type: str = "Entity"
    description: str = ""


@dataclass
class LocalGraph:
    """Result of extracting one question's closed-world local graph."""
    topic_title: str = ""
    # Other paragraph titles the question specifically names as subjects
    # relevant to topic_title -- mirrors PlannerMapping.entity_mentions in
    # llm_planner.py (an open, general field, not tied to any question
    # "type"). Judged by the same extraction LLM call that already reads the
    # full question, no extra call and no dependence on how the source
    # dataset happens to label the question. What to actually DO with these
    # extra entities (find a path, derive a relational answer, or just use
    # their own facts as another bridge hop) is decided downstream, not here.
    entity_mentions: list[str] = field(default_factory=list)
    triples: list[Triple] = field(default_factory=list)
    # Named entities mentioned ANYWHERE in the passages, independent of
    # whether they became a triple's subject/object (see MentionedEntity) --
    # e.g. a person named only as "composed by X" in passing, never the
    # subject of a captured relation, still gets a vertex + description via
    # this list. Not closed-world-validated against the given titles (that's
    # the point: this is exactly for entities the given titles only talk
    # ABOUT, never headline themselves).
    mentioned_entities: list[MentionedEntity] = field(default_factory=list)
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


DEFAULT_SYSTEM_PROMPT = """You extract a small factual knowledge graph from Wikipedia paragraphs \
so it can be traversed to answer a question -- never answer the question yourself.

Rules:
1. Every triple is (subject, relation, object). "subject" MUST be exactly one of the given \
paragraph titles (copy it verbatim).
2. "object" is EITHER another one of the given paragraph titles (verbatim) -- set is_literal=false \
-- OR a literal phrase copied from the paragraph text (a date, name, nationality, occupation, \
yes/no, number, etc.) -- set is_literal=true. Never invent an object that isn't one of the \
titles or a phrase actually present in the text. Copy the MOST SPECIFIC full phrase the text \
gives, not a broader category you generalize it to -- e.g. if the text says "born in Teton \
County, Wyoming", extract "Teton County, Wyoming", not just "Wyoming"; if it says "22 April \
1951", extract that exact date, not just the year.
3. "relation" is a short snake_case label (e.g. "directed_by", "nationality", "founded_in", \
"starring").
4. Extract only relations you can directly support with a sentence from the paragraphs \
(put it in "evidence").
5. A single sentence often states MULTIPLE separate facts about the same subject -- an \
enumeration ("four titles with X, one with Y, and two with Z"), a breakdown after a colon \
("11 titles: 6 in doubles and 5 in mixed"), or a list of appositives. Extract EACH such \
sub-fact as its own separate triple with its own relation and evidence -- do not extract only \
one fact (or none) from a sentence just because it packs several clauses/numbers together. \
For example, given the sentence "Jane Doe has 11 titles to her name: 6 in doubles and 5 in \
singles.", extract BOTH {"from_title": "Jane Doe", "relation": "doubles_titles_count", \
"to_value": "6", "is_literal": true} AND {"from_title": "Jane Doe", "relation": \
"singles_titles_count", "to_value": "5", "is_literal": true} (and the overall total if useful) \
-- never stop after only one of them.
6. Also identify "topic_title": the ONE paragraph title that is the natural starting point for \
answering the question (the entity the question is primarily about).
7. Many questions describe their target entity indirectly, through a chain of clues ("the \
grandfather of X who was Y until death", "the author who wrote about Z"), rather than naming it. \
When that happens, do NOT pick whichever title is merely topically/thematically related -- check \
EACH candidate title's own paragraph content against EVERY clue in the question, and pick the one \
whose content actually satisfies all of them. If one of the given titles' own content directly \
answers the question (e.g. the question describes a person and one title names and describes \
exactly that person), that title itself IS the topic_title, even if a different, more \
thematically-prominent title is also present as a distractor.
8. Separately, identify "entity_mentions": any OTHER given paragraph titles (verbatim, excluding \
topic_title itself) that the question specifically names as a distinct subject relevant to \
topic_title -- e.g. "which was founded first, X or Y?", "what do X and Y have in \
common?", "is X related to Y?". Only include a title here if the question is actually about that \
specific named subject, not merely because the title's name happens to appear somewhere in the \
question text or the two are topically similar. Leave it empty for questions about only one \
entity. Critically, an entity_mention is USELESS on its own -- whatever the question needs to \
know about it (its founding date, its distance from something, its category, etc.) MUST also be \
extracted as real triples from THAT title's own paragraph, exactly as you would for topic_title \
(see rules 1-5) -- never list a title under "entity_mentions" and then extract zero triples about \
it just because your triples so far all connect back to topic_title instead.
9. For every triple, also classify "from_type" (the type of "subject") and "to_type" (the type \
of "object") -- a short CamelCase type name such as "Person", "Organization", "Location", \
"WorkOfArt", "Event", "Sport", "Species", or another type that clearly fits if none of these \
do. Use the SAME type name for the same real-world kind of entity across different \
triples/questions (e.g. always "Person" for a person, never "Human" in one place and "Person" \
in another). This applies to literal objects too (is_literal=true) whenever the literal itself \
IS THE NAME of a specific, distinct real-world entity -- e.g. a composer's name mentioned as a \
literal fact still gets "to_type": "Person", a country mentioned as a literal fact still gets \
"to_type": "Location". Only leave "to_type" as an empty string for a literal that has no \
natural entity type at all: a date, a number, a count, a yes/no, a bare descriptive word (an \
occupation, a nationality adjective, a short phrase), OR a CATEGORICAL PHRASE that describes \
what "from_title" itself IS rather than naming a separate entity (e.g. for "Finding Kraftland \
is a 2006 independent documentary", "2006 independent documentary" describes what Finding \
Kraftland IS -- it is NOT a distinct WorkOfArt of its own, so leave "to_type" empty -- \
"documentary"/"shaved ice drink"/"biography" are categories, not entities, even though they can \
sound like they "name a kind of entity"). A useful test: could this literal value be its own \
row somewhere else, referred to by that same name, independent of "from_title"? If not -- if it \
only makes sense as a description OF from_title -- it isn't a separate entity.
10. Family/genealogy relations (parent, child, spouse, sibling, grandparent, etc.) are easy to \
miss because they're usually just one clause inside a longer biographical sentence (e.g. "X \
(1353-1426) was a prince... He was the second son of Y and his wife Z."). Scan every sentence \
specifically for this kind of relation and extract it as its own triple (e.g. \
{"from_title": "X", "relation": "father", "to_value": "Y", "is_literal": false}) even when it's \
a minor clause, not the sentence's main subject -- multi-hop questions frequently chain exactly \
these relations (e.g. "who is X's grandfather?" needs X-father->Y and Y-father->Z as two \
separate triples).
11. Each paragraph's sentences describe the entity NAMED IN THAT PARAGRAPH'S OWN TITLE -- but \
a paragraph can also mention OTHER given titles by name in passing (a co-star, a relative, an \
unrelated person with a similar role). Before extracting a triple, confirm the sentence's own \
grammatical subject is actually "from_title" and not one of these other mentioned names -- \
never attach a fact (a birth date, a nationality, an occupation) to "from_title" just because \
it appears somewhere in a paragraph that is really describing someone else.
12. Separately from the triples above, identify "mentioned_entities": every distinctly-named \
real-world entity mentioned ANYWHERE in the paragraphs -- a person, place, organization, or work \
-- even one that is never a triple's subject or object (e.g. a composer named only in passing as \
"composed by X" inside a sentence that's really about a date or an opus number, never given a \
triple of their own). This is NOT restricted to the given paragraph titles -- it specifically \
INCLUDES entities the paragraphs only talk ABOUT without one of them being that entity's own \
paragraph. For each, give "name" (its ACTUAL PROPER NAME, not a description), "type" (same \
CamelCase vocabulary as "from_type"/"to_type"), and "description": 1-2 sentences grounded ONLY \
in what the paragraphs actually say about it, never invented.
"name" MUST be a proper noun the text actually uses for it -- never a generic descriptive phrase \
like "the world's oldest merchant bank", "a brokerage and investment banking firm", or "the \
composer" (these are DESCRIPTIONS of an entity, not its name, and belong in "description", not \
"name" -- if you catch yourself about to write a phrase like this as a "name", find the entity's \
actual proper name elsewhere in the same paragraph instead and use that). A single paragraph \
often refers to the SAME entity multiple ways in different sentences (its full legal name once, \
then "the bank"/"the firm"/a superlative description later) -- these all describe ONE entity: \
list it ONCE under whichever name is most complete/formal, folding every other mention's detail \
into that one "description", not as separate entries. Do not list the same entity twice."""

DEFAULT_USER_TEMPLATE = """Question: {question}

Paragraph titles (subjects/objects MUST come from this list, verbatim): {titles}

Paragraphs:
{passages}

Extract triples and the topic_title using only these paragraphs."""

APPROVED_TYPES_SUFFIX = """

This tenant already has these established entity types: {approved_node_types}. \
Reuse one of these for "from_type"/"to_type" whenever the entity genuinely fits one of them \
-- only introduce a new type name when none of these fit."""

# "Gleaning" -- borrowed from Microsoft GraphRAG's own extraction pipeline
# (graphrag/index/operations/extract_graph/graph_extractor.py): a single-shot
# extraction reliably leaves genuine facts on the table even when its
# response looks complete (verified directly: a first pass on a
# family-relation-heavy passage set caught the key genealogy triple but
# still missed half a dozen other stated facts -- a follow-up turn in the
# SAME conversation caught them, at a fraction of the cost of a full second
# extraction call, since it reuses the existing context instead of re-
# reading the passages). Kept general (not genealogy-specific) since the
# gap isn't limited to family relations.
GLEANING_CONTINUE_PROMPT = """Some triples may have been missed in the last extraction. Re-scan \
the SAME passages for any additional facts that satisfy the original rules (including facts \
about entities already mentioned, not just new ones) and return ONLY the additional triples you \
missed, as a JSON object: {"triples": [{"from_title": str, "relation": str, "to_value": str, \
"is_literal": bool, "evidence": str, "from_type": str, "to_type": str}]}. Do not repeat any \
triple already listed above. Return {"triples": []} if there is truly nothing left to add."""

GLEANING_LOOP_PROMPT = """Is there anything else you missed? Answer with a single letter: Y if \
there are still triples that should be added, or N if there are none."""


def _sanitize_type_name(raw: str, *, default: str = "Entity") -> str:
    """Coerce an LLM-produced entity type name into a safe Nebula TAG/EDGE
    identifier ([A-Za-z_][A-Za-z0-9_]*) -- the prompt asks for CamelCase
    with no spaces, but isn't a hard constraint the model always follows
    (e.g. "Social group" instead of "SocialGroup"). Applied here, at parse
    time, so every downstream consumer (the ontology registry, the
    per-type vertex/edge row dicts, Nebula schema sync) sees the same
    already-safe name -- sanitizing only at the Nebula-write step would
    desync the registry/dict keys from the actual TAG name.
    """
    words = re.findall(r"[A-Za-z0-9]+", str(raw or ""))
    name = "".join(word[:1].upper() + word[1:] for word in words)
    if not name:
        return default
    if name[0].isdigit():
        name = f"T{name}"
    return name


def _format_passages(context: list[list[Any]], max_passages: int = 12) -> str:
    lines = []
    for i, (title, sentences) in enumerate(context[:max_passages], start=1):
        body = " ".join(str(s).strip() for s in sentences)
        lines.append(f"[{i}] {title}: {body}")
    return "\n".join(lines)


class PassageRelationExtractor:
    """LLM-based closed-world relation extractor for one question's candidate passages.

    Modeled directly on ``HotpotQAAnswerer``/``LLMPlanner``: same model
    resolution, retry-on-empty-response, and error classification, all
    delegated to ``LLMPlanner`` (imported, not duplicated).
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        user_template: str | None = None,
        timeout: float = 30.0,
        temperature: float = 0.0,
        max_workers: int = 4,
        max_gleanings: int = 1,
    ):
        self.model = model or LLMPlanner._default_model()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.user_template = user_template or DEFAULT_USER_TEMPLATE
        self.timeout = timeout
        self.temperature = temperature
        # Number of extra "what did you miss" follow-up turns after the
        # main extraction succeeds (see GLEANING_CONTINUE_PROMPT). 0
        # disables gleaning entirely (matches pre-gleaning behavior exactly).
        self.max_gleanings = max_gleanings
        self.last_result: LocalGraph | None = None
        # litellm's own `timeout=` kwarg doesn't reliably fire when the
        # proxy/SOCKS layer hangs before completing a CONNECT tunnel (seen
        # in practice: a call blocked for 10+ minutes past its 30s timeout
        # with an ESTABLISHED-but-stuck local proxy connection). A
        # thread-based hard deadline guarantees forward progress regardless
        # of what the underlying library/proxy does. ``max_workers`` also
        # bounds how many ``extract_local_graph`` calls can have their
        # completion() in flight at once -- a caller running MANY questions
        # concurrently on ONE shared extractor instance (see
        # ``import_hotpotqa_nebula_tenant.materialize_hotpotqa_questions``)
        # must raise this to match its own outer concurrency, or the extra
        # outer workers just queue behind this executor's default of 4.
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def _completion_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if LLMPlanner._env_bool("ALETHEIA_LLM_PLANNER_JSON_MODE", True):
            kwargs["response_format"] = {"type": "json_object"}
        if self.model.startswith("openrouter/") and LLMPlanner._env_bool(
            "ALETHEIA_LLM_PLANNER_DISABLE_REASONING", True
        ):
            kwargs["reasoning"] = {"effort": "none", "exclude": True}
        return kwargs

    @staticmethod
    def _parse_json_response(content: Any) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload: Any) -> dict | None:
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("triples"), list):
                return None
            if not isinstance(payload.get("topic_title"), str):
                return None
            return payload

        try:
            return valid(json.loads(content))
        except (json.JSONDecodeError, TypeError):
            pass

        code_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        match = re.search(code_block_pattern, content)
        if match:
            try:
                return valid(json.loads(match.group(1).strip()))
            except (json.JSONDecodeError, TypeError):
                pass

        brace_pattern = r"\{[\s\S]*\}"
        match = re.search(brace_pattern, content)
        if match:
            try:
                return valid(json.loads(match.group(0)))
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    @staticmethod
    def _parse_gleaning_response(content: Any) -> list | None:
        """Lenient variant of ``_parse_json_response`` for a gleaning
        follow-up turn: only a ``"triples"`` list is expected (no
        ``topic_title``, since that was already established in the main
        extraction turn this continues from)."""
        if not isinstance(content, str):
            return None

        def valid(payload: Any) -> list | None:
            if not isinstance(payload, dict):
                return None
            triples = payload.get("triples")
            return triples if isinstance(triples, list) else None

        try:
            result = valid(json.loads(content))
            if result is not None:
                return result
        except (json.JSONDecodeError, TypeError):
            pass

        code_block_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        match = re.search(code_block_pattern, content)
        if match:
            try:
                result = valid(json.loads(match.group(1).strip()))
                if result is not None:
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        brace_pattern = r"\{[\s\S]*\}"
        match = re.search(brace_pattern, content)
        if match:
            try:
                result = valid(json.loads(match.group(0)))
                if result is not None:
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    @staticmethod
    def _validate_triples(items: list, valid_titles: set[str]) -> list[Triple]:
        """Shared triple-validation/assembly logic -- same rules regardless
        of whether ``items`` came from the main extraction turn or a
        gleaning follow-up (see ``GLEANING_CONTINUE_PROMPT``)."""
        triples: list[Triple] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            from_title = str(item.get("from_title", "")).strip()
            to_value = str(item.get("to_value", "")).strip()
            relation = str(item.get("relation", "")).strip()
            if not from_title or not to_value or not relation:
                continue
            if from_title not in valid_titles:
                continue
            is_literal = bool(item.get("is_literal", True))
            if not is_literal and to_value not in valid_titles:
                is_literal = True
            # Model echoed the subject back as the object (see
            # _needs_retry's docstring) -- drop even if every retry
            # attempt still produced it, rather than materialize a
            # self-loop edge into the graph.
            if not is_literal and from_title == to_value:
                continue
            from_type = _sanitize_type_name(item.get("from_type", ""))
            # Classified regardless of is_literal -- a literal span
            # can still be a named Person/Organization/Location the
            # LLM should type properly (see rule 9); "Value" is only
            # the fallback for spans the LLM leaves untyped (dates,
            # counts, yes/no, bare descriptive words).
            to_type = _sanitize_type_name(
                item.get("to_type", ""), default="Value" if is_literal else "Entity",
            )
            triples.append(Triple(
                from_title=from_title,
                relation=relation,
                to_value=to_value,
                is_literal=is_literal,
                evidence=str(item.get("evidence", "")),
                from_type=from_type,
                to_type=to_type,
            ))
        return triples

    @staticmethod
    def _validate_mentioned_entities(items: list) -> list[MentionedEntity]:
        """Sanitize the "mentioned_entities" list (see rule 12 / MentionedEntity's
        docstring). Deliberately NOT checked against valid_titles -- these
        are explicitly allowed to be entities the given titles only talk
        ABOUT, never headline themselves (that's the entire point)."""
        entities: list[MentionedEntity] = []
        seen_names: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            entities.append(MentionedEntity(
                name=name,
                entity_type=_sanitize_type_name(item.get("type", "")),
                description=str(item.get("description", "")).strip(),
            ))
        return entities

    @staticmethod
    def _needs_retry(parsed: dict, valid_titles: set[str]) -> bool:
        """Did this parsed response require silently falling back to a
        default for a closed-world-validated field?

        A response that fails to parse at all already triggers a retry
        (the loop above). This catches the quieter failure mode: JSON that
        parses fine but whose content didn't survive validation -- e.g.
        ``topic_title`` isn't one of the given titles, or the model
        produced triples but not one of them has a valid ``from_title``.
        Both are treated the same as a parse failure and retried, rather
        than silently accepted, because provider-side non-determinism means
        a repeat request at the same temperature often does better (verified
        directly: the identical prompt re-run against the same passages
        produced 8 triples vs. 2 across two calls). Generic on purpose --
        any closed-world-validated field works the same way, not just
        topic_title, so this doesn't need updating each time a new field
        gets its own validation.

        Also catches a specific extraction failure mode seen in genealogy-
        heavy passages: a "father"/"mother"/etc. triple where "to_value" is
        an exact copy of "from_title" -- the model echoing the subject back
        as the object instead of naming the actual parent from the evidence
        sentence (observed on titles that share a surname and only differ
        by a generational ordinal, e.g. "Patrick Chaworth, 3rd Viscount
        Chaworth" vs. "John Chaworth, 2nd Viscount Chaworth" -- genuinely
        different people, not an "impossible" self-reference to reject on
        principle, but this EXACT-STRING-MATCH case specifically means the
        model named no one else at all).
        """
        topic_title = str(parsed.get("topic_title", "")).strip()
        if topic_title not in valid_titles:
            return True
        triples = parsed.get("triples")
        if isinstance(triples, list) and triples:
            has_valid_triple = any(
                isinstance(t, dict) and str(t.get("from_title", "")).strip() in valid_titles
                for t in triples
            )
            if not has_valid_triple:
                return True
            has_self_referential_triple = any(
                isinstance(t, dict)
                and not t.get("is_literal", True)
                and str(t.get("from_title", "")).strip()
                and str(t.get("from_title", "")).strip() == str(t.get("to_value", "")).strip()
                for t in triples
            )
            if has_self_referential_triple:
                return True
        return False

    def _glean_additional_triples(
        self, *, messages: list[dict[str, str]], existing_triples: list[Triple], valid_titles: set[str],
    ) -> list[Triple]:
        """Up to ``self.max_gleanings`` follow-up turns asking what the main
        extraction missed (see ``GLEANING_CONTINUE_PROMPT``'s docstring).
        ``messages`` ends with the main extraction's own successful
        assistant turn -- gleaning continues that SAME conversation rather
        than re-sending the passages, so the model can see what it already
        extracted and avoid repeating itself.

        Best-effort: any failure (timeout, unparseable response) silently
        stops gleaning and returns whatever was accumulated so far --
        gleaning is a bonus on top of an already-successful extraction, not
        something that should ever turn a success into a failure."""
        try:
            from litellm import completion
        except ImportError:
            return list(existing_triples)

        triples = list(existing_triples)
        seen = {(t.from_title, t.relation, t.to_value, t.is_literal) for t in triples}
        messages = list(messages)

        for round_index in range(self.max_gleanings):
            messages.append({"role": "user", "content": GLEANING_CONTINUE_PROMPT})
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=messages,
                    timeout=self.timeout,
                    temperature=self.temperature,
                    **self._completion_kwargs(),
                )
                try:
                    raw_response = future.result(timeout=self.timeout + 15)
                except FutureTimeoutError:
                    if future.done():
                        raise
                    future.cancel()
                    raise TimeoutError(
                        f"hard timeout: no response after {self.timeout + 15:.0f}s (proxy/connect hang)"
                    )
                raw_contents = []
                if raw_response and raw_response.choices:
                    raw_contents = LLMPlanner._response_text_candidates(raw_response.choices[0].message)
            except Exception as e:
                logger.warning("Passage extractor: gleaning round %d failed, stopping: %s", round_index + 1, e)
                break

            gleaned_items = None
            raw_content = ""
            for candidate in raw_contents:
                gleaned_items = self._parse_gleaning_response(candidate)
                if gleaned_items is not None:
                    raw_content = candidate
                    break
            if not gleaned_items:
                # Empty/unparseable gleaning response -- model signaled
                # (or effectively signaled) nothing more to add.
                break

            new_triples = [
                t for t in self._validate_triples(gleaned_items, valid_titles)
                if (t.from_title, t.relation, t.to_value, t.is_literal) not in seen
            ]
            for t in new_triples:
                seen.add((t.from_title, t.relation, t.to_value, t.is_literal))
            triples.extend(new_triples)

            if round_index >= self.max_gleanings - 1:
                break  # last allowed round -- no point asking if there's more

            messages.append({"role": "assistant", "content": raw_content})
            messages.append({"role": "user", "content": GLEANING_LOOP_PROMPT})
            try:
                future = self._executor.submit(
                    completion, model=self.model, messages=messages,
                    timeout=self.timeout, temperature=self.temperature, **self._completion_kwargs(),
                )
                loop_response = future.result(timeout=self.timeout + 15)
                loop_contents = LLMPlanner._response_text_candidates(loop_response.choices[0].message)
                loop_answer = loop_contents[0].strip().upper() if loop_contents else "N"
            except Exception as e:
                logger.warning("Passage extractor: gleaning loop-check failed, stopping: %s", e)
                break
            if not loop_answer.startswith("Y"):
                break
            messages.append({"role": "assistant", "content": loop_contents[0]})

        return triples

    def extract_local_graph(
        self, question: str, context: list[list[Any]], approved_node_types: list[str] | None = None,
    ) -> LocalGraph:
        """Extract a closed-world local graph for one question.

        ``approved_node_types``, when given a non-empty list, nudges the
        model to reuse the tenant's already-established entity type names
        instead of inventing near-duplicates (see module docstring) -- it
        does not restrict/reject types outside this list.
        """
        start = time.time()
        result = LocalGraph()
        titles = [title for title, _ in context]

        if not question or not question.strip():
            result.error = "empty question"
            self.last_result = result
            return result
        if not context:
            result.error = "empty context"
            self.last_result = result
            return result

        user_msg = self.user_template.format(
            question=question,
            titles=json.dumps(titles, ensure_ascii=False),
            passages=_format_passages(context),
        )
        if approved_node_types:
            user_msg += APPROVED_TYPES_SUFFIX.format(
                approved_node_types=json.dumps(sorted(set(approved_node_types)), ensure_ascii=False)
            )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"triples": [{"from_title": str, "relation": str, "to_value": str, '
            '"is_literal": bool, "evidence": str, "from_type": str, "to_type": str}], '
            '"topic_title": str, "entity_mentions": [str], '
            '"mentioned_entities": [{"name": str, "type": str, "description": str}]}'
        )

        try:
            from litellm import completion
        except ImportError:
            result.error = "litellm not installed"
            result.error_type = "runtime"
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            self.last_result = result
            return result

        valid_titles = set(titles)

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + LLMPlanner._empty_response_retry_count() + LLMPlanner._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
                    timeout=self.timeout,
                    temperature=self.temperature,
                    **self._completion_kwargs(),
                )
                try:
                    raw_response = future.result(timeout=self.timeout + 15)
                except FutureTimeoutError:
                    # Python 3.11+ makes concurrent.futures.TimeoutError an
                    # alias of the builtin TimeoutError, so this branch also
                    # catches a TimeoutError raised BY completion() itself
                    # (e.g. litellm's own timeout= firing normally) -- not
                    # just our own wait timing out. future.done() tells them
                    # apart: True means completion() already ran and raised
                    # on its own (re-raise that original error as-is); False
                    # means our wait genuinely elapsed with no response.
                    if future.done():
                        raise
                    future.cancel()
                    raise TimeoutError(
                        f"hard timeout: no response after {self.timeout + 15:.0f}s (proxy/connect hang)"
                    )

                raw_contents = []
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = LLMPlanner._response_text_candidates(msg)

                last_exception = None
                candidate_parsed = None
                for candidate in raw_contents:
                    candidate_parsed = self._parse_json_response(candidate)
                    if candidate_parsed is not None:
                        raw_content = candidate
                        break

                if candidate_parsed is not None:
                    # Keep the best-so-far result even if it's low-quality,
                    # in case every attempt ends up needing a fallback --
                    # better to use the last attempt's data than none at all.
                    parsed = candidate_parsed
                    if not self._needs_retry(candidate_parsed, valid_titles):
                        break
                    if attempt < max_attempts:
                        logger.warning(
                            "Passage extractor: parsed but a validated field required a "
                            "fallback (provider non-determinism can give a better result on "
                            "retry), retrying attempt %d/%d", attempt + 1, max_attempts,
                        )
                    continue
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "Passage extractor: empty response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "Passage extractor: transient error (%s) on attempt %d/%d, retrying: %s",
                    LLMPlanner.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(LLMPlanner._retry_backoff_seconds())

        try:
            if parsed:
                triples = self._validate_triples(parsed.get("triples", []), valid_titles)
                if self.max_gleanings > 0:
                    triples = self._glean_additional_triples(
                        messages=[
                            {"role": "system", "content": self.system_prompt + json_instruction},
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": raw_content},
                        ],
                        existing_triples=triples,
                        valid_titles=valid_titles,
                    )
                result.triples = triples
                topic_title = str(parsed.get("topic_title", "")).strip()
                result.topic_title = topic_title if topic_title in valid_titles else (titles[0] if titles else "")
                entity_mentions = parsed.get("entity_mentions", [])
                if not isinstance(entity_mentions, list):
                    entity_mentions = []
                result.entity_mentions = [
                    str(t).strip() for t in entity_mentions
                    if str(t).strip() in valid_titles and str(t).strip() != result.topic_title
                ]
                mentioned_entities = parsed.get("mentioned_entities", [])
                if not isinstance(mentioned_entities, list):
                    mentioned_entities = []
                result.mentioned_entities = self._validate_mentioned_entities(mentioned_entities)
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = LLMPlanner.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("Passage extractor failed after retries: %s", last_exception)
            else:
                result.error = LLMPlanner._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "Passage extractor: JSON parse failed, content[:200]=%r", raw_content[:200]
                )

        except Exception as e:
            result.error = str(e)
            result.error_type = LLMPlanner.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("Passage extractor failed: %s", e)

        self.last_result = result
        return result
