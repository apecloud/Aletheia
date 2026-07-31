#!/usr/bin/env python3
"""Run HotpotQA's Nebula tenant through the REAL ReasoningEngine.analyze()
path, via GraphInstanceRepository -- not the standalone benchmark harness
(``run_hotpotqa_nebula_e2e_benchmark.py``), which never touches
``analyze()``/``InstanceRepository`` at all.

This is the unification proof from the SQL-retirement plan: today
``analyze()`` never runs on Nebula. If this reaches comparable accuracy to
the standalone harness's proven 90%+, the graph-native retrieval core
(Phase 0's ``_gather_center_data`` rewrite + Phase 1's
``GraphInstanceRepository``) is a real, working replacement for the SQL
``InstanceRepository`` path, not just a parallel experiment.

Mirrors ``run_hotpotqa_kg_e2e_benchmark.py`` (the SQL-tenant sibling)
structurally, and reuses this session's already-validated
``GraphHitJudge``/``graph_hit`` scoring from
``run_hotpotqa_nebula_e2e_benchmark.py`` rather than reinventing scoring.
"""

from __future__ import annotations

import argparse
import json
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
from import_hotpotqa_nebula_tenant import DEFAULT_CASES, DEFAULT_SPACE, EDGE_TYPE, TAG_NAME  # noqa: E402
from hotpotqa_graph_judge import GraphHitJudge  # noqa: E402
from run_hotpotqa_nebula_e2e_benchmark import gold_candidates  # noqa: E402
from tenant_registry import default_metadata_db_url  # noqa: E402

DEFAULT_TENANT_ID = "hotpotqa-graph-v1"
DEFAULT_OBJECT_TYPE = "entity"
DEFAULT_RESULTS = ROOT / "reports" / "hotpotqa-nebula-via-analyze-results.json"


def _prefixed(object_type: str, vertex_id: str) -> str:
    return f"{object_type}:{vertex_id}"


_DIAGNOSTIC_KEY_FACT_LABELS = {"question_path_plan", "llm_question_path_plan"}


def _judge_facts_from_analyze_result(result: dict[str, Any]) -> list[dict[str, str]]:
    """GraphHitJudge expects a list of real {"label", "rel"} facts (like the
    standalone harness's traverse() output) -- NOT one giant serialized blob,
    which drowns the actual answer in structural JSON noise and derails the
    LLM's judgment. Extracts the same answer surfaces the standalone
    harness's run_multi_center_case scores (the derived answer, its
    supporting centers) plus this single-center path's own key facts.

    Also includes the center's own label as a fact (single- and multi-center
    alike) -- sometimes the center's own identity already IS the answer
    (e.g. center="Yoruba people" for "which group ... use the Ida?"), the
    same reason gather_facts() in the standalone harness fetches each
    center's own label rather than only what's reached *from* it.
    """
    facts: list[dict[str, str]] = []
    metrics = result.get("metrics") or {}
    if metrics.get("label"):
        facts.append({"label": str(metrics["label"]), "rel": "center_label"})
    if metrics.get("answer"):
        facts.append({"label": str(metrics["answer"]), "rel": "derived_answer"})
    for lbl in metrics.get("supporting_labels") or []:
        facts.append({"label": str(lbl), "rel": "supporting_center"})
    for center in metrics.get("centers") or []:
        if center.get("label"):
            facts.append({"label": str(center["label"]), "rel": "center_label"})
    for kf in result.get("key_facts") or []:
        if kf.get("label") in _DIAGNOSTIC_KEY_FACT_LABELS:
            continue
        facts.append({"label": str(kf.get("value", "")), "rel": str(kf.get("label", ""))})
    return facts


def analyze_hit(result: dict[str, Any] | None, question: str, golds: list[str], judge: GraphHitJudge) -> tuple[bool, bool]:
    """Does analyze()'s output surface the gold answer?

    Scored via GraphHitJudge (name-variant/relation-as-answer aware) against
    the same shape of facts the standalone harness already validated --
    falls back to a plain normalized substring check over those facts'
    labels if the judge itself is unavailable. Returns (hit, used_fallback).
    """
    if not result:
        return False, False
    facts = _judge_facts_from_analyze_result(result)
    from hotpotqa_sample_eval import normalize_answer

    for gold in golds:
        judgement = judge.judge(question, gold, facts)
        if judgement.used_fallback:
            norm_gold = normalize_answer(gold)
            if norm_gold and any(norm_gold in normalize_answer(f["label"]) or normalize_answer(f["label"]) in norm_gold for f in facts if f.get("label")):
                return True, True
            continue
        if judgement.hit:
            return True, False
    return False, False


