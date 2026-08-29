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
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from aletheia.reasoning.engine import ReasoningEngine  # noqa: E402
from aletheia.graph_store.instance_repository import GraphInstanceRepository  # noqa: E402
from import_hotpotqa_nebula_tenant import DEFAULT_CASES, DEFAULT_SPACE  # noqa: E402
from hotpotqa_graph_judge import GraphHitJudge  # noqa: E402
from aletheia.llms.planner import LLMPlanner  # noqa: E402
from aletheia.enrichment.entity_resolver import SOURCE_SPACE_DESCRIPTION  # noqa: E402
from aletheia.ontology.label_embeddings import find_nearest_labels  # noqa: E402
from run_hotpotqa_nebula_e2e_benchmark import gold_candidates  # noqa: E402
from aletheia.core.tenant_registry import default_metadata_db_url  # noqa: E402

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
    from hotpotqa_sample_eval import fallback_answer_matches

    for gold in golds:
        judgement = judge.judge(question, gold, facts)
        if judgement.used_fallback:
            if any(fallback_answer_matches(gold, f["label"]) for f in facts if f.get("label")):
                return True, True
            continue
        if judgement.hit:
            return True, False
    return False, False


def _prefixed_with_real_type(repo: GraphInstanceRepository, fallback_object_type: str, vertex_id: str) -> str:
    """``analyze()``/``entity_config`` are keyed by each vertex's REAL typed
    node-type name now (e.g. "Team", "Person"), not one fixed generic
    "entity" type -- ``entity_config.get(object_type.lower())`` in
    ``reasoning_engine._gather_center_data`` returns None (silently skipping
    the whole center) for any type name it doesn't recognize. Resolve the
    vertex's actual tag from Nebula rather than assume a fixed type."""
    vertex = repo._fetch_vertex(vertex_id)
    real_type = vertex["types"][0] if vertex and vertex.get("types") else fallback_object_type
    return _prefixed(real_type, vertex_id)


# Widened from 5 after gleaning roughly doubled this tenant's vertex count
# (560->1119) -- a fixed top-K window is more likely to crowd out the
# correct match as the candidate pool grows, and widening the net here is
# cheap (bigger candidate list in one LLM prompt) compared to re-tuning
# distance thresholds or extraction itself. 20 is calibrated for the
# gleaning + is_literal-dedup-fix checkpoint config (923-1230 vertices,
# measured 83% effective hit rate on the 100-question HotpotQA benchmark
# -- see import_hotpotqa_nebula_tenant.py's --skip-mentioned-entities
# flag). If a tenant is imported WITH mentioned_entities enabled (roughly
# doubles vertex count again, ~1900-2100), 40 measured better there
# (though still net-neutral-to-negative overall -- see that flag's help
# text) -- bump this back up if reusing this script against such a tenant.
_CANDIDATE_RETRIEVAL_K = 20

# ontology_label_embeddings.DEFAULT_MAX_DISTANCE (0.40) was calibrated for
# short "{type} {label}" strings on both sides of the comparison -- a short
# guessed mention (e.g. "Ida") against a full LLM-generated description
# sentence ("The Ida is a type of sword...") sits systematically farther
# apart in embedding space than two short labels do, even for the correct
# match. Measured directly against real post-rebuild cases: correct-match
# distances ranged 0.36-0.63 (e.g. "Ida"->0.577, "Libby Mitchell"->0.613,
# "Masherbrum"->0.53), while 0.40 filtered nearly half of all questions down
# to zero candidates. 0.75 comfortably covers the observed range with
# margin -- the verify_entity_candidate step downstream is the real
# precision gate (see _resolve_live_centers's docstring), so a wider
# retrieval net here just gives it more to correctly accept/reject from.
_DESCRIPTION_MATCH_MAX_DISTANCE = 0.75


