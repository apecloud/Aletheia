"""Extract a closed-world local knowledge graph from one HotpotQA question.

Unlike WebQSP/Mintaka (whose entities and typed relations come from a real
external KG -- Freebase / Wikidata), HotpotQA ships only raw Wikipedia
paragraphs. To build a real graph tenant without any live external fetch,
this module asks an LLM to read a *single* question's own ~10 passages and
extract typed (subject, relation, object) triples restricted to a closed
world: the subject/object of every triple must be either one of the
question's own passage titles, or a short literal span copied from the text
(a date, name, nationality, yes/no, etc.). No entity linking to Wikidata/
Wikipedia happens anywhere in this module -- the only network call is the
LLM completion itself, same category already used throughout this project.

Usage:
    from hotpotqa_kg_extraction import HotpotQARelationExtractor
    extractor = HotpotQARelationExtractor()
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

logger = logging.getLogger("HotpotQARelationExtractor")


@dataclass
class Triple:
    """One extracted fact: from_title --relation--> to_value."""
    from_title: str
    relation: str
    to_value: str
    is_literal: bool
    evidence: str = ""


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
-- OR a short literal phrase copied from the paragraph text (a date, name, nationality, \
occupation, yes/no, number, etc.) -- set is_literal=true. Never invent an object that isn't \
one of the titles or a short phrase actually present in the text.
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
entity."""

DEFAULT_USER_TEMPLATE = """Question: {question}

Paragraph titles (subjects/objects MUST come from this list, verbatim): {titles}

Paragraphs:
{passages}

Extract triples and the topic_title using only these paragraphs."""


def _format_passages(context: list[list[Any]], max_passages: int = 12) -> str:
    lines = []
    for i, (title, sentences) in enumerate(context[:max_passages], start=1):
        body = " ".join(str(s).strip() for s in sentences)
        lines.append(f"[{i}] {title}: {body}")
    return "\n".join(lines)


class HotpotQARelationExtractor:
    """LLM-based closed-world relation extractor for one HotpotQA question.

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
    ):
        self.model = model or LLMPlanner._default_model()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.user_template = user_template or DEFAULT_USER_TEMPLATE
        self.timeout = timeout
        self.temperature = temperature
        self.last_result: LocalGraph | None = None
        # litellm's own `timeout=` kwarg doesn't reliably fire when the
        # proxy/SOCKS layer hangs before completing a CONNECT tunnel (seen
        # in practice: a call blocked for 10+ minutes past its 30s timeout
        # with an ESTABLISHED-but-stuck local proxy connection). A
        # thread-based hard deadline guarantees forward progress regardless
        # of what the underlying library/proxy does.
        self._executor = ThreadPoolExecutor(max_workers=4)

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
        return False

    def extract_local_graph(self, question: str, context: list[list[Any]]) -> LocalGraph:
        """Extract a closed-world local graph for one HotpotQA question."""
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
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"triples": [{"from_title": str, "relation": str, "to_value": str, '
            '"is_literal": bool, "evidence": str}], "topic_title": str, "entity_mentions": [str]}'
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

        try:
            parsed = None
            raw_contents: list[str] = []
            raw_content = ""
            finish_reason = ""
            max_attempts = 1 + LLMPlanner._empty_response_retry_count()

            for attempt in range(1, max_attempts + 1):
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
                            "HotpotQA extractor: parsed but a validated field required a "
                            "fallback (provider non-determinism can give a better result on "
                            "retry), retrying attempt %d/%d", attempt + 1, max_attempts,
                        )
                    continue
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "HotpotQA extractor: empty response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )

            if parsed:
                triples: list[Triple] = []
                for item in parsed.get("triples", []):
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
                    triples.append(Triple(
                        from_title=from_title,
                        relation=relation,
                        to_value=to_value,
                        is_literal=is_literal,
                        evidence=str(item.get("evidence", "")),
                    ))
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
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            else:
                result.error = LLMPlanner._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "HotpotQA extractor: JSON parse failed, content[:200]=%r", raw_content[:200]
                )

        except Exception as e:
            result.error = str(e)
            result.error_type = LLMPlanner.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("HotpotQA extractor failed: %s", e)

        self.last_result = result
        return result
