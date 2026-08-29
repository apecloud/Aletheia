#!/usr/bin/env python3
"""WebQSP public benchmark for Aletheia question-path planner.

Loads 100 WebQSP questions (converted to Aletheia tenant format), runs
_plan_question_paths on each, and evaluates Hit@1 / F1 against gold answer
relation paths. Compares results with published SOTA (OntGQA WebQSP Hit@1=91.5).

The benchmark tests planner-layer quality: does the planner correctly select
which relation types and entity types are relevant for each question?

Run: python -m unittest tests.test_webqsp_benchmark
"""

from __future__ import annotations

import json
import os
import statistics
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from aletheia.reasoning.engine import ReasoningEngine


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = ROOT / "benchmarks" / "webqsp" / "webqsp_aletheia_benchmark.json"
DEFAULT_CACHE_PATH = ROOT / "tmp" / "webqsp_planner_cache.json"
CACHE_VERSION = "webqsp-planner-cache-v2"

if BENCHMARK_PATH.exists():
    with BENCHMARK_PATH.open() as f:
        WEBQSP_DATA = json.load(f)
else:
    WEBQSP_DATA = []


# ---------------------------------------------------------------------------
# Evaluation metrics (same definitions as maritime-risk benchmark)
# ---------------------------------------------------------------------------

def evaluate_hit_at_1(predicted: set[str], gold: set[str]) -> bool:
    """Hit@1: True if any predicted link key matches a gold link key."""
    return bool(predicted & gold)


