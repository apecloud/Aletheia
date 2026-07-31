"""LLM judge for whether a graph traversal's retrieved facts support a gold answer.

Plain substring matching (the original ``graph_hit``) produces false
negatives on name variants that are the same entity but not a literal
substring of each other -- e.g. label "Joseph Campbell" vs gold "Joseph John
Campbell" (middle name inserted), or "Cook's Landing Place, Town of Seventeen
Seventy" vs gold "Captain Cook's Landing Place" (different prefix/suffix).
This asks an LLM instead: given the question, the gold answer, and the facts
actually retrieved from the graph traversal, does any fact identify the same
entity as the gold answer (accounting for name variants/abbreviations), or
merely a topically related one?

Modeled directly on ``HotpotQAAnswerer``/``HotpotQARelationExtractor``: same
call/retry/hard-timeout/error-classification shape, reusing
``LLMPlanner`` for model resolution rather than duplicating it.
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

logger = logging.getLogger("GraphHitJudge")


@dataclass
class JudgeResult:
    hit: bool = False
    matched_label: str = ""
    reasoning: str = ""
    latency_ms: float = 0.0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


DEFAULT_SYSTEM_PROMPT = """You judge whether a knowledge-graph search found the correct answer to a question.

You are given: the question, the gold (correct) answer, and a list of facts actually retrieved by \
graph traversal (each a "label" -- an entity or attribute value -- and the "relation" that reached it).

Rules:
1. Answer hit=true if ANY retrieved label identifies the SAME real-world entity/value as the gold \
answer -- even if the wording differs (a shorter/longer name, a middle name present in one but not \
the other, an abbreviation, a title prefix like "Captain", reordered words, etc.).
2. Also answer hit=true if the gold answer describes a RELATIONSHIP or CATEGORY rather than an \
entity, and the fact's "relation" (not its label) expresses that same relationship/category -- e.g. \
gold="brother" matches a fact whose relation is "is_older_brother_of" even though that fact's label \
is the *other* person's name, not the word "brother".
3. A label that is a Wikipedia-style meta-title built from an entity's name (e.g. "X filmography", \
"X discography", "List of awards received by X") identifies that entity X itself.
4. Answer hit=false if every retrieved label/relation is merely topically related but is NOT the same \
entity/value/relationship as the gold answer (e.g. a different person, a different date, a different \
place).
5. If there are no retrieved facts at all, hit=false.
6. Set matched_label to the exact retrieved label you judged as the hit (empty string if hit=false)."""

DEFAULT_USER_TEMPLATE = """Question: {question}
Gold answer: {gold_answer}

Retrieved facts (from graph traversal, starting at the question's topic entity), shown as \
"relation: label":
{facts}

Does any retrieved fact (its label, or its relation, per the rules) identify the same entity/value/\
relationship as the gold answer?"""


def _format_facts(facts: list[dict[str, str]], max_facts: int = 30) -> str:
    if not facts:
        return "(none)"
    lines = []
    for i, fact in enumerate(facts[:max_facts], start=1):
        rel = fact.get("rel", "")
        label = fact.get("label", "")
        lines.append(f"[{i}] {rel}: {label}" if rel else f"[{i}] {label}")
    return "\n".join(lines)


class GraphHitJudge:
    """LLM judge for graph-traversal hit/miss, robust to name-variant wording."""

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
        self.last_result: JudgeResult | None = None
        # Same hard-timeout rationale as HotpotQARelationExtractor: litellm's
        # own `timeout=` kwarg doesn't reliably fire on a stuck proxy/SOCKS
        # CONNECT tunnel.
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
            if not isinstance(payload.get("hit"), bool):
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

    def judge(self, question: str, gold_answer: str, facts: list[dict[str, str]]) -> JudgeResult:
        """Judge whether any retrieved fact identifies the gold answer's entity/value."""
        start = time.time()
        result = JudgeResult()

        if not gold_answer:
            self.last_result = result
            return result
        if not facts:
            self.last_result = result
            return result

        user_msg = self.user_template.format(
            question=question,
            gold_answer=gold_answer,
            facts=_format_facts(facts),
        )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"hit": bool, "matched_label": str, "reasoning": str}'
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
                logger.warning("GraphHitJudge: empty response, retrying attempt %d/%d", attempt + 1, max_attempts)

            if parsed:
                result.hit = bool(parsed.get("hit", False))
                result.matched_label = str(parsed.get("matched_label", "") or "")
                result.reasoning = str(parsed.get("reasoning", "") or "")
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            else:
                result.error = LLMPlanner._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning("GraphHitJudge: JSON parse failed, content[:200]=%r", raw_content[:200])

        except Exception as e:
            result.error = str(e)
            result.error_type = LLMPlanner.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("GraphHitJudge failed: %s", e)

        self.last_result = result
        return result
