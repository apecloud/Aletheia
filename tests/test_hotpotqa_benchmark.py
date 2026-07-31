#!/usr/bin/env python3
"""HotpotQA passage-only benchmark for Aletheia.

Loads a frozen 100-question HotpotQA dev-distractor sample and answers each
question directly from its own gold+distractor Wikipedia paragraphs via
``HotpotQAAnswerer`` -- no graph tenant, no ``ReasoningEngine``. Scored with
official EM / token-F1 (see ``hotpotqa_sample_eval``).

Run: python -m unittest tests.test_hotpotqa_benchmark
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from hotpotqa_frozen_sample import DEFAULT_SAMPLE_PATH, load_hotpotqa_cases  # noqa: E402
from hotpotqa_sample_eval import evaluate_em, evaluate_f1  # noqa: E402


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

DEFAULT_CACHE_PATH = ROOT / "tmp" / "hotpotqa_planner_cache.json"
CACHE_VERSION = "hotpotqa-answerer-cache-v1"

HOTPOTQA_DATA = load_hotpotqa_cases(DEFAULT_SAMPLE_PATH)


# ---------------------------------------------------------------------------
# Runtime controls (same env-var shape as WebQSP's benchmark runner)
# ---------------------------------------------------------------------------

def benchmark_limit(default: int | None = None) -> int | None:
    raw = os.environ.get("HOTPOTQA_BENCHMARK_LIMIT", "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def benchmark_progress_every(default: int = 10) -> int:
    raw = os.environ.get("HOTPOTQA_BENCHMARK_PROGRESS_EVERY", "")
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def benchmark_cache_path(default: Path = DEFAULT_CACHE_PATH) -> Path | None:
    """Set HOTPOTQA_BENCHMARK_CACHE_PATH=off to disable the cache.

    Without an explicit path, the cache is enabled only for live LLM runs
    (ALETHEIA_LLM_PLANNER_ENABLED=1), same convention as WebQSP.
    """
    raw = os.environ.get("HOTPOTQA_BENCHMARK_CACHE_PATH", "")
    if raw.lower() in {"0", "false", "no", "off"}:
        return None
    if raw:
        return Path(raw)
    llm_enabled = os.environ.get("ALETHEIA_LLM_PLANNER_ENABLED", "").lower()
    if llm_enabled in {"1", "true", "yes"}:
        return default
    return None


def benchmark_fail_fast_on_provider_error(default: bool = True) -> bool:
    raw = os.environ.get("HOTPOTQA_BENCHMARK_FAIL_FAST_ON_PROVIDER_ERROR", "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def benchmark_fail_fast_on_runtime_invalid(default: bool = True) -> bool:
    raw = os.environ.get("HOTPOTQA_BENCHMARK_FAIL_FAST_ON_RUNTIME_INVALID", "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Cache + timing (same shape as WebQSPBenchmarkRunner)
# ---------------------------------------------------------------------------

def _context_hash(context: list[list[Any]]) -> str:
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _cache_key(q: dict[str, Any]) -> str:
    payload = {
        "version": CACHE_VERSION,
        "question": q["question"],
        "context_hash": _context_hash(q["context"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


@dataclass
class BenchmarkTiming:
    latencies_ms: list[float] = field(default_factory=list)
    timeout_count: int = 0
    error_count: int = 0
    provider_error_count: int = 0
    runtime_invalid_count: int = 0
    fallback_count: int = 0

    def record(self, elapsed_ms: float, error: str = "", error_type: str = "", used_fallback: bool = False) -> None:
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
                "avg_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0,
                "timeout_count": self.timeout_count, "error_count": self.error_count,
                "provider_error_count": self.provider_error_count,
                "runtime_invalid_count": self.runtime_invalid_count,
                "fallback_count": self.fallback_count,
            }
        p95_index = min(len(values) - 1, int(round((len(values) - 1) * 0.95)))
        return {
            "avg_ms": statistics.fmean(values), "p50_ms": statistics.median(values),
            "p95_ms": values[p95_index], "max_ms": values[-1],
            "timeout_count": self.timeout_count, "error_count": self.error_count,
            "provider_error_count": self.provider_error_count,
            "runtime_invalid_count": self.runtime_invalid_count,
            "fallback_count": self.fallback_count,
        }


class AnswerResultCache:
    """JSON cache for question/context to answerer output."""

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
        with self.path.open("w") as f:
            json.dump({"version": CACHE_VERSION, "entries": self.entries}, f, indent=2, sort_keys=True)

    def summary(self) -> dict[str, int | str]:
        return {
            "path": str(self.path) if self.path else "disabled",
            "hits": self.hits, "misses": self.misses, "entries": len(self.entries),
        }


class HotpotQABenchmarkRunner:
    """Reusable HotpotQA passage-only benchmark with cache, subsets, progress."""

    def __init__(
        self,
        answerer: Any,
        cache_path: Path | None = None,
        progress_every: int = 10,
        emit_progress: bool = True,
        fail_fast_on_provider_error: bool = True,
        fail_fast_on_runtime_invalid: bool = True,
    ):
        self.answerer = answerer
        self.cache = AnswerResultCache(cache_path)
        self.progress_every = max(0, progress_every)
        self.emit_progress = emit_progress
        self.timing = BenchmarkTiming()
        self.fail_fast_on_provider_error = fail_fast_on_provider_error
        self.fail_fast_on_runtime_invalid = fail_fast_on_runtime_invalid
        self.aborted = False
        self.abort_reason = ""

    def _empty_answer_payload(self) -> dict[str, Any]:
        return {"answer": "", "supporting_titles": []}

    def _answer_payload(self, result: Any) -> dict[str, Any]:
        return {
            "answer": result.answer,
            "supporting_titles": list(result.supporting_titles),
        }

    def _answer_payload_from_cache_or_llm(
        self, q: dict[str, Any]
    ) -> tuple[dict[str, Any], bool, float, dict[str, Any]]:
        key = _cache_key(q)
        cached = self.cache.get(key)
        if cached is not None:
            return cached, True, 0.0, {"error": "", "error_type": "", "used_fallback": False}

        start = time.monotonic()
        result = self.answerer.answer_question(q["question"], q["context"])
        elapsed_ms = (time.monotonic() - start) * 1000
        payload = self._answer_payload(result)
        error_info = {
            "error": result.error if result.used_fallback else "",
            "error_type": result.error_type if result.used_fallback else "",
            "used_fallback": bool(result.used_fallback),
        }
        if not error_info["error"]:
            self.cache.put(key, payload)
        return payload, False, elapsed_ms, error_info

    def run_question(self, q: dict[str, Any]) -> dict[str, Any]:
        """Run the answerer on a single HotpotQA question and evaluate."""
        error = ""
        error_type = ""
        used_fallback = False
        try:
            payload, cache_hit, elapsed_ms, error_info = self._answer_payload_from_cache_or_llm(q)
            error = str(error_info.get("error", "") or "")
            error_type = str(error_info.get("error_type", "") or "")
            used_fallback = bool(error_info.get("used_fallback", False))
        except Exception as exc:
            elapsed_ms = 0.0
            cache_hit = False
            error = str(exc)
            error_type = "runtime"
            payload = self._empty_answer_payload()

        self.timing.record(elapsed_ms, error=error, error_type=error_type, used_fallback=used_fallback)
        prediction = payload["answer"]
        gold = q["answer"]
        em = evaluate_em(prediction, gold)
        f1 = evaluate_f1(prediction, gold)
        return {
            "qid": q["qid"],
            "question": q["question"],
            "type": q.get("type", ""),
            "level": q.get("level", ""),
            "prediction": prediction,
            "gold": gold,
            "em": em,
            "f1": f1,
            "supporting_titles": payload["supporting_titles"],
            "latency_ms": elapsed_ms,
            "cache_hit": cache_hit,
            "error": error,
            "error_type": error_type,
            "used_fallback": used_fallback,
            "provider_error": error_type == "provider",
            "runtime_invalid": error_type == "runtime_invalid",
        }

    def run(self, questions: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
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
                        f"[HotpotQA] {idx}/{total} qid={result['qid']} "
                        f"em={int(result['em'])} f1={result['f1']:.3f} "
                        f"latency_ms={result['latency_ms']:.0f} cache={state}"
                    )
                if self.fail_fast_on_provider_error and result["provider_error"]:
                    self.aborted = True
                    self.abort_reason = f"provider error at {result['qid']}: {result['error']}"
                    if self.emit_progress:
                        print(f"[HotpotQA] abort: {self.abort_reason}")
                    break
                if self.fail_fast_on_runtime_invalid and result["runtime_invalid"]:
                    self.aborted = True
                    self.abort_reason = f"runtime-invalid LLM result at {result['qid']}: {result['error']}"
                    if self.emit_progress:
                        print(f"[HotpotQA] abort: {self.abort_reason}")
                    break
        finally:
            self.cache.save()
        return results

    def report(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        quality_results = [r for r in results if not r.get("provider_error") and not r.get("runtime_invalid")]
        total = len(quality_results)
        em_count = sum(1 for r in quality_results if r["em"])
        avg_f1 = sum(r["f1"] for r in quality_results) / total if total else 0.0
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
            "em_count": em_count,
            "em_rate": em_count / total if total else 0.0,
            "avg_f1": avg_f1,
            "provider_error_count": provider_error_count,
            "runtime_invalid_count": runtime_invalid_count,
            "fallback_count": fallback_count,
            "error_count": sum(1 for r in results if r.get("error")),
            "error_type_counts": error_type_counts,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "latency": self.timing.summary(),
            "cache": self.cache.summary(),
            "results": results,
        }


# ---------------------------------------------------------------------------
# Infra tests: deterministic, no live LLM
# ---------------------------------------------------------------------------

class BenchmarkRunnerInfrastructureTest(unittest.TestCase):
    """Deterministic tests for benchmark runtime controls (no live LLM)."""

    def _sample_question(self) -> dict[str, Any]:
        return {
            "qid": "HotpotTest-1",
            "question": "In what year was X founded?",
            "type": "bridge",
            "level": "easy",
            "context": [["X", ["X was founded in 1900.", "X is a university."]]],
            "supporting_facts": [["X", 0]],
            "answer": "1900",
        }

    class _Result:
        def __init__(self, answer="", titles=None, error="", error_type="", used_fallback=False):
            self.answer = answer
            self.supporting_titles = titles or []
            self.error = error
            self.error_type = error_type
            self.used_fallback = used_fallback

    def test_cache_key_changes_with_context(self):
        q1 = self._sample_question()
        q2 = json.loads(json.dumps(q1))
        q2["context"] = [["X", ["X was founded in 1901."]]]
        self.assertNotEqual(_cache_key(q1), _cache_key(q2))

    def test_runner_persists_and_reuses_cache(self):
        class CountingAnswerer:
            def __init__(self, fail: bool = False):
                self.calls = 0
                self.fail = fail

            def answer_question(self, *args):
                self.calls += 1
                if self.fail:
                    raise AssertionError("answerer should not be called on cache hit")
                return BenchmarkRunnerInfrastructureTest._Result(answer="1900")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            q = self._sample_question()

            first_runner = HotpotQABenchmarkRunner(CountingAnswerer(), cache_path=cache_path, emit_progress=False)
            first_result = first_runner.run([q])[0]
            self.assertFalse(first_result["cache_hit"])
            self.assertTrue(first_result["em"])
            self.assertTrue(cache_path.exists())

            second_answerer = CountingAnswerer(fail=True)
            second_runner = HotpotQABenchmarkRunner(second_answerer, cache_path=cache_path, emit_progress=False)
            second_result = second_runner.run([q])[0]
            self.assertTrue(second_result["cache_hit"])
            self.assertTrue(second_result["em"])
            self.assertEqual(second_answerer.calls, 0)

    def test_provider_error_aborts_and_is_excluded_from_quality_metrics(self):
        class ProviderErrorAnswerer:
            def __init__(self):
                self.calls = 0

            def answer_question(self, *args):
                self.calls += 1
                return BenchmarkRunnerInfrastructureTest._Result(
                    error="OpenRouter 402: requires more credits, or fewer max_tokens",
                    error_type="provider",
                    used_fallback=True,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            q = self._sample_question()
            q2 = json.loads(json.dumps(q))
            q2["qid"] = "HotpotTest-2"
            answerer = ProviderErrorAnswerer()
            runner = HotpotQABenchmarkRunner(answerer, cache_path=Path(tmpdir) / "cache.json", emit_progress=False)

            results = runner.run([q, q2])
            report = runner.report(results)

            self.assertEqual(answerer.calls, 1)
            self.assertEqual(len(results), 1)
            self.assertTrue(results[0]["provider_error"])
            self.assertEqual(report["attempted_questions"], 1)
            self.assertEqual(report["total_questions"], 0)
            self.assertTrue(report["aborted"])
            self.assertIn("OpenRouter 402", report["abort_reason"])

    def test_runtime_invalid_aborts_and_is_excluded_from_quality_metrics(self):
        class RuntimeInvalidAnswerer:
            def __init__(self):
                self.calls = 0

            def answer_question(self, *args):
                self.calls += 1
                return BenchmarkRunnerInfrastructureTest._Result(
                    error="Failed to parse JSON from LLM response: empty content",
                    error_type="runtime_invalid",
                    used_fallback=True,
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            q = self._sample_question()
            q2 = json.loads(json.dumps(q))
            q2["qid"] = "HotpotTest-2"
            answerer = RuntimeInvalidAnswerer()
            runner = HotpotQABenchmarkRunner(answerer, cache_path=Path(tmpdir) / "cache.json", emit_progress=False)

            results = runner.run([q, q2])
            report = runner.report(results)

            self.assertEqual(answerer.calls, 1)
            self.assertTrue(results[0]["runtime_invalid"])
            self.assertTrue(report["aborted"])
            self.assertIn("runtime-invalid LLM result", report["abort_reason"])

    def test_runner_limit_and_report_include_runtime_controls(self):
        class StaticAnswerer:
            def answer_question(self, *args):
                return BenchmarkRunnerInfrastructureTest._Result(answer="1900")

        q1 = self._sample_question()
        q2 = json.loads(json.dumps(q1))
        q2["qid"] = "HotpotTest-2"

        runner = HotpotQABenchmarkRunner(StaticAnswerer(), cache_path=None, emit_progress=False)
        results = runner.run([q1, q2], limit=1)
        report = runner.report(results)

        self.assertEqual(report["total_questions"], 1)
        self.assertEqual(report["em_count"], 1)
        self.assertIn("p95_ms", report["latency"])
        self.assertEqual(report["cache"]["path"], "disabled")


# ---------------------------------------------------------------------------
# Metric unit tests: deterministic, no live LLM
# ---------------------------------------------------------------------------

class MetricTest(unittest.TestCase):
    def test_em_exact_match_after_normalization(self):
        self.assertTrue(evaluate_em("The Eiffel Tower", "eiffel tower"))
        self.assertFalse(evaluate_em("Eiffel Tower", "Big Ben"))

    def test_f1_partial_overlap(self):
        self.assertGreater(evaluate_f1("Eiffel Tower in Paris", "Eiffel Tower"), 0.5)
        self.assertEqual(evaluate_f1("completely unrelated", "Eiffel Tower"), 0.0)

    def test_yes_no_no_partial_credit(self):
        self.assertEqual(evaluate_f1("yes", "no"), 0.0)
        self.assertEqual(evaluate_f1("yes", "yes"), 1.0)


# ---------------------------------------------------------------------------
# Quality benchmark: needs a real dataset file; live-LLM parts need
# ALETHEIA_LLM_PLANNER_ENABLED=1 (same convention as WebQSP).
# ---------------------------------------------------------------------------

class HotpotQABenchmarkTest(unittest.TestCase):
    """HotpotQA passage-only benchmark for Aletheia's LLM answerer."""

    def setUp(self):
        if not HOTPOTQA_DATA:
            self.skipTest(
                "No frozen HotpotQA sample found. Run: "
                "python scripts/hotpotqa_frozen_sample.py"
            )
        from hotpotqa_passage_qa import HotpotQAAnswerer
        self.answerer = HotpotQAAnswerer()
        self.runner = HotpotQABenchmarkRunner(
            self.answerer,
            cache_path=benchmark_cache_path(),
            progress_every=benchmark_progress_every(),
            fail_fast_on_provider_error=benchmark_fail_fast_on_provider_error(),
            fail_fast_on_runtime_invalid=benchmark_fail_fast_on_runtime_invalid(),
        )

    # -- Data integrity --

    def test_benchmark_has_at_least_50_questions(self):
        self.assertGreaterEqual(
            len(HOTPOTQA_DATA), 50,
            f"Expected >= 50 questions, got {len(HOTPOTQA_DATA)}. "
            f"Run: python scripts/hotpotqa_frozen_sample.py"
        )

    def test_benchmark_covers_bridge_and_comparison(self):
        types = {q["type"] for q in HOTPOTQA_DATA}
        self.assertIn("bridge", types)
        self.assertIn("comparison", types)

    def test_benchmark_covers_hard_level(self):
        """The official distractor validation split is 100% "hard" level --
        easy/medium labels only appear in the train split -- so this checks
        the one level that's actually present rather than asserting a mix
        that doesn't exist in this split."""
        levels = {q["level"] for q in HOTPOTQA_DATA}
        self.assertEqual(levels, {"hard"})

    def test_all_questions_have_gold_answer_and_context(self):
        for q in HOTPOTQA_DATA:
            self.assertTrue(q["answer"], f"{q['qid']} has no gold answer")
            self.assertTrue(q["context"], f"{q['qid']} has no context passages")

    # -- Overall metrics (needs a live LLM to be meaningful) --

    def test_overall_metrics_report(self):
        if not os.environ.get("ALETHEIA_LLM_PLANNER_ENABLED"):
            self.skipTest("Set ALETHEIA_LLM_PLANNER_ENABLED=1 to run the live-LLM quality report")

        all_results = self.runner.run(HOTPOTQA_DATA, limit=benchmark_limit())
        report = self.runner.report(all_results)

        type_stats: dict[str, dict] = {}
        level_stats: dict[str, dict] = {}
        for r in all_results:
            if r.get("provider_error"):
                continue
            for stats, key in ((type_stats, r["type"]), (level_stats, r["level"])):
                stats.setdefault(key, {"total": 0, "em": 0})
                stats[key]["total"] += 1
                stats[key]["em"] += int(r["em"])

        latency = report["latency"]
        cache = report["cache"]
        report_str = (
            f"\n{'='*60}\n"
            f"  HotpotQA Passage-Only Benchmark Report (Aletheia)\n"
            f"{'='*60}\n"
            f"Attempted questions: {report['attempted_questions']}\n"
            f"Quality questions: {report['total_questions']}\n"
            f"EM: {report['em_count']}/{report['total_questions']} ({report['em_rate']:.1%})\n"
            f"Avg F1: {report['avg_f1']:.3f}\n"
            f"Latency ms: avg={latency['avg_ms']:.0f}, p50={latency['p50_ms']:.0f}, "
            f"p95={latency['p95_ms']:.0f}, max={latency['max_ms']:.0f}, "
            f"timeouts={latency['timeout_count']}, errors={latency['error_count']}\n"
            f"Runtime: aborted={report['aborted']}, abort_reason={report['abort_reason']}\n"
            f"Cache: path={cache['path']}, hits={cache['hits']}, misses={cache['misses']}\n"
            f"\nBy type:\n"
        )
        for t, s in sorted(type_stats.items()):
            rate = s["em"] / s["total"] if s["total"] else 0.0
            report_str += f"  {t:15s}: {s['em']}/{s['total']} ({rate:.1%})\n"
        report_str += "\nBy level:\n"
        for lvl, s in sorted(level_stats.items()):
            rate = s["em"] / s["total"] if s["total"] else 0.0
            report_str += f"  {lvl:15s}: {s['em']}/{s['total']} ({rate:.1%})\n"
        report_str += (
            f"\nPublished HotpotQA distractor-setting baselines (for context, not a hard gate):\n"
            f"  Yang et al. 2018 baseline EM/F1: 45.6 / 58.9\n"
            f"  Aletheia EM/F1:                  {report['em_rate']*100:.1f} / {report['avg_f1']*100:.1f}\n"
            f"{'='*60}\n"
        )
        print(report_str)

        self.assertGreater(report["total_questions"], 0)
        self.assertGreaterEqual(report["em_rate"], 0.0)
        self.assertGreaterEqual(report["avg_f1"], 0.0)


if __name__ == "__main__":
    unittest.main()
