"""Passage-only LLM answerer for the HotpotQA benchmark.

Unlike ``llm_planner.LLMPlanner`` (which maps a question to *relation keys*
in a graph schema), this module answers the question directly from the raw
Wikipedia paragraphs HotpotQA already provides -- no graph tenant, no
center_node, no ``ReasoningEngine`` involvement. It otherwise follows the
exact same call/retry/error-classification shape as ``LLMPlanner`` so it
plugs into the existing benchmark-runner conventions.

Usage:
    from hotpotqa_passage_qa import HotpotQAAnswerer
    answerer = HotpotQAAnswerer()
    result = answerer.answer_question(question, context)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from llm_planner import LLMPlanner

logger = logging.getLogger("HotpotQAAnswerer")


@dataclass
class AnswererResult:
    """Result of answering one HotpotQA question from its own passages."""
    answer: str = ""
    supporting_titles: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    token_usage: int = 0
    model: str = ""
    used_fallback: bool = False
    error: str = ""
    error_type: str = ""


DEFAULT_SYSTEM_PROMPT = """You are a careful reading-comprehension assistant. You are given a \
question and a set of numbered Wikipedia paragraphs. Answer using ONLY the given paragraphs \
-- never outside knowledge.

Rules:
1. If the question is a yes/no question, answer exactly "yes" or "no".
2. Otherwise, answer with the shortest exact phrase copied from the paragraphs that answers \
the question (a name, date, place, or short noun phrase) -- not a full sentence.
3. List the titles of the paragraphs you actually used as evidence.
4. If the paragraphs do not contain the answer, give your best guess from the closest \
paragraph rather than leaving the answer empty."""

DEFAULT_USER_TEMPLATE = """Question: {question}

Paragraphs:
{passages}

Answer the question using only the paragraphs above."""


def _format_passages(context: list[list[Any]], max_passages: int = 12) -> str:
    lines = []
    for i, (title, sentences) in enumerate(context[:max_passages], start=1):
        body = " ".join(str(s).strip() for s in sentences)
        lines.append(f"[{i}] {title}: {body}")
    return "\n".join(lines)


class HotpotQAAnswerer:
    """LLM-based passage-only answerer for HotpotQA questions.

    Model resolution, retry-on-empty-response, and error classification are
    delegated to ``LLMPlanner`` (imported, not duplicated) so both benchmarks
    share one place to configure the LLM (``ALETHEIA_LLM_PLANNER_MODEL`` and
    friends).
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
        self.last_result: AnswererResult | None = None

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
            if not isinstance(payload.get("answer"), str):
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

    def answer_question(self, question: str, context: list[list[Any]]) -> AnswererResult:
        """Answer a HotpotQA question from its own gold+distractor passages."""
        start = time.time()
        result = AnswererResult()

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
            passages=_format_passages(context),
        )
        json_instruction = (
            "\n\nRespond with ONLY a JSON object in this exact format "
            "(no markdown, no extra text):\n"
            '{"answer": str, "supporting_titles": [str]}'
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
                raw_response = completion(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt + json_instruction},
                        {"role": "user", "content": user_msg},
                    ],
                    timeout=self.timeout,
                    temperature=self.temperature,
                    **self._completion_kwargs(),
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
                logger.warning(
                    "HotpotQA answerer: empty response, retrying attempt %d/%d",
                    attempt + 1, max_attempts,
                )

            if parsed:
                result.answer = str(parsed.get("answer", "")).strip()
                titles = parsed.get("supporting_titles", [])
                result.supporting_titles = [str(t) for t in titles] if isinstance(titles, list) else []
                result.model = self.model
                result.latency_ms = (time.time() - start) * 1000
            else:
                result.error = LLMPlanner._parse_failure_message(raw_contents, finish_reason)
                result.error_type = "runtime_invalid"
                result.used_fallback = True
                result.latency_ms = (time.time() - start) * 1000
                logger.warning(
                    "HotpotQA answerer: JSON parse failed, content[:200]=%r", raw_content[:200]
                )

        except Exception as e:
            result.error = str(e)
            result.error_type = LLMPlanner.classify_error(result.error)
            result.used_fallback = True
            result.latency_ms = (time.time() - start) * 1000
            logger.warning("HotpotQA answerer failed: %s", e)

        self.last_result = result
        return result