def run_case(
    engine: ReasoningEngine, tenant: str, object_type: str, case: dict[str, Any], judge: GraphHitJudge,
    *, depth: int, limit: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    center_node = case.get("center_node")
    if not center_node:
        return {
            "qid": case["qid"], "question": case["question"], "center_node": "",
            "graph_hit": False, "analyze_returned": False, "no_center_node": True,
            "latency_ms": 0.0, "error": "no center_node (extraction failed)",
        }
    try:
        additional = [_prefixed(object_type, n) for n in (case.get("additional_center_nodes") or [])]
        result = engine.analyze(
            tenant, _prefixed(object_type, center_node), question=case["question"],
            depth=depth, limit=limit, additional_center_nodes=additional or None,
        )
        golds = gold_candidates(case)
        hit, used_fallback = analyze_hit(result, case["question"], golds, judge)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": center_node,
            "graph_hit": hit, "used_fallback_scoring": used_fallback,
            "analyze_returned": result is not None, "no_center_node": False,
            "latency_ms": latency_ms, "error": "",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": center_node,
            "graph_hit": False, "used_fallback_scoring": False, "analyze_returned": False,
            "no_center_node": False, "latency_ms": latency_ms, "error": str(exc),
        }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality_results = [r for r in results if not r.get("no_center_node") and not r.get("error")]
    total = len(quality_results)
    latencies = sorted(float(r["latency_ms"]) for r in quality_results) if quality_results else []
    p95_index = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1)))) if latencies else 0
    hit_count = sum(1 for r in quality_results if r["graph_hit"])
    return {
        "attempted_questions": len(results),
        "total_questions": total,
        "graph_hit_count": hit_count,
        "graph_hit_rate": hit_count / total if total else 0.0,
        "analyze_returned_count": sum(1 for r in quality_results if r["analyze_returned"]),
        "fallback_scoring_count": sum(1 for r in quality_results if r.get("used_fallback_scoring")),
        "avg_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
        "p95_latency_ms": latencies[p95_index] if latencies else 0.0,
        "no_center_node_count": sum(1 for r in results if r.get("no_center_node")),
        "error_count": sum(1 for r in results if r.get("error") and not r.get("no_center_node")),
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
    progress_every: int = 10,
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
    judge = GraphHitJudge()

    results = []
    try:
        total = len(cases)
        for idx, case in enumerate(cases, start=1):
            result = run_case(engine, tenant_id, object_type, case, judge, depth=depth, limit=limit)
            results.append(result)
            if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
                print(
                    f"[HotpotQA-via-analyze] {idx}/{total} qid={result['qid']} "
                    f"graph_hit={int(result['graph_hit'])} latency_ms={result['latency_ms']:.0f}"
                )
    finally:
        repo.close()

    return summarize(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run HotpotQA Nebula tenant through ReasoningEngine.analyze()")
    parser.add_argument("--tenant", default=DEFAULT_TENANT_ID)
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--object-type", default=DEFAULT_OBJECT_TYPE)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-password", default="nebula")
    parser.add_argument("--relation-catalog-db-url", default=default_metadata_db_url())
    parser.add_argument("--relation-catalog-scope", default="hotpotqa")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--results-json", type=Path, default=DEFAULT_RESULTS)
    return parser


def main(argv: list[str] | None = None) -> int:
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
    print("  HotpotQA via ReasoningEngine.analyze() (Graph-native)")
    print(f"{'='*60}")
    print(f"Attempted questions: {report['attempted_questions']}")
    print(f"Quality questions:   {report['total_questions']}")
    print(f"Graph hit: {report['graph_hit_count']}/{report['total_questions']} ({report['graph_hit_rate']:.1%})")
    print(f"analyze() returned:  {report['analyze_returned_count']}/{report['total_questions']}")
    print(f"Scored via substring fallback (LLM judge unavailable): {report['fallback_scoring_count']}")
    print(f"Latency ms: avg={report['avg_latency_ms']:.0f} p95={report['p95_latency_ms']:.0f}")
    print(f"No center_node (extraction failed): {report['no_center_node_count']}")
    print(f"Errors: {report['error_count']}")
    print(f"results_json={args.results_json}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