def _resolve_live_centers(pg_session, tenant_id: str, question: str, planner: LLMPlanner) -> list[str]:
    """Genuine "question in, graph search out" entry point, two stages:

    1. Ask an LLM which real-world entity/entities the bare question refers
       to, using its own knowledge to resolve indirect/descriptive
       references (see LLMPlanner.extract_question_entity_mentions's
       docstring) -- preserves multi-center comparison questions (LLM can
       name 2+ entities; pure whole-question embedding similarity could
       only ever return one).
    2. For each guessed name, retrieve the top-K nearest candidates from
       this tenant's LLM-generated entity-description embeddings
       (source_space="nebula_vertex_description", built by the post-import
       description pass in import_hotpotqa_nebula_tenant.py -- richer than
       a bare "{type} {label}" match, see llm_planner.EntityDescriptionResult's
       docstring) and ask the LLM to confirm which (if any) is the same
       real-world entity -- rather than trusting a single nearest-neighbor
       argmin, which fails whenever the guessed name's exact spelling
       doesn't match the graph's label (see EntityCandidateVerification's
       docstring). A hallucinated name with no real match in the top-K, or
       that the LLM declines to confirm, is simply dropped -- no
       closed-world validation needed."""
    mentions = planner.extract_question_entity_mentions(question).mentions
    resolved = []
    for mention in mentions:
        candidates = find_nearest_labels(
            pg_session, tenant_id, mention, k=_CANDIDATE_RETRIEVAL_K, source_space=SOURCE_SPACE_DESCRIPTION,
            max_distance=_DESCRIPTION_MATCH_MAX_DISTANCE,
        )
        if not candidates:
            continue
        verification = planner.verify_entity_candidate(mention, [c["label"] for c in candidates])
        if verification.chosen_index is None:
            continue
        node_id = candidates[verification.chosen_index]["node_id"]
        if node_id not in resolved:
            resolved.append(node_id)
    return resolved


def _resolve_decomposed_centers(
    pg_session, tenant_id: str, question: str, planner: LLMPlanner,
) -> list[dict[str, Any]] | None:
    """Question-decomposition entry point -- borrowed from StepChain
    GraphRAG (arXiv:2510.02827). Splits ``question`` into sub-questions via
    ``LLMPlanner.decompose_question``, then resolves EACH sub-question's own
    centers via ``_resolve_live_centers`` (already generic over any input
    text, not specific to the original whole question). Returns ``None``
    when decomposition itself is a no-op (an already-atomic question, or
    the LLM call fell back) -- signals the caller to use the existing
    non-decomposed resolution path instead, so atomic questions see zero
    behavior change from this feature existing.

    Each returned entry is ``{"sub_question": str, "center_node": str,
    "additional_center_nodes": [str, ...]}`` with bare (not yet type-
    prefixed) node ids, same shape ``_resolve_live_centers`` itself
    returns -- an entry whose sub-question never resolved keeps
    ``center_node=""`` rather than being dropped, so the caller/
    ``ReasoningEngine.analyze_decomposed`` can still make use of whatever
    DID resolve."""
    decomposition = planner.decompose_question(question)
    if decomposition.used_fallback or len(decomposition.sub_questions) <= 1:
        return None

    entries = []
    for sub_question in decomposition.sub_questions:
        resolved = _resolve_live_centers(pg_session, tenant_id, sub_question, planner)
        entries.append({
            "sub_question": sub_question,
            "center_node": resolved[0] if resolved else "",
            "additional_center_nodes": resolved[1:],
        })
    return entries


