#!/usr/bin/env python3
"""Score HotpotQA predictions with the official EM / token-F1 metrics.

Normalization and F1 follow the official HotpotQA/SQuAD evaluation script:
lowercase, strip punctuation, drop English articles, collapse whitespace,
then compare tokens for F1 and the normalized strings for EM. "yes"/"no"
answers are compared as exact tokens (no partial-token F1 credit), matching
the official script's special-casing.

Usage:
    python scripts/hotpotqa_sample_eval.py --predictions tmp/hotpotqa_predictions.json \
        --gold benchmarks/hotpotqa/hotpot_sample100.json --report-json tmp/hotpotqa_report.json
"""

from __future__ import annotations

import argparse
import json
import re
import string
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def normalize_answer(text: str) -> str:
    """Lowercase, remove punctuation/articles, collapse whitespace."""

    def remove_articles(s: str) -> str:
        return re.sub(r"\b(a|an|the)\b", " ", s)

    def white_space_fix(s: str) -> str:
        return " ".join(s.split())

    def remove_punc(s: str) -> str:
        exclude = set(string.punctuation)
        return "".join(ch for ch in s if ch not in exclude)

    def lower(s: str) -> str:
        return s.lower()

    return white_space_fix(remove_articles(remove_punc(lower(text or ""))))


def evaluate_em(prediction: str, gold: str) -> bool:
    """Exact match over normalized answer strings."""
    return normalize_answer(prediction) == normalize_answer(gold)


def evaluate_f1(prediction: str, gold: str) -> float:
    """Token-overlap F1, with yes/no/noanswer special-casing.

    If either side is yes/no/noanswer and they don't match exactly, F1 is 0
    (no partial credit for confusing "yes" with a token overlap), matching
    the official HotpotQA eval script.
    """
    normalized_prediction = normalize_answer(prediction)
    normalized_gold = normalize_answer(gold)

    special_tokens = {"yes", "no", "noanswer"}
    if normalized_prediction in special_tokens or normalized_gold in special_tokens:
        return 1.0 if normalized_prediction == normalized_gold else 0.0

    prediction_tokens = normalized_prediction.split()
    gold_tokens = normalized_gold.split()
    if not prediction_tokens or not gold_tokens:
        return 1.0 if prediction_tokens == gold_tokens else 0.0

    common = Counter(prediction_tokens) & Counter(gold_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0

    precision = num_same / len(prediction_tokens)
    recall = num_same / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def score_predictions(
    predictions: dict[str, str],
    gold_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score a qid->predicted-answer map against the frozen gold sample."""
    per_question: list[dict[str, Any]] = []
    for case in gold_cases:
        qid = case["qid"]
        pred = predictions.get(qid, "")
        gold = case["answer"]
        em = evaluate_em(pred, gold)
        f1 = evaluate_f1(pred, gold)
        per_question.append({
            "qid": qid,
            "type": case.get("type", ""),
            "level": case.get("level", ""),
            "prediction": pred,
            "gold": gold,
            "em": em,
            "f1": f1,
        })

    total = len(per_question)
    em_count = sum(1 for r in per_question if r["em"])
    avg_f1 = sum(r["f1"] for r in per_question) / total if total else 0.0

    def bucket(field_name: str) -> dict[str, dict[str, float | int]]:
        buckets: dict[str, dict[str, Any]] = {}
        for r in per_question:
            key = r[field_name] or "unknown"
            b = buckets.setdefault(key, {"total": 0, "em_count": 0, "f1_sum": 0.0})
            b["total"] += 1
            b["em_count"] += int(r["em"])
            b["f1_sum"] += r["f1"]
        return {
            key: {
                "total": b["total"],
                "em_count": b["em_count"],
                "em_rate": b["em_count"] / b["total"] if b["total"] else 0.0,
                "avg_f1": b["f1_sum"] / b["total"] if b["total"] else 0.0,
            }
            for key, b in buckets.items()
        }

    return {
        "total_questions": total,
        "em_count": em_count,
        "em_rate": em_count / total if total else 0.0,
        "avg_f1": avg_f1,
        "by_type": bucket("type"),
        "by_level": bucket("level"),
        "results": per_question,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score HotpotQA predictions with EM/F1")
    parser.add_argument("--predictions", type=Path, required=True, help="qid -> predicted answer JSON")
    parser.add_argument("--gold", type=Path, required=True, help="frozen HotpotQA sample JSON")
    parser.add_argument("--report-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    gold_cases = json.loads(args.gold.read_text(encoding="utf-8"))
    report = score_predictions(predictions, gold_cases)

    print(f"EM: {report['em_count']}/{report['total_questions']} ({report['em_rate']:.1%})")
    print(f"Avg F1: {report['avg_f1']:.3f}")
    print("By type:")
    for key, stats in sorted(report["by_type"].items()):
        print(f"  {key:12s}: EM={stats['em_rate']:.1%} F1={stats['avg_f1']:.3f} n={stats['total']}")
    print("By level:")
    for key, stats in sorted(report["by_level"].items()):
        print(f"  {key:12s}: EM={stats['em_rate']:.1%} F1={stats['avg_f1']:.3f} n={stats['total']}")

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
