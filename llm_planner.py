"""LLM-enhanced question-to-relation mapping for Aletheia planner.

Uses an LLM (via litellm + instructor) to map natural language questions
to relevant relation types from the ontology link_config. This replaces
keyword matching as the primary mapping mechanism, falling back to
keyword matching when the LLM is unavailable or returns no results.

Architecture follows the OntGQA planner-judge pattern:
- Planner: LLM receives question + available relations (with descriptions)
- Judge: LLM returns ranked candidate relations with confidence scores
- Integrator: results are merged into QuestionPathPlan.matched_link_keys

Usage:
    from llm_planner import LLMPlanner
    planner = LLMPlanner()
    result = planner.map_question_to_relations(
        question="what is the nationality of the president",
        topic_type="person",
        link_config=[...],
        descriptions={...},
    )
"""

from __future__ import annotations

import json
import re
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("LLMPlanner")


# ---------------------------------------------------------------------------
# Structured output schema for instructor
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel, Field
    _HAS_PYDANTIC = True
except ImportError:
    _HAS_PYDANTIC = False
    BaseModel = object  # type: ignore

    class Field:  # type: ignore
        def __init__(self, *a, **kw):
            pass


class RelationCandidate(BaseModel if _HAS_PYDANTIC else object):
    """A single relation candidate selected by the LLM."""
    link_key: str = Field(description="The exact link_key from the available relations")
    confidence: float = Field(description="Confidence score 0.0-1.0", ge=0.0, le=1.0)
    reasoning: str = Field(description="Brief explanation of why this relation is relevant")


class LLMPlannerResult(BaseModel if _HAS_PYDANTIC else object):
    """Structured output from the LLM planner."""
    selected_relations: list[RelationCandidate] = Field(
        description="Ranked list of relations relevant to the question"
    )
    matched_entity_types: list[str] = Field(
        description="Entity types inferred from the question that are relevant to the selected relations"
    )
    selected_capabilities: list[str] = Field(
        default_factory=list,
        description="Which of the offered retrieval capabilities (by name) this question needs",
    )
    entity_mentions: list[str] = Field(
        default_factory=list,
        description="Specific named entities the question refers to, when it names more than one "
        "(comparison, relationship-check, joint analysis, etc.) -- empty for single-entity questions",
    )


# ---------------------------------------------------------------------------
# Result dataclass (used by reasoning_engine without pydantic dependency)
# ---------------------------------------------------------------------------

@dataclass
class PlannerMapping:
    """Result of LLM question-to-relation mapping."""
    matched_link_keys: set[str] = field(default_factory=set)
    ranked_link_keys: list[str] = field(default_factory=list)
    matched_entity_types: set[str] = field(default_factory=set)
    confidence_scores: dict[str, float] = field(default_factory=dict)
    reasoning: str = ""
    latency_ms: float = 0.0
    token_usage: int = 0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""
    # Which named retrieval capabilities (from the caller-supplied registry,
    # see map_question_to_relations's `capabilities` param) this question
    # needs, judged by the LLM rather than keyword matching. An open set,
    # not a fixed enum -- adding a new capability elsewhere never requires
    # touching this dataclass. When used_fallback is True this stays empty,
    # leaving the caller at its safe "fetch everything" default.
    selected_capabilities: set[str] = field(default_factory=set)
    # Specific named entities the question refers to, when it names more
    # than one (comparison, relationship-check, joint analysis, etc.) --
    # raw text spans; resolving them to real instance ids against a known
    # candidate pool is the caller's job (see
    # ReasoningEngine._resolve_entity_mentions). Not tied to any particular
    # question "type" -- any question naming 2+ specific subjects can use
    # this, not just comparisons.
    entity_mentions: list[str] = field(default_factory=list)


@dataclass
class QuestionEntityMentions:
    """Result of identifying which real-world entity/entities a bare
    question refers to -- no passages, no known graph vertices given, just
    the question text. Unlike closed-world extraction (which validates a
    name against a given candidate list), this asks the model to use its
    own general knowledge to resolve INDIRECT/DESCRIPTIVE references (e.g.
    "which group of people in West Africa with populations in Ghana, Ivory
    Coast... uses the Ida?" -> "Yoruba people", never named in the question
    itself). The caller resolves each returned name against the actual
    graph (e.g. via agents/ontology_label_embeddings.find_nearest_label)
    -- a hallucinated name simply fails that lookup, so this doesn't need
    its own closed-world validation the way passage extraction does."""
    mentions: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


@dataclass
class EntityCandidateVerification:
    """Result of asking the model to pick which (if any) retrieved
    candidate a guessed entity mention actually refers to. A guessed
    mention name (from ``extract_question_entity_mentions``) doesn't always
    match the graph's exact label spelling -- this closes that gap by
    retrieving several nearest candidates (not trusting a single nearest-
    neighbor argmin) and letting the model confirm/pick among them, the
    same "disambiguate against a given candidate list" pattern already
    proven for closed-world passage extraction, just with the candidate
    list built from vector search instead of given passage titles."""
    chosen_index: int | None = None
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


