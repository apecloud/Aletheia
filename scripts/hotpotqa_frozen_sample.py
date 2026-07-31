#!/usr/bin/env python3
"""Download and freeze a deterministic HotpotQA dev-distractor sample.

HotpotQA (Yang et al., 2018) pairs each question with ~10 candidate
Wikipedia paragraphs (2 gold/supporting, 8 distractor) and a short span or
yes/no answer. Unlike WebQSP/Mintaka this benchmark is passage-only: no
knowledge-graph tenant is built, so there is no live per-question fetch to
get rate-limited on -- just one static dataset download.

Usage:
    python scripts/hotpotqa_frozen_sample.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# The original CMU host (curtis.ml.cmu.edu) that ships the raw JSON dump is
# no longer reachable (dead personal academic server). The HuggingFace
# dataset mirror ("hotpotqa/hotpot_qa", "distractor" config, "validation"
# split) has the identical 7405-question dev-distractor set as parquet.
DEV_DISTRACTOR_URL = (
    "https://huggingface.co/api/datasets/hotpotqa/hotpot_qa/parquet/distractor/validation/0.parquet"
)
DEFAULT_RAW_PATH = ROOT / "benchmarks" / "hotpotqa" / "hotpot_dev_distractor_validation.parquet"
DEFAULT_SAMPLE_PATH = ROOT / "benchmarks" / "hotpotqa" / "hotpot_sample100.json"
USER_AGENT = "Aletheia-HotpotQA-Benchmark/1.0"


def download_dev_distractor(dest: Path = DEFAULT_RAW_PATH, *, url: str = DEV_DISTRACTOR_URL) -> Path:
    """One-time download of the official HotpotQA dev-distractor set.

    Uses ``ProxyHandler()`` (which reads ``HTTP_PROXY``/``HTTPS_PROXY``/
    ``ALL_PROXY`` from the environment), not ``ProxyHandler({})`` -- the
    latter forces a direct connection and was the exact bug that stalled the
    Mintaka pipeline for hours (see import_task236_frozen100_public_tenant.py).
    """
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    opener = build_opener(ProxyHandler())
    request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": USER_AGENT})
    with opener.open(request, timeout=120) as response:
        payload = response.read()
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    tmp_path.write_bytes(payload)
    tmp_path.replace(dest)
    return dest


def _load_raw_records(raw_path: Path) -> list[dict[str, Any]]:
    """Read the HF parquet mirror into one plain dict per question.

    Each parquet row stores ``context``/``supporting_facts`` as a struct of
    parallel arrays (``{"title": [...], "sentences": [[...], [...]]}``)
    rather than the original JSON's list-of-lists -- this reshapes it back
    into ``[[title, sentences], ...]`` / ``[[title, sent_id], ...]`` pairs.
    """
    df = pd.read_parquet(raw_path)
    records = []
    for row in df.to_dict(orient="records"):
        context_cols = row["context"]
        context = list(zip(list(context_cols["title"]), [list(s) for s in context_cols["sentences"]]))
        facts_cols = row["supporting_facts"]
        supporting_facts = list(zip(list(facts_cols["title"]), [int(i) for i in facts_cols["sent_id"]]))
        records.append({
            "_id": row["id"],
            "question": row["question"],
            "answer": row["answer"],
            "type": row["type"],
            "level": row["level"],
            "context": context,
            "supporting_facts": supporting_facts,
        })
    return records


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields this benchmark needs from one raw HotpotQA record."""
    return {
        "qid": record["_id"],
        "question": record["question"].strip(),
        "answer": record["answer"].strip(),
        "type": record.get("type", ""),
        "level": record.get("level", ""),
        "context": [[title, list(sentences)] for title, sentences in record.get("context", [])],
        "supporting_facts": [[title, idx] for title, idx in record.get("supporting_facts", [])],
    }


def freeze_sample(records: list[dict[str, Any]], *, sample_size: int = 100) -> list[dict[str, Any]]:
    """Deterministic sample covering both question types and all difficulty
    levels, with no randomness.

    Groups raw records by (type, level), sorts each group by its own ``_id``,
    then round-robins one record at a time across the sorted groups. Given
    the same source file this always picks the same sample_size questions,
    naturally keeping HotpotQA's bridge/comparison and easy/medium/hard mix.
    """
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (record.get("type", ""), record.get("level", ""))
        groups.setdefault(key, []).append(record)
    for key in groups:
        groups[key].sort(key=lambda r: r["_id"])

    ordered_keys = sorted(groups.keys())
    selected: list[dict[str, Any]] = []
    row = 0
    while len(selected) < sample_size:
        progressed = False
        for key in ordered_keys:
            group = groups[key]
            if row < len(group):
                selected.append(group[row])
                progressed = True
                if len(selected) == sample_size:
                    break
        if not progressed:
            break
        row += 1

    return [_normalize_record(record) for record in selected]


def build_frozen_sample(
    *,
    raw_path: Path = DEFAULT_RAW_PATH,
    sample_path: Path = DEFAULT_SAMPLE_PATH,
    sample_size: int = 100,
    source_url: str = DEV_DISTRACTOR_URL,
) -> Path:
    if sample_path.exists():
        return sample_path
    download_dev_distractor(raw_path, url=source_url)
    records = _load_raw_records(raw_path)
    if not records:
        raise ValueError(f"HotpotQA source at {raw_path} is empty or malformed")
    sample = freeze_sample(records, sample_size=sample_size)
    if len(sample) < sample_size:
        raise ValueError(f"Only {len(sample)} eligible questions, requested {sample_size}")
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = sample_path.with_suffix(sample_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(sample, indent=2, sort_keys=False, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(sample_path)
    sample_path.chmod(0o444)
    return sample_path


def load_hotpotqa_cases(path: Path = DEFAULT_SAMPLE_PATH) -> list[dict[str, Any]]:
    """Load a frozen HotpotQA sample: question, context passages, and gold answer."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze a deterministic HotpotQA dev-distractor sample")
    parser.add_argument("--raw-path", type=Path, default=DEFAULT_RAW_PATH)
    parser.add_argument("--dest", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--source-url", default=DEV_DISTRACTOR_URL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.dest.exists():
        print(json.dumps({"status": "OK", "note": "already exists", "path": str(args.dest)}))
        return 0
    sample_path = build_frozen_sample(
        raw_path=args.raw_path,
        sample_path=args.dest,
        sample_size=args.sample_size,
        source_url=args.source_url,
    )
    sample = load_hotpotqa_cases(sample_path)
    types = sorted({row["type"] for row in sample})
    levels = sorted({row["level"] for row in sample})
    print(json.dumps({
        "status": "OK",
        "path": str(sample_path),
        "sample_size": len(sample),
        "types": types,
        "levels": levels,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