def run_case(
    engine: ReasoningEngine, repo: GraphInstanceRepository, tenant: str, object_type: str,
    case: dict[str, Any], judge: GraphHitJudge, *, depth: int, limit: int,
    pg_session=None, live_entity_linking_tenant_id: str | None = None, llm_planner: LLMPlanner | None = None,
    enable_decomposition: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    center_node = case.get("center_node")
    additional_center_nodes = case.get("additional_center_nodes") or []
    decomposed_entries = None

    if live_entity_linking_tenant_id is not None:
        if enable_decomposition:
            sub_question_centers = _resolve_decomposed_centers(
                pg_session, live_entity_linking_tenant_id, case["question"], llm_planner,
            )
            # None means decomposition was a no-op (atomic question, or the
            # LLM call fell back) -- falls through to the exact existing
            # non-decomposed path below, same as if this whole feature
            # didn't exist. Also falls through if NOTHING resolved across
            # every sub-question -- no worse than the non-decomposed path
            # would have done with the same underlying entity-linking gap.
            if sub_question_centers is not None and any(e["center_node"] for e in sub_question_centers):
                decomposed_entries = [
                    {
                        "sub_question": entry["sub_question"],
                        "center_node": (
                            _prefixed_with_real_type(repo, object_type, entry["center_node"])
                            if entry["center_node"] else ""
                        ),
                        "additional_center_nodes": [
                            _prefixed_with_real_type(repo, object_type, n)
                            for n in entry["additional_center_nodes"]
                        ],
                    }
                    for entry in sub_question_centers
                ]
        if decomposed_entries is None:
            resolved = _resolve_live_centers(pg_session, live_entity_linking_tenant_id, case["question"], llm_planner)
            center_node = resolved[0] if resolved else None
            additional_center_nodes = resolved[1:]

    if decomposed_entries is None and not center_node:
        return {
            "qid": case["qid"], "question": case["question"], "center_node": "",
            "graph_hit": False, "analyze_returned": False, "no_center_node": True,
            "latency_ms": 0.0, "error": "no center_node (extraction failed)",
        }
    try:
        if decomposed_entries is not None:
            result = engine.analyze_decomposed(
                tenant, case["question"], decomposed_entries, depth=depth, limit=limit,
            )
            reported_center = ",".join(e["center_node"] for e in decomposed_entries if e["center_node"])
        else:
            additional = [
                _prefixed_with_real_type(repo, object_type, n) for n in additional_center_nodes
            ]
            result = engine.analyze(
                tenant, _prefixed_with_real_type(repo, object_type, center_node), question=case["question"],
                depth=depth, limit=limit, additional_center_nodes=additional or None,
            )
            reported_center = center_node
        golds = gold_candidates(case)
        hit, used_fallback = analyze_hit(result, case["question"], golds, judge)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": reported_center,
            "graph_hit": hit, "used_fallback_scoring": used_fallback,
            "analyze_returned": result is not None, "no_center_node": False,
            "latency_ms": latency_ms, "error": "", "decomposed": decomposed_entries is not None,
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"], "question": case["question"], "center_node": center_node or "",
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


class _ThreadResources:
    """Nebula's session (via GraphInstanceRepository's client) and a
    SQLAlchemy Session are NOT safe for concurrent use from multiple
    threads -- unlike LLM calls (I/O-bound, each with their own executor),
    a single shared repo/pg_session would corrupt or serialize underneath
    concurrent run_case() calls. Each worker THREAD gets its own full set,
    built lazily on first use in that thread and reused for every
    subsequent case dispatched to it (threading.local, not one-per-task --
    ThreadPoolExecutor reuses a fixed pool of worker threads across many
    submitted tasks). All created instances are tracked in a shared list
    (list.append is atomic under the GIL) so the main thread can close
    every one of them after the pool shuts down."""

    def __init__(self, *, repo_kwargs, live_entity_linking, relation_catalog_db_url, relation_catalog_scope):
        self._local = threading.local()
        self._repo_kwargs = repo_kwargs
        self._live_entity_linking = live_entity_linking
        self._relation_catalog_db_url = relation_catalog_db_url
        self._relation_catalog_scope = relation_catalog_scope
        self.created: list[Any] = []

    def get(self):
        if not hasattr(self._local, "repo"):
            repo = GraphInstanceRepository(**self._repo_kwargs)
            engine = ReasoningEngine(repo)
            judge = GraphHitJudge()
            pg_session = None
            llm_planner = None
            if self._live_entity_linking:
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker

                db_url = self._relation_catalog_db_url or default_metadata_db_url()
                pg_session = sessionmaker(bind=create_engine(db_url))()
                llm_planner = LLMPlanner()
            self._local.repo = repo
            self._local.engine = engine
            self._local.judge = judge
            self._local.pg_session = pg_session
            self._local.llm_planner = llm_planner
            self.created.append((repo, pg_session))
        return self._local.repo, self._local.engine, self._local.judge, self._local.pg_session, self._local.llm_planner

    def close_all(self):
        for repo, pg_session in self.created:
            repo.close()
            if pg_session is not None:
                pg_session.close()


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
    live_entity_linking: bool = False,
    concurrency: int = 1,
    enable_decomposition: bool = False,
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if max_questions:
        cases = cases[:max_questions]

    live_entity_linking_tenant_id = relation_catalog_scope if live_entity_linking else None
    resources = _ThreadResources(
        repo_kwargs=dict(
            space=space,
            nebula_ip=nebula_ip, nebula_port=nebula_port, nebula_user=nebula_user, nebula_password=nebula_password,
            relation_catalog_db_url=relation_catalog_db_url, relation_catalog_scope=relation_catalog_scope,
        ),
        live_entity_linking=live_entity_linking,
        relation_catalog_db_url=relation_catalog_db_url,
        relation_catalog_scope=relation_catalog_scope,
    )

    def _run_one(case: dict[str, Any]) -> dict[str, Any]:
        repo, engine, judge, pg_session, llm_planner = resources.get()
        return run_case(
            engine, repo, tenant_id, object_type, case, judge, depth=depth, limit=limit,
            pg_session=pg_session, live_entity_linking_tenant_id=live_entity_linking_tenant_id,
            llm_planner=llm_planner, enable_decomposition=enable_decomposition,
        )

    total = len(cases)
    results = []
    try:
        if concurrency <= 1:
            for idx, case in enumerate(cases, start=1):
                result = _run_one(case)
                results.append(result)
                if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
                    print(
                        f"[HotpotQA-via-analyze] {idx}/{total} qid={result['qid']} "
                        f"graph_hit={int(result['graph_hit'])} latency_ms={result['latency_ms']:.0f}",
                        flush=True,
                    )
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                future_to_case = {pool.submit(_run_one, case): case for case in cases}
                completed = 0
                for future in as_completed(future_to_case):
                    result = future.result()
                    results.append(result)
                    completed += 1
                    if progress_every and (completed == 1 or completed % progress_every == 0 or completed == total):
                        print(
                            f"[HotpotQA-via-analyze] {completed}/{total} qid={result['qid']} "
                            f"graph_hit={int(result['graph_hit'])} latency_ms={result['latency_ms']:.0f}",
                            flush=True,
                        )
    finally:
        resources.close_all()

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
    parser.add_argument(
        "--live-entity-linking", action="store_true",
        help=(
            "Resolve each case's center_node from the raw question text via embedding "
            "nearest-neighbor lookup (agents/ontology_label_embeddings.py) against the "
            "tenant's existing nebula_vertex dedup embeddings, instead of using the "
            "frozen cases.json's precomputed center_node (which came from the extraction "
            "LLM directly reading the passages). Drops additional_center_nodes."
        ),
    )
    parser.add_argument(
        "--concurrency", type=int, default=1,
        help=(
            "Run this many cases concurrently (each case's LLM/Nebula calls are I/O-bound "
            "and independent). Each worker thread gets its own Nebula/Postgres connections "
            "(see _ThreadResources) -- 1 (default) preserves the old serial behavior and "
            "in-order progress log."
        ),
    )
    parser.add_argument(
        "--enable-decomposition", action="store_true",
        help=(
            "Split each question into independent sub-questions (LLMPlanner.decompose_question, "
            "borrowed from StepChain GraphRAG arXiv:2510.02827) and resolve/gather/derive each "
            "sub-question independently before merging into a final answer "
            "(ReasoningEngine.analyze_decomposed), instead of resolving the whole question as one "
            "unit. Requires --live-entity-linking (decomposition re-resolves entities from each "
            "sub-question's own text). A no-op on already-atomic questions -- falls through to "
            "the existing non-decomposed path with zero behavior change."
        ),
    )
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
        live_entity_linking=args.live_entity_linking,
        concurrency=args.concurrency,
        enable_decomposition=args.enable_decomposition,
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
    print(f"results_json={args.results_json}", flush=True)
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