@dataclass
class EntityDescriptionResult:
    """Result of summarizing an entity's evidence sentences (already
    extracted during passage extraction, no new reading) into a short
    description -- borrowed from GraphRAG's own construction pipeline,
    which embeds a description of everything the corpus says about an
    entity rather than just its bare label, giving query-time entity
    linking (``extract_question_entity_mentions`` +
    ``verify_entity_candidate``) much more to match against than a
    same-looking name alone. Used only to build a query-time-linking
    embedding index (``agents/ontology_label_embeddings.py``'s
    "description" field) -- never fed into construction-time entity
    dedup (``agents/graph_entity_resolver.py``), which needs the
    stability of a bare type+label match, not context that varies by
    which passage happened to mention the entity."""
    description: str = ""
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


@dataclass
class RelationalDerivation:
    """Result of deriving an answer from several centers' own facts (used
    when no direct graph path connects them). Not tied to any question
    "type" -- serves any question naming multiple entities, whatever shape
    the answer takes: picking one of the centers ("which was founded
    first?"), naming a shared trait across all of them ("what pursuit did
    both have in common?"), describing a relationship ("how are X and Y
    related?"), or anything else. The schema doesn't presuppose the answer
    is "one of the given centers" -- that would hardcode a pick-a-winner
    question shape; ``supporting_center_nodes`` can hold zero, one, or all
    of the given centers depending on what the question actually needs."""
    answer: str = ""
    supporting_center_nodes: list[str] = field(default_factory=list)
    reasoning: str = ""
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


@dataclass
class QuestionDecomposition:
    """Result of splitting a question into independent logical sub-
    questions, each answerable by starting from ONE named entity and
    following its own relation(s) -- borrowed from StepChain GraphRAG
    (arXiv:2510.02827), whose own ablation study found question
    decomposition to be the single biggest lever on HotpotQA accuracy,
    bigger than adding graph-based reasoning alone. An already-atomic
    question (one clue, one hop) decomposes to a single-item list
    containing itself unchanged -- the caller treats that as a no-op signal
    to use the existing non-decomposed resolution path instead."""
    sub_questions: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


@dataclass
class MergedAnswerResult:
    """Result of synthesizing a final answer from each sub-question's own
    partial answer (see QuestionDecomposition's docstring) -- mirrors
    StepChain GraphRAG's two-tier merge (partial per-sub-question answers
    -> a single LLM synthesis re-grounded against the original question)."""
    answer: str = ""
    reasoning: str = ""
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


# ---------------------------------------------------------------------------
# LLM Planner
# ---------------------------------------------------------------------------

# Default prompt template for the LLM
DEFAULT_SYSTEM_PROMPT = """You are an ontology reasoning planner. Given a natural language question and a list of available relation types (with descriptions), select the relations most relevant to answering the question, and judge which retrieval capabilities it needs.

Rules:
1. Only select relations whose link_key exactly matches one from the available relations list.
2. For each selected relation, provide a confidence score (0.0-1.0) and brief reasoning.
3. Also list entity types from the question that are relevant to the selected relations.
4. If no relations are relevant, return empty lists.
5. Prefer high-precision selections over broad coverage.
6. You will be given a list of named retrieval capabilities (each with a description of when it
   applies). Select ONLY the capability names that this specific question genuinely needs, based
   on its intent -- not surface keyword overlap. Return their exact names in "selected_capabilities".
   If none apply, return an empty list. Do not invent capability names not in the given list.
7. Separately: if the question names TWO OR MORE SPECIFIC individual entities (not "this vs a
   whole population" -- specific named subjects, e.g. "which was founded first, X or Y?", "is X
   related to Y?", "how do X and Y compare on Z?"), list the exact entity name/subject phrases
   from the question (not paraphrased) in "entity_mentions". Leave it empty for questions about
   only one entity."""

DEFAULT_USER_TEMPLATE = """Question: {question}
Topic entity type: {topic_type}

Available relations:
{relations}

Available retrieval capabilities:
{capabilities}

Select the relations most relevant to answering this question, which capabilities it needs, and
any specific entity mentions (only if it names 2+ specific subjects)."""


