#!/usr/bin/env python3
"""Download and freeze a deterministic 2WikiMultihopQA validation sample.

2WikiMultihopQA (Ho et al., 2020) pairs each question with 10 candidate
Wikipedia paragraphs (a mix of gold/supporting and distractor passages) and
a short answer -- same passage-only shape as HotpotQA (this benchmark
replaces WebQSP, whose only local file was a messy RoG/HuggingFace
derivative with an empty gold-subgraph row, four empty-answer rows, and a
row count that doesn't match any official split). Reuses HotpotQA's own
extraction (``passage_relation_extraction.PassageRelationExtractor``, which is
dataset-agnostic) and Nebula importer (``import_hotpotqa_nebula_tenant.py``,
invoked with ``--input`` pointed at this sample) unchanged, since the case
schema produced here matches HotpotQA's own frozen-sample schema exactly.

The official GitHub release (``Alab-NII/2wikimultihop``) has no single
canonical HuggingFace mirror -- several independent re-uploads exist with
inconsistent schemas. ``framolfese/2WikiMultihopQA`` is used here because it
remaps fields to the exact HotpotQA shape (``id, question, answer, type,
evidences, supporting_facts, context``), plus an extra ``evidences`` field
(ordered (subject, relation, object) triples) that HotpotQA doesn't have --
kept in the frozen sample for potential future use, not currently consumed
by extraction/import.

Usage:
    python scripts/twowikimultihopqa_frozen_sample.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_URL = (
    "https://huggingface.co/api/datasets/framolfese/2WikiMultihopQA/parquet/default/validation/0.parquet"
)
DEFAULT_RAW_PATH = ROOT / "benchmarks" / "2wikimultihopqa" / "validation_raw.parquet"
DEFAULT_SAMPLE_PATH = ROOT / "benchmarks" / "2wikimultihopqa" / "twowiki_sample100.json"
USER_AGENT = "Aletheia-2WikiMultihopQA-Benchmark/1.0"


def download_validation(dest: Path = DEFAULT_RAW_PATH, *, url: str = VALIDATION_URL) -> Path:
    """One-time download of the validation split.

    Uses ``ProxyHandler()`` (reads ``HTTP_PROXY``/``HTTPS_PROXY``/``ALL_PROXY``
    from the environment), not ``ProxyHandler({})`` -- the latter forces a
    direct connection (see ``hotpotqa_frozen_sample.py``'s own docstring for
    the incident this avoids).
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
    """Read the parquet mirror into one plain dict per question.

    ``context``/``supporting_facts`` are structs of parallel arrays (same
    shape as HotpotQA's HF parquet mirror) -- reshaped back into
    ``[[title, sentences], ...]`` / ``[[title, sent_id], ...]`` pairs.
    ``evidences`` is already a plain list of ``[subject, relation, object]``
    triples, no reshaping needed.
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
            "context": context,
            "supporting_facts": supporting_facts,
            "evidences": [list(triple) for triple in row["evidences"]],
        })
    return records


def _normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields this benchmark needs, in HotpotQA's own frozen-
    sample case shape (``level`` has no 2WikiMultihopQA equivalent -- left
    empty, same as ``import_hotpotqa_nebula_tenant.py``'s own
    ``case.get("level", "")`` default -- so the HotpotQA importer/benchmark
    scripts work against this sample completely unchanged)."""
    return {
        "qid": record["_id"],
        "question": record["question"].strip(),
        "answer": record["answer"].strip(),
        "type": record.get("type", ""),
        "level": "",
        "context": [[title, list(sentences)] for title, sentences in record.get("context", [])],
        "supporting_facts": [[title, idx] for title, idx in record.get("supporting_facts", [])],
        "evidences": record.get("evidences", []),
    }


def freeze_sample(records: list[dict[str, Any]], *, sample_size: int = 100) -> list[dict[str, Any]]:
    """Deterministic sample covering all four question types, with no
    randomness -- same round-robin-by-group approach as HotpotQA's own
    ``freeze_sample`` (grouped by ``type`` only; 2WikiMultihopQA has no
    ``level`` dimension)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record.get("type", ""), []).append(record)
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
    source_url: str = VALIDATION_URL,
) -> Path:
    if sample_path.exists():
        return sample_path
    download_validation(raw_path, url=source_url)
    records = _load_raw_records(raw_path)
    if not records:
        raise ValueError(f"2WikiMultihopQA source at {raw_path} is empty or malformed")
    sample = freeze_sample(records, sample_size=sample_size)
    if len(sample) < sample_size:
        raise ValueError(f"Only {len(sample)} eligible questions, requested {sample_size}")
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = sample_path.with_suffix(sample_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(sample, indent=2, sort_keys=False, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(sample_path)
    sample_path.chmod(0o444)
    return sample_path


def load_twowiki_cases(path: Path = DEFAULT_SAMPLE_PATH) -> list[dict[str, Any]]:
    """Load a frozen 2WikiMultihopQA sample: question, context passages, and gold answer."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze a deterministic 2WikiMultihopQA validation sample")
    parser.add_argument("--raw-path", type=Path, default=DEFAULT_RAW_PATH)
    parser.add_argument("--dest", type=Path, default=DEFAULT_SAMPLE_PATH)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--source-url", default=VALIDATION_URL)
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
    sample = load_twowiki_cases(sample_path)
    types = sorted({row["type"] for row in sample})
    print(json.dumps({
        "status": "OK",
        "path": str(sample_path),
        "sample_size": len(sample),
        "types": types,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