def evaluate_f1(predicted: set[str], gold: set[str]) -> float:
    """F1 over link key sets."""
    if not predicted and not gold:
        return 1.0
    tp = len(predicted & gold)
    fp = len(predicted - gold)
    fn = len(gold - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ---------------------------------------------------------------------------
# Fake repo (no DB needed for planner-layer evaluation)
# ---------------------------------------------------------------------------

class FakeRepo:
    """Minimal repo stub so ReasoningEngine can be constructed without a DB."""
    pass


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def _relation_set_hash(
    link_config: list[dict[str, Any]],
    descriptions: dict[str, str],
) -> str:
    """Stable hash for the relation candidates presented to the planner."""
    payload = []
    for lc in sorted(link_config, key=lambda item: item.get("link", "")):
        link_key = lc.get("link", "")
        payload.append({
            "link": link_key,
            "from": lc.get("from", ""),
            "to": lc.get("to", ""),
            "description": descriptions.get(link_key, ""),
        })
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _cache_key(q: dict[str, Any]) -> str:
    """Cache key: question + topic type + relation set hash."""
    payload = {
        "version": CACHE_VERSION,
        "question": q["question"],
        "topic_type": q["topic_type"],
        "relation_set_hash": _relation_set_hash(q["link_config"], q["descriptions"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def benchmark_limit(default: int | None = None) -> int | None:
    """Read optional WEBQSP_BENCHMARK_LIMIT for 10/20/100-question subsets."""
    raw = os.environ.get("WEBQSP_BENCHMARK_LIMIT", "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def benchmark_progress_every(default: int = 10) -> int:
    """Read optional WEBQSP_BENCHMARK_PROGRESS_EVERY progress interval."""
    raw = os.environ.get("WEBQSP_BENCHMARK_PROGRESS_EVERY", "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def benchmark_cache_path(default: Path = DEFAULT_CACHE_PATH) -> Path | None:
    """Read optional WEBQSP_BENCHMARK_CACHE_PATH.

    Set WEBQSP_BENCHMARK_CACHE_PATH=off to disable cache reads and writes.
    Without an explicit path, cache is enabled only for LLM benchmark runs.
    """
    raw = os.environ.get("WEBQSP_BENCHMARK_CACHE_PATH", "")
    if raw.lower() in {"0", "false", "no", "off"}:
        return None
    if raw:
        return Path(raw)
    llm_enabled = os.environ.get("ALETHEIA_LLM_PLANNER_ENABLED", "").lower()
    if llm_enabled in {"1", "true", "yes"}:
        return default
    return None


def benchmark_fail_fast_on_provider_error(default: bool = True) -> bool:
    """Read optional WEBQSP_BENCHMARK_FAIL_FAST_ON_PROVIDER_ERROR."""
    raw = os.environ.get("WEBQSP_BENCHMARK_FAIL_FAST_ON_PROVIDER_ERROR", "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def benchmark_fail_fast_on_runtime_invalid(default: bool = True) -> bool:
    """Read optional WEBQSP_BENCHMARK_FAIL_FAST_ON_RUNTIME_INVALID."""
    raw = os.environ.get("WEBQSP_BENCHMARK_FAIL_FAST_ON_RUNTIME_INVALID", "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


@dataclass
class BenchmarkTiming:
    """Latency/error counters for one benchmark run."""
    latencies_ms: list[float] = field(default_factory=list)
    timeout_count: int = 0
    error_count: int = 0
    provider_error_count: int = 0
    runtime_invalid_count: int = 0
    fallback_count: int = 0

    def record(
        self,
        elapsed_ms: float,
        error: str = "",
        error_type: str = "",
        used_fallback: bool = False,
    ) -> None:
        self.latencies_ms.append(elapsed_ms)
        if used_fallback:
            self.fallback_count += 1
        if error:
            self.error_count += 1
            if error_type == "provider":
                self.provider_error_count += 1
            if error_type == "runtime_invalid":
                self.runtime_invalid_count += 1
            if error_type == "timeout" or "timeout" in error.lower() or "timed out" in error.lower():
                self.timeout_count += 1

    def summary(self) -> dict[str, float | int]:
        values = sorted(self.latencies_ms)
        if not values:
            return {
                "avg_ms": 0.0,
                "p50_ms": 0.0,
                "p95_ms": 0.0,
                "max_ms": 0.0,
                "timeout_count": self.timeout_count,
                "error_count": self.error_count,
                "provider_error_count": self.provider_error_count,
                "runtime_invalid_count": self.runtime_invalid_count,
                "fallback_count": self.fallback_count,
            }
        p95_index = min(len(values) - 1, int(round((len(values) - 1) * 0.95)))
        return {
            "avg_ms": statistics.fmean(values),
            "p50_ms": statistics.median(values),
            "p95_ms": values[p95_index],
            "max_ms": values[-1],
            "timeout_count": self.timeout_count,
            "error_count": self.error_count,
            "provider_error_count": self.provider_error_count,
            "runtime_invalid_count": self.runtime_invalid_count,
            "fallback_count": self.fallback_count,
        }


class PlannerResultCache:
    """JSON cache for question/relation-set to selected planner output."""

    def __init__(self, path: Path | None):
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        self.dirty = False
        self.hits = 0
        self.misses = 0
        if self.path and self.path.exists():
            with self.path.open() as f:
                payload = json.load(f)
            if payload.get("version") == CACHE_VERSION:
                self.entries = payload.get("entries", {})

    def get(self, key: str) -> dict[str, Any] | None:
        cached = self.entries.get(key)
        if cached is None:
            self.misses += 1
            return None
        self.hits += 1
        return cached

    def put(self, key: str, value: dict[str, Any]) -> None:
        self.entries[key] = value
        self.dirty = True

    def save(self) -> None:
        if not self.path or not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "entries": self.entries,
        }
        with self.path.open("w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    def summary(self) -> dict[str, int | str]:
        return {
            "path": str(self.path) if self.path else "disabled",
            "hits": self.hits,
            "misses": self.misses,
            "entries": len(self.entries),
        }


class WebQSPBenchmarkRunner:
    """Reusable WebQSP planner benchmark with cache, subsets, and progress."""

    def __init__(
        self,
        engine: ReasoningEngine,
        cache_path: Path | None = None,
        progress_every: int = 10,
        emit_progress: bool = True,
        fail_fast_on_provider_error: bool = True,
        fail_fast_on_runtime_invalid: bool = True,
    ):
        self.engine = engine
        self.cache = PlannerResultCache(cache_path)
        self.progress_every = max(0, progress_every)
        self.emit_progress = emit_progress
        self.timing = BenchmarkTiming()
        self.fail_fast_on_provider_error = fail_fast_on_provider_error
        self.fail_fast_on_runtime_invalid = fail_fast_on_runtime_invalid
        self.aborted = False
        self.abort_reason = ""

    def _empty_plan_payload(self) -> dict[str, Any]:
        return {
            "selected_link_keys": [],
            "selected_target_types": [],
            "admissible_chains": [],
            "is_full_aggregation": True,
            "keyword_link_keys": [],
            "llm_link_keys": [],
            "llm_ranked_link_keys": [],
            "llm_confidence_scores": {},
            "selected_after_convergence_keys": [],
            "llm_confidence_filtered_link_keys": [],
            "llm_truncated_link_keys": [],
            "llm_top_k_link_keys": [],
            "planner_selection_sources": {},
            "planner_convergence_config": {},
            "llm_convergence_applied": False,
        }

    def _plan_payload(self, plan: ReasoningEngine.QuestionPathPlan) -> dict[str, Any]:
        payload = self._empty_plan_payload()
        payload.update({
            "selected_link_keys": sorted(plan.selected_link_keys),
            "selected_target_types": sorted(plan.selected_target_types),
            "admissible_chains": [list(chain) for chain in plan.admissible_chains],
            "is_full_aggregation": plan.is_full_aggregation,
            "keyword_link_keys": sorted(plan.keyword_link_keys),
            "llm_link_keys": sorted(plan.llm_link_keys),
            "llm_ranked_link_keys": list(plan.llm_ranked_link_keys),
            "llm_confidence_scores": dict(plan.llm_confidence_scores),
            "selected_after_convergence_keys": list(plan.selected_after_convergence_keys),
            "llm_confidence_filtered_link_keys": list(plan.llm_confidence_filtered_link_keys),
            "llm_truncated_link_keys": list(plan.llm_truncated_link_keys),
            "llm_top_k_link_keys": list(plan.llm_top_k_link_keys),
            "planner_selection_sources": {
                key: list(sources)
                for key, sources in plan.planner_selection_sources.items()
            },
            "planner_convergence_config": dict(plan.planner_convergence_config),
            "llm_convergence_applied": bool(plan.llm_convergence_applied),
        })
        return payload

    def _selection_reason_by_key(self, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        reasons = {}
        keyword_keys = set(payload.get("keyword_link_keys") or [])
        llm_ranked = list(payload.get("llm_ranked_link_keys") or [])
        top_k_keys = set(payload.get("llm_top_k_link_keys") or [])
        source_map = payload.get("planner_selection_sources") or {}
        confidence_scores = payload.get("llm_confidence_scores") or {}
        for key in payload.get("selected_link_keys") or []:
            sources = list(source_map.get(key) or [])
            if key in keyword_keys and "keyword" not in sources:
                sources.append("keyword")
            reason = "selected_by_" + "+".join(sources) if sources else "selected_by_fallback"
            reasons[key] = {
                "sources": sources,
                "reason": reason,
                "llm_rank": llm_ranked.index(key) + 1 if key in llm_ranked else None,
                "confidence": confidence_scores.get(key),
                "in_llm_top_k": key in top_k_keys,
                "added_by_keyword": key in keyword_keys,
            }
        return reasons

    def _gold_link_diagnostics(
        self,
        payload: dict[str, Any],
        gold_keys: set[str],
    ) -> dict[str, dict[str, Any]]:
        selected = set(payload.get("selected_link_keys") or [])
        ranked = list(payload.get("llm_ranked_link_keys") or [])
        top_k = set(payload.get("llm_top_k_link_keys") or [])
        truncated = set(payload.get("llm_truncated_link_keys") or [])
        confidence_filtered = set(payload.get("llm_confidence_filtered_link_keys") or [])
        selected_after_convergence = set(payload.get("selected_after_convergence_keys") or [])
        keyword = set(payload.get("keyword_link_keys") or [])
        diagnostics = {}
        for key in sorted(gold_keys):
            if key in selected:
                status = "selected"
            elif key in confidence_filtered:
                status = "filtered_by_confidence"
            elif key in truncated:
                status = "truncated_by_top_k"
            elif key in ranked:
                status = "ranked_but_not_selected"
            else:
                status = "absent_from_llm_rank"
            diagnostics[key] = {
                "status": status,
                "llm_rank": ranked.index(key) + 1 if key in ranked else None,
                "in_selected_link_keys": key in selected,
                "in_selected_after_convergence_keys": key in selected_after_convergence,
                "in_llm_top_k": key in top_k,
                "in_llm_truncated": key in truncated,
                "filtered_by_confidence": key in confidence_filtered,
                "in_keyword_link_keys": key in keyword,
            }
        return diagnostics

    def _top_k_truncation_evidence(
        self,
        payload: dict[str, Any],
        gold_keys: set[str],
    ) -> dict[str, Any]:
        truncated = set(payload.get("llm_truncated_link_keys") or [])
        confidence_filtered = set(payload.get("llm_confidence_filtered_link_keys") or [])
        ranked = set(payload.get("llm_ranked_link_keys") or [])
        selected = set(payload.get("selected_link_keys") or [])
        return {
            "top_k": payload.get("planner_convergence_config", {}).get("top_k"),
            "truncated_link_keys": list(payload.get("llm_truncated_link_keys") or []),
            "gold_truncated_link_keys": sorted(gold_keys & truncated),
            "gold_filtered_by_confidence_keys": sorted(gold_keys & confidence_filtered),
            "gold_absent_from_llm_rank_keys": sorted(gold_keys - ranked),
            "gold_selected_link_keys": sorted(gold_keys & selected),
        }

    def _plan_payload_from_cache_or_engine(
        self,
        q: dict[str, Any],
    ) -> tuple[dict[str, Any], bool, float, dict[str, Any]]:
        key = _cache_key(q)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, True, 0.0, {
                "error": "",
                "error_type": "",
                "used_fallback": False,
            }

        planner_before = self._latest_llm_result()
        start = time.monotonic()
        plan = self.engine._plan_question_paths(
            q["question"],
            q["topic_type"],
            q["entity_config"],
            q["link_config"],
            q["descriptions"],
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        planner_error = self._planner_error_since(planner_before)
        payload = self._plan_payload(plan)
        if not planner_error["error"]:
            self.cache.put(key, payload)
        return payload, False, elapsed_ms, planner_error

    def _latest_llm_result(self) -> Any:
        planner = getattr(self.engine, "_llm_planner", None)
        return getattr(planner, "last_result", None)

    def _planner_error_since(self, previous_result: Any) -> dict[str, Any]:
        current_result = self._latest_llm_result()
        if current_result is None or current_result is previous_result:
            return {"error": "", "error_type": "", "used_fallback": False}
        error = getattr(current_result, "error", "") or ""
        used_fallback = bool(getattr(current_result, "used_fallback", False))
        error_type = getattr(current_result, "error_type", "") or ""
        if not error_type and error:
            try:
                from aletheia.llms.planner import LLMPlanner
                error_type = LLMPlanner.classify_error(error)
            except Exception:
                error_type = "runtime"
        return {
            "error": error if used_fallback else "",
            "error_type": error_type if used_fallback else "",
            "used_fallback": used_fallback,
        }

    def run_question(self, q: dict[str, Any]) -> dict[str, Any]:
        """Run planner on a single WebQSP question and evaluate."""
        error = ""
        error_type = ""
        used_fallback = False
        try:
            payload, cache_hit, elapsed_ms, error_info = self._plan_payload_from_cache_or_engine(q)
            error = str(error_info.get("error", "") or "")
            error_type = str(error_info.get("error_type", "") or "")
            used_fallback = bool(error_info.get("used_fallback", False))
        except Exception as exc:
            elapsed_ms = 0.0
            cache_hit = False
            error = str(exc)
            error_type = "runtime"
            payload = self._empty_plan_payload()
        self.timing.record(
            elapsed_ms,
            error=error,
            error_type=error_type,
            used_fallback=used_fallback,
        )
        predicted_keys = set(payload["selected_link_keys"])
        gold_keys = set(q["gold_link_keys"])
        hit1 = evaluate_hit_at_1(predicted_keys, gold_keys)
        f1 = evaluate_f1(predicted_keys, gold_keys)
        return {
            "qid": q["qid"],
            "question": q["question"],
            "topic_type": q["topic_type"],
            "hop_count": q["hop_count"],
            "hit1": hit1,
            "f1": f1,
            "predicted_keys": predicted_keys,
            "gold_keys": gold_keys,
            "selected_relation_count": len(predicted_keys),
            "is_full_aggregation": bool(payload["is_full_aggregation"]),
            "selected_target_types": set(payload["selected_target_types"]),
            "keyword_link_keys": set(payload["keyword_link_keys"]),
            "llm_link_keys": set(payload["llm_link_keys"]),
            "llm_ranked_link_keys": list(payload["llm_ranked_link_keys"]),
            "llm_confidence_scores": dict(payload["llm_confidence_scores"]),
            "selected_after_convergence_keys": list(payload["selected_after_convergence_keys"]),
            "llm_confidence_filtered_link_keys": list(payload["llm_confidence_filtered_link_keys"]),
            "llm_truncated_link_keys": list(payload["llm_truncated_link_keys"]),
            "llm_top_k_link_keys": list(payload["llm_top_k_link_keys"]),
            "planner_convergence_config": dict(payload["planner_convergence_config"]),
            "llm_convergence_applied": bool(payload["llm_convergence_applied"]),
            "selection_reason_by_key": self._selection_reason_by_key(payload),
            "gold_link_diagnostics": self._gold_link_diagnostics(payload, gold_keys),
            "top_k_truncation_evidence": self._top_k_truncation_evidence(payload, gold_keys),
            "latency_ms": elapsed_ms,
            "cache_hit": cache_hit,
            "error": error,
            "error_type": error_type,
            "used_fallback": used_fallback,
            "provider_error": error_type == "provider",
            "runtime_invalid": error_type == "runtime_invalid",
        }

    def serializable_result(self, result: dict[str, Any]) -> dict[str, Any]:
        """Convert one result row into a stable JSON report payload."""
        payload = dict(result)
        for key in (
            "predicted_keys",
            "gold_keys",
            "selected_target_types",
            "keyword_link_keys",
            "llm_link_keys",
        ):
            payload[key] = sorted(payload.get(key) or [])
        return payload

    def run(
        self,
        questions: list[dict[str, Any]],
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        subset = questions[:limit] if limit else questions
        total = len(subset)
        results = []
        try:
            for idx, q in enumerate(subset, start=1):
                result = self.run_question(q)
                results.append(result)
                if self.emit_progress and self.progress_every and (
                    idx == 1 or idx % self.progress_every == 0 or idx == total
                ):
                    state = "hit" if result["cache_hit"] else "miss"
                    print(
                        f"[WebQSP] {idx}/{total} qid={result['qid']} "
                        f"hit1={int(result['hit1'])} f1={result['f1']:.3f} "
                        f"latency_ms={result['latency_ms']:.0f} cache={state}"
                    )
                if self.fail_fast_on_provider_error and result["provider_error"]:
                    self.aborted = True
                    self.abort_reason = f"provider error at {result['qid']}: {result['error']}"
                    if self.emit_progress:
                        print(f"[WebQSP] abort: {self.abort_reason}")
                    break
                if self.fail_fast_on_runtime_invalid and result["runtime_invalid"]:
                    self.aborted = True
                    self.abort_reason = f"runtime-invalid LLM result at {result['qid']}: {result['error']}"
                    if self.emit_progress:
                        print(f"[WebQSP] abort: {self.abort_reason}")
                    break
        finally:
            self.cache.save()
        return results

    def report(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        quality_results = [
            r for r in results
            if not r.get("provider_error") and not r.get("runtime_invalid")
        ]
        total = len(quality_results)
        hit1_count = sum(1 for r in quality_results if r["hit1"])
        avg_f1 = sum(r["f1"] for r in quality_results) / total if total else 0.0
        full_agg_count = sum(1 for r in quality_results if r["is_full_aggregation"])
        provider_error_count = sum(1 for r in results if r.get("provider_error"))
        runtime_invalid_count = sum(1 for r in results if r.get("runtime_invalid"))
        fallback_count = sum(1 for r in results if r.get("used_fallback"))
        error_type_counts: dict[str, int] = {}
        for result in results:
            error_type = result.get("error_type") or ""
            if error_type:
                error_type_counts[error_type] = error_type_counts.get(error_type, 0) + 1
        return {
            "attempted_questions": len(results),
            "total_questions": total,
            "hit1_count": hit1_count,
            "hit1_rate": hit1_count / total if total else 0.0,
            "avg_f1": avg_f1,
            "full_aggregation_count": full_agg_count,
            "full_aggregation_rate": full_agg_count / total if total else 0.0,
            "provider_error_count": provider_error_count,
            "runtime_invalid_count": runtime_invalid_count,
            "fallback_count": fallback_count,
            "error_count": sum(1 for r in results if r.get("error")),
            "error_type_counts": error_type_counts,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "latency": self.timing.summary(),
            "cache": self.cache.summary(),
            "results": [self.serializable_result(result) for result in results],
        }


class BenchmarkRunnerInfrastructureTest(unittest.TestCase):
    """Deterministic tests for benchmark runtime controls."""

    def _sample_question(self) -> dict[str, Any]:
        return {
            "qid": "WebQTest-1",
            "question": "what is the nationality of this person?",
            "topic_type": "person",
            "hop_count": 1,
            "entity_config": {
                "person": {"table": "webqsp_person", "pk": "id"},
                "country": {"table": "webqsp_country", "pk": "id"},
            },
            "link_config": [
                {
                    "link": "person:n:m:people_person_nationality",
                    "from": "person",
                    "to": "country",
                }
            ],
            "descriptions": {
                "person:n:m:people_person_nationality": "the nationality of a person"
            },
            "gold_link_keys": ["person:n:m:people_person_nationality"],
        }

    def _question_with_links(self, links: list[str], gold: list[str] | None = None) -> dict[str, Any]:
        q = self._sample_question()
        q["link_config"] = [
            {"link": link, "from": "person", "to": "country"}
            for link in links
        ]
        q["descriptions"] = {link: link.replace("_", " ") for link in links}
        q["gold_link_keys"] = gold or [links[0]]
        return q

    def test_cache_key_changes_with_relation_descriptions(self):
        q1 = self._sample_question()
        q2 = json.loads(json.dumps(q1))
        q2["descriptions"]["person:n:m:people_person_nationality"] = "country of citizenship"
        self.assertNotEqual(_cache_key(q1), _cache_key(q2))

    def test_cache_key_uses_trace_version_for_config_compatibility(self):
        q = self._sample_question()
        key = _cache_key(q)
        self.assertEqual(CACHE_VERSION, "webqsp-planner-cache-v2")
        self.assertNotEqual(key, sha256(json.dumps({
            "version": "webqsp-planner-cache-v1",
            "question": q["question"],
            "topic_type": q["topic_type"],
            "relation_set_hash": _relation_set_hash(q["link_config"], q["descriptions"]),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_runner_persists_and_reuses_cache(self):
        class CountingEngine:
            def __init__(self, fail: bool = False):
                self.calls = 0
                self.fail = fail

            def _plan_question_paths(self, *args):
                self.calls += 1
                if self.fail:
                    raise AssertionError("engine should not be called on cache hit")
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys={"person:n:m:people_person_nationality"},
                    selected_target_types={"country"},
                    is_full_aggregation=False,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            q = self._sample_question()

            first_engine = CountingEngine()
            first_runner = WebQSPBenchmarkRunner(
                first_engine,
                cache_path=cache_path,
                emit_progress=False,
            )
            first_result = first_runner.run([q])[0]
            self.assertFalse(first_result["cache_hit"])
            self.assertTrue(first_result["hit1"])
            self.assertEqual(first_engine.calls, 1)
            self.assertTrue(cache_path.exists())

            second_engine = CountingEngine(fail=True)
            second_runner = WebQSPBenchmarkRunner(
                second_engine,
                cache_path=cache_path,
                emit_progress=False,
            )
            second_result = second_runner.run([q])[0]
            self.assertTrue(second_result["cache_hit"])
            self.assertTrue(second_result["hit1"])
            self.assertEqual(second_engine.calls, 0)

    def test_runner_ignores_old_cache_version_without_trace_fields(self):
        class StaticEngine:
            def __init__(self):
                self.calls = 0

            def _plan_question_paths(self, *args):
                self.calls += 1
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys={"person:n:m:people_person_nationality"},
                    selected_target_types={"country"},
                    is_full_aggregation=False,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text(json.dumps({
                "version": "webqsp-planner-cache-v1",
                "entries": {
                    _cache_key(self._sample_question()): {
                        "selected_link_keys": ["wrong:key"],
                        "selected_target_types": [],
                        "admissible_chains": [],
                        "is_full_aggregation": False,
                    }
                },
            }))
            engine = StaticEngine()
            runner = WebQSPBenchmarkRunner(engine, cache_path=cache_path, emit_progress=False)
            result = runner.run([self._sample_question()])[0]

            self.assertFalse(result["cache_hit"])
            self.assertEqual(engine.calls, 1)
            self.assertTrue(result["hit1"])

    def test_planner_trace_persists_top_k_truncation_evidence(self):
        gold = "person:n:m:people_person_profession"
        top = "person:n:m:common_topic_notable_for"
        q = self._question_with_links([top, gold], gold=[gold])

        class TraceEngine:
            def _plan_question_paths(self, *args):
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys={top},
                    selected_target_types={"topic"},
                    llm_link_keys={top, gold},
                    llm_ranked_link_keys=[top, gold],
                    llm_confidence_scores={top: 0.91, gold: 0.89},
                    selected_after_convergence_keys=[top],
                    llm_top_k_link_keys=[top],
                    llm_truncated_link_keys=[gold],
                    planner_selection_sources={top: ["llm_top_k"]},
                    planner_convergence_config={"top_k": 1, "min_confidence": 0.0},
                    llm_convergence_applied=True,
                    is_full_aggregation=False,
                )

        runner = WebQSPBenchmarkRunner(TraceEngine(), cache_path=None, emit_progress=False)
        result = runner.run([q])[0]

        self.assertFalse(result["hit1"])
        self.assertEqual(result["llm_ranked_link_keys"], [top, gold])
        self.assertEqual(result["top_k_truncation_evidence"]["gold_truncated_link_keys"], [gold])
        self.assertEqual(result["gold_link_diagnostics"][gold]["status"], "truncated_by_top_k")
        self.assertEqual(result["gold_link_diagnostics"][gold]["llm_rank"], 2)

    def test_runner_limit_and_report_include_runtime_controls(self):
        class StaticEngine:
            def _plan_question_paths(self, *args):
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys={"person:n:m:people_person_nationality"},
                    selected_target_types={"country"},
                    is_full_aggregation=False,
                )

        q1 = self._sample_question()
        q2 = json.loads(json.dumps(q1))
        q2["qid"] = "WebQTest-2"

        runner = WebQSPBenchmarkRunner(
            StaticEngine(),
            cache_path=None,
            emit_progress=False,
        )
        results = runner.run([q1, q2], limit=1)
        report = runner.report(results)

        self.assertEqual(report["total_questions"], 1)
        self.assertEqual(report["hit1_count"], 1)
        self.assertIn("p95_ms", report["latency"])
        self.assertIn("timeout_count", report["latency"])
        self.assertEqual(report["cache"]["path"], "disabled")
        self.assertEqual(report["results"][0]["selected_relation_count"], 1)
        self.assertIn("llm_ranked_link_keys", report["results"][0])
        self.assertIn("gold_link_diagnostics", report["results"][0])

    def test_llm_timeout_is_counted_and_not_cached(self):
        class Result:
            used_fallback = True
            error = "Request timed out"
            error_type = "timeout"

        class Planner:
            last_result = None

        class TimeoutEngine:
            def __init__(self):
                self._llm_planner = Planner()
                self.calls = 0

            def _plan_question_paths(self, *args):
                self.calls += 1
                self._llm_planner.last_result = Result()
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys={"person:n:m:people_person_nationality"},
                    selected_target_types={"country"},
                    is_full_aggregation=False,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            q = self._sample_question()
            engine = TimeoutEngine()
            runner = WebQSPBenchmarkRunner(
                engine,
                cache_path=Path(tmpdir) / "cache.json",
                emit_progress=False,
            )

            results = runner.run([q, q])
            report = runner.report(results)

            self.assertEqual(engine.calls, 2)
            self.assertEqual(report["latency"]["timeout_count"], 2)
            self.assertEqual(report["latency"]["error_count"], 2)
            self.assertEqual(report["cache"]["hits"], 0)
            self.assertEqual(report["cache"]["entries"], 0)
            self.assertEqual(results[0]["error"], "Request timed out")
            self.assertEqual(results[0]["llm_ranked_link_keys"], [])
            self.assertEqual(results[0]["top_k_truncation_evidence"]["gold_absent_from_llm_rank_keys"], [
                "person:n:m:people_person_nationality"
            ])

    def test_provider_error_aborts_and_is_excluded_from_quality_metrics(self):
        class Result:
            used_fallback = True
            error = "OpenRouter 402: requires more credits, or fewer max_tokens"
            error_type = "provider"

        class Planner:
            last_result = None

        class ProviderErrorEngine:
            def __init__(self):
                self._llm_planner = Planner()
                self.calls = 0

            def _plan_question_paths(self, *args):
                self.calls += 1
                self._llm_planner.last_result = Result()
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys=set(),
                    selected_target_types=set(),
                    is_full_aggregation=True,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            q = self._sample_question()
            q2 = json.loads(json.dumps(q))
            q2["qid"] = "WebQTest-2"
            engine = ProviderErrorEngine()
            runner = WebQSPBenchmarkRunner(
                engine,
                cache_path=Path(tmpdir) / "cache.json",
                emit_progress=False,
            )

            results = runner.run([q, q2])
            report = runner.report(results)

            self.assertEqual(engine.calls, 1)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["provider_error"])
            self.assertTrue(results[0]["used_fallback"])
            self.assertEqual(results[0]["error_type"], "provider")
            self.assertEqual(report["attempted_questions"], 1)
            self.assertEqual(report["total_questions"], 0)
            self.assertEqual(report["provider_error_count"], 1)
            self.assertEqual(report["runtime_invalid_count"], 0)
            self.assertEqual(report["fallback_count"], 1)
            self.assertEqual(report["full_aggregation_count"], 0)
            self.assertTrue(report["aborted"])
            self.assertIn("OpenRouter 402", report["abort_reason"])
            self.assertEqual(report["latency"]["provider_error_count"], 1)
            self.assertEqual(report["latency"]["runtime_invalid_count"], 0)
            self.assertEqual(report["latency"]["fallback_count"], 1)
            self.assertEqual(report["cache"]["entries"], 0)

    def test_runtime_invalid_aborts_and_is_excluded_from_quality_metrics(self):
        class Result:
            used_fallback = True
            error = "Failed to parse JSON from LLM response: empty content"
            error_type = "runtime_invalid"

        class Planner:
            last_result = None

        class RuntimeInvalidEngine:
            def __init__(self):
                self._llm_planner = Planner()
                self.calls = 0

            def _plan_question_paths(self, *args):
                self.calls += 1
                self._llm_planner.last_result = Result()
                return ReasoningEngine.QuestionPathPlan(
                    selected_link_keys=set(),
                    selected_target_types=set(),
                    is_full_aggregation=True,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            q = self._sample_question()
            q2 = json.loads(json.dumps(q))
            q2["qid"] = "WebQTest-2"
            engine = RuntimeInvalidEngine()
            runner = WebQSPBenchmarkRunner(
                engine,
                cache_path=Path(tmpdir) / "cache.json",
                emit_progress=False,
            )

            results = runner.run([q, q2])
            report = runner.report(results)

            self.assertEqual(engine.calls, 1)
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0]["provider_error"])
            self.assertTrue(results[0]["runtime_invalid"])
            self.assertTrue(results[0]["used_fallback"])
            self.assertEqual(results[0]["error_type"], "runtime_invalid")
            self.assertEqual(report["attempted_questions"], 1)
            self.assertEqual(report["total_questions"], 0)
            self.assertEqual(report["provider_error_count"], 0)
            self.assertEqual(report["runtime_invalid_count"], 1)
            self.assertEqual(report["fallback_count"], 1)
            self.assertEqual(report["error_count"], 1)
            self.assertEqual(report["error_type_counts"], {"runtime_invalid": 1})
            self.assertEqual(report["full_aggregation_count"], 0)
            self.assertTrue(report["aborted"])
            self.assertIn("runtime-invalid LLM result", report["abort_reason"])
            self.assertEqual(report["latency"]["runtime_invalid_count"], 1)
            self.assertEqual(report["latency"]["fallback_count"], 1)
            self.assertEqual(report["cache"]["entries"], 0)


class WebQSPBenchmarkTest(unittest.TestCase):
    """WebQSP public benchmark for Aletheia planner-layer evaluation.

    Tests that _plan_question_paths selects the correct link keys for
    100 WebQSP multi-hop questions converted to Aletheia tenant format.
    """

    def setUp(self):
        self.engine = ReasoningEngine(FakeRepo())
        self.runner = WebQSPBenchmarkRunner(
            self.engine,
            cache_path=benchmark_cache_path(),
            progress_every=benchmark_progress_every(),
            fail_fast_on_provider_error=benchmark_fail_fast_on_provider_error(),
            fail_fast_on_runtime_invalid=benchmark_fail_fast_on_runtime_invalid(),
        )

    def _run_question(self, q: dict[str, Any]) -> dict[str, Any]:
        """Run planner on a single WebQSP question and evaluate."""
        return self.runner.run_question(q)

    # -- Test 1: Data integrity --

    def test_benchmark_has_at_least_50_questions(self):
        """Verify we loaded at least 50 WebQSP questions."""
        self.assertGreaterEqual(
            len(WEBQSP_DATA), 50,
            f"Expected >= 50 questions, got {len(WEBQSP_DATA)}. "
            f"Run: python scripts/convert_webqsp_to_aletheia.py"
        )

    def test_benchmark_covers_multiple_topic_types(self):
        """Verify the benchmark covers diverse entity types."""
        types = {q["topic_type"] for q in WEBQSP_DATA}
        self.assertGreaterEqual(
            len(types), 5,
            f"Expected >= 5 topic types, got {len(types)}: {types}"
        )

    def test_benchmark_covers_1hop_and_2hop(self):
        """Verify both 1-hop and 2-hop questions are present."""
        hop_counts = {q["hop_count"] for q in WEBQSP_DATA}
        self.assertIn(1, hop_counts, "No 1-hop questions found")
        self.assertIn(2, hop_counts, "No 2-hop questions found")

    def test_all_questions_have_gold_link_keys(self):
        """Every question must have at least one gold link key in its link_config."""
        for q in WEBQSP_DATA:
            self.assertTrue(
                q["gold_link_keys"],
                f"{q['qid']} has no gold link keys: {q['question']}"
            )
            all_links = {lc["link"] for lc in q["link_config"]}
            for gk in q["gold_link_keys"]:
                self.assertIn(
                    gk, all_links,
                    f"{q['qid']} gold key '{gk}' not in link_config"
                )

    # -- Test 2: 1-hop Hit@1 --

    def test_1hop_hit1_rate(self):
        """1-hop questions should achieve reasonable Hit@1 rate."""
        one_hop = [q for q in WEBQSP_DATA if q["hop_count"] == 1]
        results = [self._run_question(q) for q in one_hop]
        hit1_count = sum(1 for r in results if r["hit1"])
        hit1_rate = hit1_count / len(results) if results else 0.0
        print(f"\n--- WebQSP 1-hop Hit@1: {hit1_count}/{len(results)} ({hit1_rate:.1%}) ---")
        # Log failures for analysis
        for r in results:
            if not r["hit1"]:
                print(f"  MISS: {r['qid']} | {r['question']}")
                print(f"    Gold: {r['gold_keys']}")
                print(f"    Predicted: {r['predicted_keys']}")
        self.assertGreater(hit1_rate, 0.0, "1-hop Hit@1 should be > 0%")

    # -- Test 3: 2-hop Hit@1 --

    def test_2hop_hit1_rate(self):
        """2-hop questions should achieve reasonable Hit@1 rate."""
        two_hop = [q for q in WEBQSP_DATA if q["hop_count"] == 2]
        results = [self._run_question(q) for q in two_hop]
        hit1_count = sum(1 for r in results if r["hit1"])
        hit1_rate = hit1_count / len(results) if results else 0.0
        print(f"\n--- WebQSP 2-hop Hit@1: {hit1_count}/{len(results)} ({hit1_rate:.1%}) ---")
        for r in results:
            if not r["hit1"]:
                print(f"  MISS: {r['qid']} | {r['question']}")
                print(f"    Gold: {r['gold_keys']}")
                print(f"    Predicted: {r['predicted_keys']}")
        self.assertGreater(hit1_rate, 0.0, "2-hop Hit@1 should be > 0%")

    # -- Test 4: Overall metrics --

    def test_overall_metrics_report(self):
        """Run all questions and output a structured metrics report."""
        all_results = self.runner.run(
            WEBQSP_DATA,
            limit=benchmark_limit(),
        )
        report = self.runner.report(all_results)
        total = report["total_questions"]

        # Per-type breakdown
        type_stats: dict[str, dict] = {}
        for r in all_results:
            if r.get("provider_error"):
                continue
            t = r["topic_type"]
            type_stats.setdefault(t, {"total": 0, "hit1": 0})
            type_stats[t]["total"] += 1
            if r["hit1"]:
                type_stats[t]["hit1"] += 1

        latency = report["latency"]
        cache = report["cache"]

        report_str = (
            f"\n{'='*60}\n"
            f"  WebQSP Public Benchmark Report (Aletheia Planner)\n"
            f"{'='*60}\n"
            f"Attempted questions: {report['attempted_questions']}\n"
            f"Planner-quality questions: {report['total_questions']}\n"
            f"Hit@1: {report['hit1_count']}/{report['total_questions']} ({report['hit1_rate']:.1%})\n"
            f"Avg F1: {report['avg_f1']:.3f}\n"
            f"Full aggregation fallback: {report['full_aggregation_count']}/{report['total_questions']} ({report['full_aggregation_rate']:.1%})\n"
            f"Latency ms: avg={latency['avg_ms']:.0f}, p50={latency['p50_ms']:.0f}, "
            f"p95={latency['p95_ms']:.0f}, max={latency['max_ms']:.0f}, "
            f"timeouts={latency['timeout_count']}, errors={latency['error_count']}, "
            f"provider_errors={latency['provider_error_count']}, "
            f"runtime_invalid={latency['runtime_invalid_count']}, "
            f"fallbacks={latency['fallback_count']}\n"
            f"Runtime: aborted={report['aborted']}, provider_error_count={report['provider_error_count']}, "
            f"runtime_invalid_count={report['runtime_invalid_count']}, "
            f"fallback_count={report['fallback_count']}, error_type_counts={report['error_type_counts']}, "
            f"abort_reason={report['abort_reason']}\n"
            f"Cache: path={cache['path']}, hits={cache['hits']}, misses={cache['misses']}, entries={cache['entries']}\n"
            f"\nPer-type breakdown:\n"
        )
        for t, s in sorted(type_stats.items(), key=lambda x: -x[1]["total"]):
            rate = s["hit1"] / s["total"] if s["total"] else 0.0
            report_str += f"  {t:15s}: {s['hit1']}/{s['total']} ({rate:.1%})\n"

        report_str += (
            f"\nSOTA comparison:\n"
            f"  OntGQA WebQSP Hit@1: 91.5%\n"
            f"  Aletheia Hit@1:       {report['hit1_rate']:.1%}\n"
            f"  Gap: {91.5 - report['hit1_rate']*100:.1f}pp\n"
            f"{'='*60}\n"
        )
        print(report_str)

        # Structural assertions (not pass/fail gates, just sanity)
        self.assertGreater(report["total_questions"], 0)
        self.assertGreaterEqual(report["hit1_rate"], 0.0)
        self.assertGreaterEqual(report["avg_f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