class LLMPlanner:
    """LLM-enhanced question-to-relation mapper.

    Uses litellm + instructor for structured output. Falls back gracefully
    when LLM is unavailable. Model/provider are configurable via environment
    variables or constructor arguments.
    """

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str | None = None,
        user_template: str | None = None,
        timeout: float = 30.0,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ):
        self.model = model or self._default_model()
        self.system_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
        self.user_template = user_template or DEFAULT_USER_TEMPLATE
        self.timeout = timeout
        self.temperature = temperature
        # Retain the constructor parameter for compatibility, but do not pass
        # max_tokens to the provider. The planner should not impose a completion cap.
        self.max_tokens = None
        self.last_result: PlannerMapping | None = None
        # litellm's own `timeout=` kwarg doesn't reliably fire on a stuck
        # proxy/SOCKS CONNECT tunnel -- observed hanging indefinitely (0% CPU,
        # ESTABLISHED connection to a local proxy that never responds) during
        # a real benchmark run. Same hard-timeout pattern as
        # GraphHitJudge/PassageRelationExtractor: run completion() in a
        # worker thread and enforce our own wall-clock timeout via
        # future.result().
        self._executor = ThreadPoolExecutor(max_workers=4)

    @staticmethod
    def classify_error(error: str) -> str:
        """Classify LLM runtime errors for benchmark accounting."""
        lowered = (error or "").lower()
        if not lowered:
            return ""
        runtime_invalid_markers = (
            "failed to parse json",
            "invalid json",
            "empty content",
            "missing content",
            "non-content response",
        )
        if any(marker in lowered for marker in runtime_invalid_markers):
            return "runtime_invalid"
        if "timeout" in lowered or "timed out" in lowered:
            return "timeout"
        provider_markers = (
            "402",
            "requires more credits",
            "insufficient credits",
            "max_tokens",
            "openrouter",
            "rate limit",
            "quota",
            "provider",
        )
        if any(marker in lowered for marker in provider_markers):
            return "provider"
        return "runtime"

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        value = os.environ.get(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def _completion_kwargs(self) -> dict[str, Any]:
        """Provider options that make planner output parseable."""
        kwargs: dict[str, Any] = {}
        if self._env_bool("ALETHEIA_LLM_PLANNER_JSON_MODE", True):
            kwargs["response_format"] = {"type": "json_object"}
        if self.model.startswith("openrouter/") and self._env_bool(
            "ALETHEIA_LLM_PLANNER_DISABLE_REASONING", True
        ):
            # Prefer final JSON over hidden thought for relation planning.
            kwargs["reasoning"] = {"effort": "none", "exclude": True}
        return kwargs

    @staticmethod
    def _empty_response_retry_count() -> int:
        """Retry only provider responses with no text candidates at all."""
        raw = os.environ.get("ALETHEIA_LLM_PLANNER_EMPTY_RESPONSE_RETRIES", "1")
        try:
            return max(0, int(raw))
        except ValueError:
            return 1

    @staticmethod
    def _transient_error_retry_count() -> int:
        """Extra retries for network/timeout exceptions raised while calling
        the model (dropped connections, proxy hangs, hard timeouts) --
        separate from ``_empty_response_retry_count``, which only covers the
        model responding with no text candidates. Shared by every LLM call
        site in this codebase (planner, relational-answer derivation,
        extractor, judge) -- without this, a single transient network blip
        immediately falls back to a degraded non-LLM path (keyword-only
        matching, substring scoring, etc.) instead of just retrying. Real
        observed cause: a local proxy shared by many unrelated processes on
        the machine occasionally refuses new connections under load
        (``[Errno 61] Connection refused``), not a remote rate limit."""
        raw = os.environ.get("ALETHEIA_LLM_TRANSIENT_RETRIES", "2")
        try:
            return max(0, int(raw))
        except ValueError:
            return 2

    @staticmethod
    def _retry_backoff_seconds() -> float:
        """Delay before retrying after a transient network error, so a
        momentarily-congested shared proxy has time to drain -- observed:
        every retry attempt failing with the identical "Connection refused"
        error, back to back, with no delay between them, meaning an instant
        retry mostly just re-hits the same congestion."""
        raw = os.environ.get("ALETHEIA_LLM_RETRY_BACKOFF_SECONDS", "2.0")
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 2.0

    @staticmethod
    def _default_model() -> str:
        """Resolve default model from environment.

        Priority:
        1. ALETHEIA_LLM_PLANNER_MODEL env var (explicit override)
        2. ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL (existing config)
        3. Fallback: openrouter/qwen/qwen3.6-27b (confirmed by @dullboy)
        """
        # Explicit override for the LLM planner
        explicit = os.environ.get("ALETHEIA_LLM_PLANNER_MODEL", "")
        if explicit:
            return explicit
        # Existing OpenRouter config
        provider = os.environ.get("ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER", "")
        if provider == "openrouter":
            model = os.environ.get("ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL", "")
            if model:
                return f"openrouter/{model}"
        # Fallback: try OpenAI
        if os.environ.get("OPENAI_API_KEY"):
            return "gpt-4o-mini"
        # Fallback: try Gemini
        if os.environ.get("GEMINI_API_KEY"):
            return "gemini/gemini-2.0-flash"
        # Default: Qwen 3.6 27B via OpenRouter (confirmed by @dullboy)
        if os.environ.get("OPENROUTER_API_KEY"):
            return "openrouter/qwen/qwen3.6-27b"
        return "openrouter/qwen/qwen3.6-27b"

    def _format_relations(
        self,
        link_config: list[dict],
        descriptions: dict[str, str],
        max_relations: int = 80,
    ) -> str:
        """Format link_config into a readable relation list for the LLM prompt."""
        lines = []
        for i, lc in enumerate(link_config[:max_relations]):
            link_key = lc["link"]
            desc = descriptions.get(link_key, "")
            from_type = lc.get("from", "")
            to_type = lc.get("to", "")
            line = f"  {i+1}. link_key=\"{link_key}\" | from={from_type} to={to_type}"
            if desc:
                line += f" | description=\"{desc}\""
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _format_capabilities(capabilities: dict[str, str]) -> str:
        """Format a caller-supplied capability registry for the LLM prompt.

        An open registry, not a fixed enum -- the caller (typically
        ReasoningEngine) owns which capabilities exist; this just presents
        whatever it's given, the same way _format_relations presents
        whatever link_config it's given."""
        if not capabilities:
            return "  (none offered)"
        return "\n".join(f'  - "{name}": {desc}' for name, desc in capabilities.items())

    def map_question_to_relations(
        self,
        question: str,
        topic_type: str,
        link_config: list[dict],
        descriptions: dict[str, str],
        capabilities: dict[str, str] | None = None,
    ) -> PlannerMapping:
        """Map a natural language question to relevant relation types via LLM.

        Returns a PlannerMapping with matched link keys, entity types,
        confidence scores, selected capabilities, entity mentions, and
        observability metadata. ``capabilities`` is an open registry
        ({name: description}) of retrieval capabilities the caller can
        offer -- not a fixed set baked into this module.
        """
        start = time.time()
        result = PlannerMapping()

        if not question or not question.strip():
            result.error = "empty question"
            self.last_result = result
            return result

        if not link_config:
            result.error = "empty link_config"
            self.last_result = result
            return result

        capabilities = capabilities or {}
        relations_text = self._format_relations(link_config, descriptions)
        user_msg = self.user_template.format(
            question=question,
            topic_type=topic_type,
            relations=relations_text,
            capabilities=self._format_capabilities(capabilities),
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

        valid_link_keys = {lc["link"] for lc in link_config}

        # Use raw litellm + manual JSON parsing directly.
        # instructor 1.14.x is incompatible with Qwen models that return
        # reasoning_content alongside content ("multiple tool calls" error).
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            "{\"selected_relations\": [{\"link_key\": str, \"confidence\": float, "
            "\"reasoning\": str}], \"matched_entity_types\": [str], "
            "\"selected_capabilities\": [str], \"entity_mentions\": [str]}"
        )

        parsed = None
        raw_contents = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

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

                # Extract parseable final content. Some reasoning models expose
                # non-final text in reasoning_content; parse it only as recovery.
                raw_contents = []
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_json_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty response for question=%r, retrying attempt %d/%d",
                    question[:80], attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                for candidate in parsed.get("selected_relations", []):
                    key = candidate.get("link_key", "")
                    if key in valid_link_keys:
                        result.matched_link_keys.add(key)
                        if key not in result.ranked_link_keys:
                            result.ranked_link_keys.append(key)
                        conf = candidate.get("confidence", 0.5)
                        try:
                            conf = float(conf)
                        except (TypeError, ValueError):
                            conf = 0.5
                        conf = max(0.0, min(1.0, conf))
                        result.confidence_scores[key] = conf

                result.matched_entity_types = set(parsed.get("matched_entity_types", []))
                selected_capabilities = parsed.get("selected_capabilities", [])
                result.selected_capabilities = (
                    {str(c) for c in selected_capabilities if str(c) in capabilities}
                    if isinstance(selected_capabilities, list) else set()
                )
                entity_mentions = parsed.get("entity_mentions", [])
                result.entity_mentions = (
                    [str(e) for e in entity_mentions] if isinstance(entity_mentions, list) else []
                )
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000

                logger.info(
                    "LLM planner: question=%r, matched %d relations, %.0fms",
                    question[:80], len(result.matched_link_keys), result.latency_ms
                )
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: JSON parse failed for question=%r, content[:200]=%r",
                    question[:80], raw_content[:200]
                )

        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner failed: %s", e)

        self.last_result = result
        return result

    @staticmethod
    def _coerce_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return ""

    @classmethod
    def _message_field(cls, message: Any, name: str) -> Any:
        if isinstance(message, dict):
            return message.get(name)
        return getattr(message, name, None)

    @classmethod
    def _response_text_candidates(cls, message: Any) -> list[str]:
        """Return final-content candidates first, recoverable reasoning second."""
        candidates: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            text = cls._coerce_text(value).strip()
            if text and text not in seen:
                candidates.append(text)
                seen.add(text)

        add(cls._message_field(message, "content"))
        add(cls._message_field(message, "reasoning_content"))
        add(cls._message_field(message, "reasoning"))

        for container_name in ("additional_kwargs", "provider_specific_fields", "model_extra"):
            container = cls._message_field(message, container_name)
            if isinstance(container, dict):
                add(container.get("content"))
                add(container.get("reasoning_content"))
                add(container.get("reasoning"))
        return candidates

    @staticmethod
    def _parse_failure_message(contents: list[str], finish_reason: str = "") -> str:
        suffix = f"; finish_reason={finish_reason}" if finish_reason else ""
        if not contents:
            return f"Failed to parse JSON from LLM response: empty content{suffix}"
        return f"Failed to parse JSON from LLM response{suffix}"

    @staticmethod
    def _parse_json_response(content) -> dict | None:
        """Extract and parse JSON from LLM response content.

        Handles cases where the model wraps JSON in markdown code blocks
        or includes extra text before/after the JSON.
        """
        if not isinstance(content, str):
            return None

        def valid(payload: Any) -> dict | None:
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("selected_relations"), list):
                return None
            if not isinstance(payload.get("matched_entity_types"), list):
                return None
            return payload

        # Try direct parse first
        try:
            return valid(json.loads(content))
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting from markdown code block
        code_block_pattern = r'```(?:json)?\s*([\s\S]*?)\`\`\`'
        match = re.search(code_block_pattern, content)
        if match:
            try:
                return valid(json.loads(match.group(1).strip()))
            except (json.JSONDecodeError, TypeError):
                pass

        # Try finding first { ... } block
        brace_pattern = r"\{[\s\S]*\}"
        match = re.search(brace_pattern, content)
        if match:
            try:
                return valid(json.loads(match.group(0)))
            except (json.JSONDecodeError, TypeError):
                pass

        return None

    # ------------------------------------------------------------------
    # Relational derivation (no direct graph path between named centers) --
    # serves any question naming multiple specific entities, not just
    # comparisons (relationship-checks, joint analysis, etc.)
    # ------------------------------------------------------------------

    RELATIONAL_SYSTEM_PROMPT = """You answer a question about several specific entities, based ONLY \
on the facts given for each -- never on the entities' names.

Rules:
1. Each entity is identified only by an opaque "center_node" id -- you are NOT told its name.
2. Derive the actual answer to the question from the given facts (dates, counts, values, relations) \
-- never from guessing which id "sounds right". The answer can take whatever shape the question \
needs: identifying one center ("which was founded first?"), naming a trait shared by several or all \
of them ("what do they have in common?"), describing a relationship between them ("how are X and Y \
related?"), a yes/no, a count, etc. Do not assume the answer must single out exactly one center.
3. List every center_node whose facts you actually used to derive the answer in \
"supporting_center_nodes" -- this can be one, several, or all of the given centers, whichever \
actually support the answer you gave.
4. If the facts are insufficient to fully decide, give your best answer from what's available and \
say so in your reasoning -- never leave "answer" empty."""

    RELATIONAL_USER_TEMPLATE = """Question: {question}

Candidates (facts only, no names):
{centers}

Derive the answer to the question from these facts, and list which center_node(s) support it."""

    @staticmethod
    def _format_relational_centers(centers_facts: list[dict]) -> str:
        lines = []
        for center in centers_facts:
            lines.append(f'center_node="{center["center_node"]}":')
            facts = center.get("facts") or []
            if not facts:
                lines.append("  (no facts available)")
            for fact in facts[:30]:
                lines.append(f"  - {fact.get('relation', '')}: {fact.get('value', '')}")
        return "\n".join(lines)

    @staticmethod
    def _parse_relational_json_response(content) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("answer"), str) or not payload["answer"]:
                return None
            if not isinstance(payload.get("supporting_center_nodes", []), list):
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

    def derive_relational_answer(self, question: str, centers_facts: list[dict]) -> RelationalDerivation:
        """Derive an answer that relates several named centers from each
        center's own facts alone (used when no direct graph path connects
        them). Not comparison-specific -- any question naming multiple
        entities can use this.

        ``centers_facts`` is a list of ``{"center_node": str, "facts": [{"relation":
        str, "value": str}, ...]}`` -- deliberately excluding each center's own
        label/identity (the caller, ``ReasoningEngine._llm_derive_relational_answer``,
        strips it before calling this) so the model must reason from the
        facts rather than recognizing a name.
        """
        start = time.time()
        result = RelationalDerivation()

        if not question or not question.strip() or not centers_facts:
            result.error = "empty question or centers_facts"
            self.last_result = result
            return result

        valid_center_nodes = {c["center_node"] for c in centers_facts}
        user_msg = self.RELATIONAL_USER_TEMPLATE.format(
            question=question,
            centers=self._format_relational_centers(centers_facts),
        )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"answer": str, "supporting_center_nodes": [str], "reasoning": str}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.RELATIONAL_SYSTEM_PROMPT + json_instruction},
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
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_relational_json_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty relational response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: relational transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                result.answer = str(parsed["answer"])
                supporting = parsed.get("supporting_center_nodes", [])
                result.supporting_center_nodes = [
                    c for c in supporting if isinstance(c, str) and c in valid_center_nodes
                ] if isinstance(supporting, list) else []
                result.reasoning = str(parsed.get("reasoning", "") or "")
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner relational derivation failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: relational JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )

        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner relational derivation failed: %s", e)

        self.last_result = result
        return result

    # ------------------------------------------------------------------
    # Question entity-mention extraction (no passages, no known graph) --
    # the "entry point" step: given a bare question, which real-world
    # entity/entities does it refer to? Resolving each name to an actual
    # graph vertex is the CALLER's job (nearest-neighbor lookup against
    # known labels) -- this only identifies candidate names.
    # ------------------------------------------------------------------

    ENTITY_MENTIONS_SYSTEM_PROMPT = """You identify which real-world entity or entities a question \
refers to, using your own general knowledge -- you are not given any source text to read. Same \
priority as identifying a graph traversal's starting point from a question: find the named subject \
to start FROM, never the question's final answer.

Rules:
1. FIRST, look for entities the question NAMES DIRECTLY (a person's full name, a work's title, a \
place's name, etc., e.g. "What time zone is Cleveland in?" -> "Cleveland"). This is the graph \
traversal's starting point -- extract it even when answering the question actually requires \
following one or more hops FROM it (e.g. "Which piece did Ludwig van Beethoven publish in 1801 that \
was dedicated to Count Moritz von Fries?" -> "Ludwig van Beethoven", the named person to start from \
-- NEVER try to guess the specific piece itself; that is the answer being asked for, reached by \
searching from the named entity, not the entity to return here).
2. ONLY when the question names NO entity at all -- describing its subject purely through defining \
clues instead (an ethnic group's home regions, a disease's symptoms, a person's known works or \
relationships, with no proper noun given for that subject) -- use your own knowledge to identify \
which specific real-world entity the clues describe, and return ITS actual name rather than echoing \
the descriptive phrase back. Do not use this rule when rule 1 already found a named entity, even if \
the question's ultimate answer requires further reasoning beyond that entity.
3. If the question names or describes TWO OR MORE distinct entities (a comparison, a relationship \
between two specific things, "did X and Y both..."), return ALL of them, in the order the question \
presents them.
4. If you cannot confidently identify a real entity (the question is too vague, or you are not \
sure of the answer), return an empty list rather than guessing at something you're unsure of.
5. Return each entity as a short name/title only -- no descriptions, no extra words."""

    ENTITY_MENTIONS_USER_TEMPLATE = """Question: {question}

Identify the specific real-world entity or entities this question refers to."""

    @staticmethod
    def _parse_entity_mentions_response(content) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("entity_mentions"), list):
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

    def extract_question_entity_mentions(self, question: str) -> QuestionEntityMentions:
        """Identify candidate entity name(s) a bare question refers to, for
        resolving a graph search center without a precomputed one -- see
        QuestionEntityMentions's docstring for why this differs from
        closed-world passage extraction."""
        start = time.time()
        result = QuestionEntityMentions()

        if not question or not question.strip():
            result.error = "empty question"
            self.last_result = result
            return result

        user_msg = self.ENTITY_MENTIONS_USER_TEMPLATE.format(question=question)
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"entity_mentions": [str]}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.ENTITY_MENTIONS_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
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
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_entity_mentions_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty entity-mentions response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: entity-mentions transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                mentions = parsed.get("entity_mentions", [])
                result.mentions = [str(m).strip() for m in mentions if str(m or "").strip()] \
                    if isinstance(mentions, list) else []
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner entity-mentions extraction failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: entity-mentions JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )
        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner entity-mentions extraction failed: %s", e)

        self.last_result = result
        return result

    # ------------------------------------------------------------------
    # Entity candidate verification -- disambiguate a guessed mention
    # against several retrieved graph candidates (see
    # EntityCandidateVerification's docstring).
    # ------------------------------------------------------------------

    ENTITY_VERIFICATION_SYSTEM_PROMPT = """You are given a guessed entity name and a numbered list of \
candidates retrieved from a knowledge graph by similarity search. Decide which candidate (if any) is \
actually the SAME real-world entity as the guessed name.

Rules:
1. The guessed name and the correct candidate's label don't have to match exactly -- they might use a \
different spelling, abbreviation, alternate title, or the candidate's type/label might disambiguate \
which one of several similarly-named options is right.
2. Pick the candidate that identifies the same real-world entity/value, not merely a topically related \
one.
3. If NONE of the candidates are actually the guessed entity, set "chosen_index" to null -- do not \
pick the closest-sounding one just because something must be chosen."""

    ENTITY_VERIFICATION_USER_TEMPLATE = """Guessed entity: {mention}

Candidates (numbered from 0):
{candidates}

Which candidate index is the same real-world entity as the guessed entity? null if none are."""

    @staticmethod
    def _parse_entity_verification_response(content) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            if "chosen_index" not in payload:
                return None
            value = payload["chosen_index"]
            if value is not None and not isinstance(value, int):
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

    def verify_entity_candidate(self, mention: str, candidates: list[str]) -> EntityCandidateVerification:
        """Ask which (if any) of ``candidates`` (already-formatted label/
        type strings, one per retrieved graph node) is the same real-world
        entity as the guessed ``mention``. Returns ``chosen_index`` into
        ``candidates``, or None if none match."""
        start = time.time()
        result = EntityCandidateVerification()

        if not mention or not mention.strip() or not candidates:
            result.error = "empty mention or candidates"
            self.last_result = result
            return result

        candidates_text = "\n".join(f"[{i}] {c}" for i, c in enumerate(candidates))
        user_msg = self.ENTITY_VERIFICATION_USER_TEMPLATE.format(mention=mention, candidates=candidates_text)
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"chosen_index": int_or_null}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.ENTITY_VERIFICATION_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
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
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_entity_verification_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty entity-verification response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: entity-verification transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                chosen_index = parsed.get("chosen_index")
                result.chosen_index = chosen_index if isinstance(chosen_index, int) and 0 <= chosen_index < len(candidates) else None
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner entity-verification failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: entity-verification JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )
        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner entity-verification failed: %s", e)

        self.last_result = result
        return result

    # ------------------------------------------------------------------
    # Entity description summarization -- borrowed from GraphRAG's own
    # construction pipeline (see EntityDescriptionResult's docstring).
    # ------------------------------------------------------------------

    ENTITY_DESCRIPTION_SYSTEM_PROMPT = """You summarize what a knowledge-graph entity is, given its \
name/type and a few evidence sentences it was extracted from. Write ONE short description (1-3 \
sentences) that would let someone recognize this entity from its name alone -- not just restating \
the name, but capturing what/who it is and its most identifying facts from the evidence.

Rules:
1. Base the description ONLY on the given evidence -- never invent facts not supported by it.
2. If the evidence is thin or generic, write a shorter description rather than padding it with \
speculation.
3. Do not mention "the evidence" or "the sentences" -- write as a standalone description of the \
entity itself."""

    ENTITY_DESCRIPTION_USER_TEMPLATE = """Entity: {label} (type: {entity_type})

Evidence:
{evidence}

Write a short description of this entity."""

    @staticmethod
    def _parse_entity_description_response(content) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            description = payload.get("description")
            if not isinstance(description, str) or not description.strip():
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

    def summarize_entity_description(self, label: str, entity_type: str, evidence: list[str]) -> EntityDescriptionResult:
        """Summarize ``evidence`` (already-extracted sentences this entity
        appeared in, across every question/passage it was found in) into a
        short description -- see ``EntityDescriptionResult``'s docstring
        for why this exists and how it's used."""
        start = time.time()
        result = EntityDescriptionResult()

        if not label or not label.strip():
            result.error = "empty label"
            self.last_result = result
            return result

        evidence_text = "\n".join(f"- {e}" for e in evidence if e and e.strip()) or "(no evidence given)"
        user_msg = self.ENTITY_DESCRIPTION_USER_TEMPLATE.format(
            label=label, entity_type=entity_type or "Entity", evidence=evidence_text,
        )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"description": str}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.ENTITY_DESCRIPTION_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
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
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_entity_description_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty entity-description response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: entity-description transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                result.description = str(parsed.get("description", "")).strip()
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner entity-description failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: entity-description JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )
        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner entity-description failed: %s", e)

        self.last_result = result
        return result

    # ------------------------------------------------------------------
    # Question decomposition + partial-answer merge -- borrowed from
    # StepChain GraphRAG (see QuestionDecomposition/MergedAnswerResult's
    # docstrings for why).
    # ------------------------------------------------------------------

    DECOMPOSE_QUESTION_SYSTEM_PROMPT = """You split a question into independent logical sub-questions \
for a graph-traversal QA system, using your own general knowledge -- you are not given any source \
text to read.

Rules:
1. Each sub-question must be answerable by starting from ONE named entity and following its own \
relation(s) -- e.g. "Which piece did Ludwig van Beethoven publish in 1801 that was dedicated to \
Count Moritz von Fries?" splits into ["What did Ludwig van Beethoven publish in 1801?", "What work \
was dedicated to Count Moritz von Fries?"] -- one sub-question per named clue, not per word.
2. If the question is ALREADY atomic -- it names (or describes) only ONE entity, with a single \
hop or chain of hops all starting from that same entity -- return a single-item list containing \
the ORIGINAL question completely unchanged. Do not force a split that doesn't exist.
3. A comparison question ("which was founded first, X or Y?", "did X and Y both...") splits into \
one sub-question per named entity, each asking the same underlying question about just that one \
entity (e.g. "When was X founded?" / "When was Y founded?").
4. Never invent a sub-question about an entity the original question doesn't name or describe.
5. Preserve enough of the original question's own wording in each sub-question that it stays \
answerable on its own, without needing the other sub-questions for context."""

    DECOMPOSE_QUESTION_USER_TEMPLATE = """Question: {question}

Split this question into independent sub-questions (or return it unchanged as a single-item list \
if it is already atomic)."""

    @staticmethod
    def _parse_decomposition_response(content) -> list | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            sub_questions = payload.get("sub_questions")
            if not isinstance(sub_questions, list) or not sub_questions:
                return None
            return sub_questions

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

    def decompose_question(self, question: str) -> QuestionDecomposition:
        """Split ``question`` into independent sub-questions (see
        QuestionDecomposition's docstring). An already-atomic question comes
        back as a single-item list containing itself unchanged."""
        start = time.time()
        result = QuestionDecomposition()

        if not question or not question.strip():
            result.error = "empty question"
            self.last_result = result
            return result

        user_msg = self.DECOMPOSE_QUESTION_USER_TEMPLATE.format(question=question)
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"sub_questions": [str]}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.DECOMPOSE_QUESTION_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
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
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_decomposition_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty decomposition response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: decomposition transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                result.sub_questions = [str(q).strip() for q in parsed if str(q).strip()]
                if not result.sub_questions:
                    result.sub_questions = [question]
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner question decomposition failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: decomposition JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )
        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner question decomposition failed: %s", e)

        self.last_result = result
        return result

    MERGE_PARTIAL_ANSWERS_SYSTEM_PROMPT = """You synthesize a final answer to a question from a set \
of partial findings, each derived independently from one sub-question of that same original \
question.

Rules:
1. Use the original question to decide what the final answer should actually address -- the \
sub-questions were only a means of gathering evidence, not necessarily phrased the way the final \
answer should be.
2. When a sub-question has no partial answer (its entities were never found), work from whichever \
partial answers ARE available rather than refusing to answer.
3. If the partial answers conflict or don't fully add up to a confident final answer, give your \
best answer from what's available and say so in your reasoning -- never leave "answer" empty.
4. Never invent a fact not present in any of the given partial answers/reasoning."""

    MERGE_PARTIAL_ANSWERS_USER_TEMPLATE = """Original question: {question}

Partial findings from each sub-question:
{sub_answers}

Synthesize the final answer to the original question."""

    @staticmethod
    def _format_sub_answers(sub_answers: list[dict]) -> str:
        lines = []
        for entry in sub_answers:
            lines.append(f'Sub-question: "{entry.get("sub_question", "")}"')
            answer = entry.get("answer")
            if answer:
                lines.append(f"  Answer: {answer}")
                reasoning = entry.get("reasoning") or ""
                if reasoning:
                    lines.append(f"  Reasoning: {reasoning}")
            else:
                lines.append("  Answer: (could not be determined)")
        return "\n".join(lines)

    @staticmethod
    def _parse_merge_response(content) -> dict | None:
        if not isinstance(content, str):
            return None

        def valid(payload):
            if not isinstance(payload, dict):
                return None
            if not isinstance(payload.get("answer"), str) or not payload["answer"]:
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

    def merge_partial_answers(self, question: str, sub_answers: list[dict]) -> MergedAnswerResult:
        """Synthesize a final answer from each sub-question's own partial
        answer (see MergedAnswerResult's docstring). ``sub_answers`` is a
        list of ``{"sub_question": str, "answer": str | None, "reasoning":
        str}`` -- an entry with ``answer=None`` (that sub-question's
        entities never resolved) is still included so the model knows a gap
        exists, rather than silently omitted."""
        start = time.time()
        result = MergedAnswerResult()

        if not question or not question.strip() or not sub_answers:
            result.error = "empty question or sub_answers"
            self.last_result = result
            return result

        user_msg = self.MERGE_PARTIAL_ANSWERS_USER_TEMPLATE.format(
            question=question,
            sub_answers=self._format_sub_answers(sub_answers),
        )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"answer": str, "reasoning": str}'
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

        parsed = None
        raw_contents: list[str] = []
        raw_content = ""
        finish_reason = ""
        last_exception: Exception | None = None
        max_attempts = 1 + self._empty_response_retry_count() + self._transient_error_retry_count()

        for attempt in range(1, max_attempts + 1):
            try:
                future = self._executor.submit(
                    completion,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.MERGE_PARTIAL_ANSWERS_SYSTEM_PROMPT + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
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
                finish_reason = ""
                if raw_response and raw_response.choices:
                    choice = raw_response.choices[0]
                    msg = choice.message
                    raw_finish_reason = getattr(choice, "finish_reason", "")
                    if isinstance(raw_finish_reason, str):
                        finish_reason = raw_finish_reason
                    raw_contents = self._response_text_candidates(msg)

                last_exception = None
                raw_content = raw_contents[0] if raw_contents else ""
                for candidate in raw_contents:
                    parsed = self._parse_merge_response(candidate)
                    if parsed is not None:
                        raw_content = candidate
                        break
                if parsed is not None:
                    break
                if raw_contents or attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: empty merge response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )
            except Exception as e:
                last_exception = e
                if attempt == max_attempts:
                    break
                logger.warning(
                    "LLM planner: merge transient error (%s) on attempt %d/%d, retrying: %s",
                    self.classify_error(str(e)), attempt, max_attempts, e,
                )
                time.sleep(self._retry_backoff_seconds())

        try:
            if parsed:
                result.answer = str(parsed["answer"])
                result.reasoning = str(parsed.get("reasoning", "") or "")
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            elif last_exception is not None:
                result.error = str(last_exception)
                result.error_type = self.classify_error(result.error)
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("LLM planner merge failed after retries: %s", last_exception)
            else:
                result.error = self._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "LLM planner: merge JSON invalid/parse failed, content[:200]=%r", raw_content[:200]
                )
        except Exception as e:
            result.error = str(e)
            result.error_type = self.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("LLM planner merge failed: %s", e)

        self.last_result = result
        return result
