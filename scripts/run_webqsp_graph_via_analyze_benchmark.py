#!/usr/bin/env python3
"""Run WebQSP's graph-native tenant through the REAL ReasoningEngine.analyze()
path, via GraphInstanceRepository -- a regression/sanity check against the
retired SQL tenant's internal baseline (86/100, per reports/webqsp-task83-sota-gate.md),
NOT a SOTA claim (neither this nor that baseline has been run against the
official held-out test split).
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))
sys.path.append(str(ROOT / "agents"))
sys.path.append(str(ROOT / "scripts"))

from reasoning_engine import ReasoningEngine  # noqa: E402
from graph_instance_repository import GraphInstanceRepository  # noqa: E402
from import_hotpotqa_nebula_tenant import EDGE_TYPE, TAG_NAME  # noqa: E402
from import_webqsp_graph_tenant import DEFAULT_SPACE  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402

DEFAULT_TENANT_ID = "webqsp-graph-v1"
DEFAULT_OBJECT_TYPE = "entity"
DEFAULT_CASES = ROOT / "benchmarks" / "webqsp" / "webqsp_graph_cases.json"
DEFAULT_RESULTS = ROOT / "reports" / "webqsp-graph-via-analyze-results.json"


def answer_hit(result: dict[str, Any] | None, case: dict[str, Any]) -> bool:
    if not result:
        return False
    serialized = json.dumps(result, ensure_ascii=False).lower()
    candidates = [*(case.get("answer_entities") or []), *(case.get("answers") or [])]
    for value in candidates:
        text_value = str(value or "").strip().lower()
        if text_value and text_value in serialized:
            return True
    return False


def _prefixed(object_type: str, vertex_id: str) -> str:
    return f"{object_type}:{vertex_id}"


def run_case(
    engine: ReasoningEngine, tenant: str, object_type: str, case: dict[str, Any], *, depth: int, limit: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    center_node = case.get("center_node")
    if not center_node:
        return {
            "qid": case["qid"], "question": case["question"], "center_node": "",
            "answer_hit": False, "analyze_returned": False, "no_center_node": True,
            "latency_ms": 0.0, "error": "no center_node",
        }
    try:
        additional = [_prefixed(object_type, n) for n in (case.get("additional_center_nodes") or [])]
        result = engine.analyze(
            tenant, _prefixed(object_type, center_node), question=case["question"],
            depth=depth, limit=limit, additional_center_nodes=additional or None,
        )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": center_node,
            "answer_hit": answer_hit(result, case), "analyze_returned": result is not None,
            "no_center_node": False, "latency_ms": latency_ms, "error": "",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": center_node,
            "answer_hit": False, "analyze_returned": False, "no_center_node": False,
            "latency_ms": latency_ms, "error": str(exc),
        }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality_results = [r for r in results if not r.get("no_center_node") and not r.get("error")]
    total = len(quality_results)
    latencies = sorted(float(r["latency_ms"]) for r in quality_results) if quality_results else []
    p95_index = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1)))) if latencies else 0
    hit_count = sum(1 for r in quality_results if r["answer_hit"])
    return {
        "attempted_questions": len(results),
        "total_questions": total,
        "answer_hit_count": hit_count,
        "answer_hit_rate": hit_count / total if total else 0.0,
        "analyze_returned_count": sum(1 for r in quality_results if r["analyze_returned"]),
        "avg_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
        "p95_latency_ms": latencies[p95_index] if latencies else 0.0,
        "error_count": sum(1 for r in results if r.get("error")),
        "results": results,
    }


def run_benchmark(
    *,
    tenant_id: str,
    cases_path: Path,
    space: str,
    object_type: str,
    nebula_ip: str,
    nebula_port: int,
    nebula_user: str,
    nebula_password: str,
    relation_catalog_db_url: str | None,
    relation_catalog_scope: str,
    max_questions: int | None,
    depth: int,
    limit: int,
    progress_every: int = 5,
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if max_questions:
        cases = cases[:max_questions]

    repo = GraphInstanceRepository(
        space=space, tag_name=TAG_NAME, edge_type=EDGE_TYPE, object_type=object_type,
        nebula_ip=nebula_ip, nebula_port=nebula_port, nebula_user=nebula_user, nebula_password=nebula_password,
        relation_catalog_db_url=relation_catalog_db_url, relation_catalog_scope=relation_catalog_scope,
    )
    engine = ReasoningEngine(repo)

    results = []
    try:
        total = len(cases)
        for idx, case in enumerate(cases, start=1):
            result = run_case(engine, tenant_id, object_type, case, depth=depth, limit=limit)
            results.append(result)
            if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
                print(
                    f"[WebQSP-via-analyze] {idx}/{total} qid={result['qid']} "
                    f"answer_hit={int(result['answer_hit'])} latency_ms={result['latency_ms']:.0f}"
                )
    finally:
        repo.close()

    return summarize(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run WebQSP graph tenant through ReasoningEngine.analyze()")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_ID)
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--object-type", default=DEFAULT_OBJECT_TYPE)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-password", default="nebula")
    parser.add_argument("--relation-catalog-db-url", default=default_metadata_db_url())
    parser.add_argument("--relation-catalog-scope", default="webqsp")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--results-json", type=Path, default=DEFAULT_RESULTS)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Opt-in by design (see reasoning_engine.py._get_llm_planner) so tests
    # never make network calls -- default it on for this real benchmark run
    # so relation planning and multi-center answers aren't silently
    # keyword-only just because nobody exported the flag.
    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "1")
    args = build_parser().parse_args(argv)
    report = run_benchmark(
        tenant_id=args.tenant,
        cases_path=args.cases_json,
        space=args.space,
        object_type=args.object_type,
        nebula_ip=args.nebula_ip,
        nebula_port=args.nebula_port,
        nebula_user=args.nebula_user,
        nebula_password=args.nebula_password,
        relation_catalog_db_url=args.relation_catalog_db_url,
        relation_catalog_scope=args.relation_catalog_scope,
        max_questions=args.max_questions,
        depth=args.depth,
        limit=args.limit,
    )
    args.results_json.parent.mkdir(parents=True, exist_ok=True)
    args.results_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"\n{'='*60}")
    print("  WebQSP via ReasoningEngine.analyze() (Graph-native)")
    print(f"{'='*60}")
    print(f"Attempted questions: {report['attempted_questions']}")
    print(f"Quality questions:   {report['total_questions']}")
    print(f"Answer hit: {report['answer_hit_count']}/{report['total_questions']} ({report['answer_hit_rate']:.1%})")
    print(f"analyze() returned:  {report['analyze_returned_count']}/{report['total_questions']}")
    print(f"Latency ms: avg={report['avg_latency_ms']:.0f} p95={report['p95_latency_ms']:.0f}")
    print(f"Errors: {report['error_count']}")
    print(f"results_json={args.results_json}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
