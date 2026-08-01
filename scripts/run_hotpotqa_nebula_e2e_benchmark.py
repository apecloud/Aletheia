#!/usr/bin/env python3
"""Run HotpotQA's closed-world local graph through a real Nebula nGQL traversal.

Sibling of ``run_hotpotqa_kg_e2e_benchmark.py`` (which uses
``ReasoningEngine.analyze()` over SQL) -- this variant queries Nebula
directly with native nGQL, verified against the live cluster first (see
plan verification step 0): a single multi-hop ``GO ... OVER RELATION YIELD``
query returns each reached vertex's id, its own ``label`` property (via
``properties($$).label``), and the relation that reached it -- all in one
round trip, no id-to-label reverse lookup needed (unlike the SQL variant,
where ``analyze()``'s narrative output sometimes only showed neighbor ids).
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

from nebula3.common.ttypes import Value  # noqa: E402
from graph_db_client import NebulaGraphClient  # noqa: E402
from hotpotqa_sample_eval import fallback_answer_matches  # noqa: E402
from hotpotqa_graph_judge import GraphHitJudge  # noqa: E402
from import_hotpotqa_nebula_tenant import DEFAULT_CASES, DEFAULT_SPACE, EDGE_TYPE, TAG_NAME  # noqa: E402
from reasoning_engine import ReasoningEngine  # noqa: E402
from llm_planner import LLMPlanner  # noqa: E402
from hotpotqa_frozen_sample import DEFAULT_SAMPLE_PATH  # noqa: E402

DEFAULT_RESULTS = ROOT / "reports" / "hotpotqa-nebula-e2e-results.json"


def _escape(value: str) -> str:
    return str(value).replace('"', "'")


def _value_as_str(value: Value) -> str:
    if value.getType() != Value.SVAL:
        return ""
    return value.get_sVal().decode("utf-8")


def traverse(client: NebulaGraphClient, center_node: str, *, depth: int) -> list[dict[str, str]]:
    """Multi-hop traversal from center_node, returning reached vertices' own
    labels.

    Uses BIDIRECT: the extraction step (``HotpotQARelationExtractor``) picks
    each triple's subject/object direction from whichever way the source
    sentence reads (e.g. "Jacksonville station serves the Silver Meteor"
    makes Jacksonville station the subject) -- a forward-only ``GO ... OVER
    RELATION`` from ``center_node`` only follows edges where center_node is
    the stored source, so it silently misses facts the extractor happened
    to anchor on the OTHER entity as subject. BIDIRECT follows both
    directions, matching what path-finding (``gather_bidirect_edges``)
    already does.

    Deliberately does NOT read ``$$``/``$^`` inline: verified against the
    live cluster that in a BIDIRECT multi-step traversal, the same logical
    edge gets yielded twice (once per direction) and ``$$``/``$^`` bind to
    the query's traversal direction, NOT to ``src(edge)``/``dst(edge)``'s
    stored direction -- pairing them produced wrong labels (e.g. the
    Jacksonville station vertex briefly labeled "Silver Meteor"). Only
    ``src(edge)``/``dst(edge)`` are trustworthy here (same as
    ``gather_bidirect_edges``); each neighbor's real label is fetched with
    a dedicated, unambiguous ``fetch_label()`` call instead.
    """
    query = (
        f'GO 1 TO {depth} STEPS FROM "{_escape(center_node)}" OVER {EDGE_TYPE} BIDIRECT '
        f'YIELD DISTINCT src(edge) AS src, dst(edge) AS dst, {EDGE_TYPE}.relation_label AS rel;'
    )
    result = client.execute_query(query)
    rows = []
    seen_ids: set[str] = set()
    for row in result.rows():
        values = row.values
        src = _value_as_str(values[0])
        dst = _value_as_str(values[1])
        rel = _value_as_str(values[2])
        for vid in (src, dst):
            if vid and vid != center_node and vid not in seen_ids:
                seen_ids.add(vid)
                label = fetch_label(client, vid)
                if label:
                    rows.append({"id": vid, "label": label, "rel": rel})
    return rows


def gather_bidirect_edges(client: NebulaGraphClient, center_node: str, *, depth: int) -> list[dict[str, str]]:
    """Edges (both directions merged) reachable from center_node within
    ``depth`` hops, shaped as plain ``{source, target, label}`` dicts --
    the same generic shape ``ReasoningEngine._find_path_between_centers``
    expects, so the storage-agnostic BFS in reasoning_engine.py can be
    reused here unchanged rather than reimplemented for Nebula."""
    query = (
        f'GO 1 TO {depth} STEPS FROM "{_escape(center_node)}" OVER {EDGE_TYPE} BIDIRECT '
        f'YIELD DISTINCT src(edge) AS src, dst(edge) AS dst, {EDGE_TYPE}.relation_label AS rel;'
    )
    result = client.execute_query(query)
    edges = []
    for row in result.rows():
        values = row.values
        edges.append({
            "source": _value_as_str(values[0]),
            "target": _value_as_str(values[1]),
            "label": _value_as_str(values[2]),
        })
    return edges


def find_path_between_centers(
    client: NebulaGraphClient, center_a: str, center_b: str, *, depth: int, allowed_titles: set[str] | None = None
) -> list[dict[str, str]] | None:
    """Is there a real graph path between two named centers? Tried before
    any LLM reasoning for questions naming multiple specific subjects --
    reuses ReasoningEngine's storage-agnostic BFS (it only needs plain
    source/target/label edge dicts, not a live SQL repo).

    ``allowed_titles`` (the question's own context passage titles, when
    given) excludes edges through LITERAL-valued vertices from the BFS --
    e.g. two unrelated people both having an "occupation" edge to the same
    shared literal vertex "novelist" is not a real connection between them,
    it's two independent facts that happen to reuse the same attribute
    value. Without this filter, BFS treats that shared literal vertex as a
    bridge and declares "path found", short-circuiting before
    derive_relational_answer ever sees each center's own (often far more
    relevant) facts -- confirmed on real cases: a genuine "pen_name ->
    Walter Ericson" fact on one center was never considered because a
    spurious "both are novelists" path was found first. A vertex is a real
    entity (never filtered) iff its own label is one of the question's own
    context titles verbatim -- exactly the same is_literal test the
    extractor already applies to each triple's object.
    """
    edges = gather_bidirect_edges(client, center_a, depth=depth)
    if allowed_titles:
        vertex_ids = {e["source"] for e in edges} | {e["target"] for e in edges}
        vertex_ids.discard(center_a)
        vertex_ids.discard(center_b)
        labels = {vid: fetch_label(client, vid) for vid in vertex_ids}
        labels[center_a] = fetch_label(client, center_a)
        labels[center_b] = fetch_label(client, center_b)
        edges = [
            e for e in edges
            if labels.get(e["source"], "") in allowed_titles and labels.get(e["target"], "") in allowed_titles
        ]
    return ReasoningEngine._find_path_between_centers(center_a, center_b, [], edges)


def fetch_label(client: NebulaGraphClient, vid: str) -> str:
    """Fetch a vertex's own label (used for the center node itself, since
    ``traverse`` only visits vertices *reached from* it, not itself --
    sometimes the center node's own label already is the answer, e.g.
    center="Yoruba people" for "which group ... use the Ida?")."""
    query = f'FETCH PROP ON {TAG_NAME} "{_escape(vid)}" YIELD {TAG_NAME}.label AS label;'
    result = client.execute_query(query)
    rows = result.rows()
    if not rows or not rows[0].values or rows[0].values[0].getType() != Value.SVAL:
        return ""
    return rows[0].values[0].get_sVal().decode("utf-8")


def gold_candidates(case: dict[str, Any]) -> list[str]:
    """Every string that counts as a correct answer for this case.

    HotpotQA's own ``answer`` field is sometimes a full-sentence span
    ("with other campuses located in Chicago and Doha, Qatar") or a
    fuller name form ("Joseph John Campbell") than what a graph-derived
    entity label naturally produces ("Joseph Campbell"). ``canonical_answer``
    is a small, hand-curated set of such aliases -- verified independently
    against real-world facts (not copied from whatever this pipeline
    happened to output) before being added to hotpot_nebula_cases.json, so
    this stays an honest alias table rather than a backdoor that launders
    wrong answers into hits.
    """
    candidates = [case.get("answer", "")]
    canonical = case.get("canonical_answer", "")
    if canonical:
        candidates.append(canonical)
    return [c for c in candidates if c]


def graph_hit(neighbors: list[dict[str, str]], gold_answer: str) -> bool:
    """Substring fallback: does the gold answer match any reached vertex's
    own label, OR the relation that reached it? Normalized (lowercase,
    strip punctuation/articles), checked in both substring directions.

    Checking ``rel`` too matters for questions whose answer names a
    relationship rather than an entity (e.g. gold="brother" against a fact
    shaped "is_older_brother_of -> Paolo Cannavaro" -- the object is the
    *other* person's name, not the relation word "brother"; only the
    relation label itself contains the answer).
    """
    if not gold_answer:
        return False
    for neighbor in neighbors:
        for field in ("label", "rel"):
            value = neighbor.get(field, "")
            if value and fallback_answer_matches(gold_answer, value):
                return True
    return False


def gather_facts(client: NebulaGraphClient, center_nodes: list[str], *, depth: int) -> list[dict[str, str]]:
    """Traverse from every given center node and merge results, including
    each center's own label (``traverse`` only visits vertices *reached
    from* a center, not the center itself -- see ``fetch_label``'s
    docstring). Questions naming multiple specific subjects pass multiple
    center nodes (topic plus each additional entity mention) so facts about
    all of them are available to the judge, not just whichever one became
    the primary topic entity.
    """
    facts: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for center in center_nodes:
        for neighbor in traverse(client, center, depth=depth):
            if neighbor["id"] not in seen_ids:
                seen_ids.add(neighbor["id"])
                facts.append(neighbor)
        if center not in seen_ids:
            label = fetch_label(client, center)
            if label:
                seen_ids.add(center)
                facts.append({"id": center, "label": label, "rel": "__self__"})
    return facts


def run_multi_center_case(
    client: NebulaGraphClient,
    case: dict[str, Any],
    planner: LLMPlanner,
    judge: GraphHitJudge,
    center_nodes: list[str],
    *,
    depth: int,
    allowed_titles: set[str] | None = None,
) -> dict[str, Any]:
    """Questions naming 2+ specific subjects (whatever the reason -- a
    relationship check, a shared-trait question, a bridge chain that just
    happens to reference a second title). Path-finding is tried first (a
    real connection between the named entities, if one exists in the local
    graph); when no path exists, each center's facts (excluding its own
    label -- the fix for the answer-leakage bug found earlier, where both
    named entities' own names were always present in the evidence and let
    the judge "win" without real reasoning) are handed to
    derive_relational_answer for facts-only LLM reasoning.

    ``derive_relational_answer`` doesn't presuppose the answer picks one of
    the given centers -- it returns a free-text ``answer`` plus whichever
    center(s) support it (one, several, or all), so questions asking "what
    do they have in common" score the same way as "which one is X": the
    derived answer is just handed to the same GraphHitJudge bridge questions
    use, rather than special-cased into a single winner label.
    """
    golds = gold_candidates(case)
    primary = center_nodes[0]

    # entity_mentions can return more than 1 extra candidate when the
    # question text ambiguously matches several context titles that share a
    # stripped base (e.g. "Battle of Manila (1574)" and "Battle of Manila
    # (1945)" both normalize to "battle of manila") -- check every pair, not
    # just the first two, so a real path isn't missed just because of list
    # order.
    for other in center_nodes[1:]:
        path = find_path_between_centers(client, primary, other, depth=depth, allowed_titles=allowed_titles)
        if path:
            # The connection itself (e.g. "acquired_by") is often not the
            # answer -- the answer is usually an ATTRIBUTE of one of the
            # connected entities (a location, a district, a role), found on
            # ONE of the path's own vertices, not the vertices' own names.
            # A real case: path chris_pine --starring--> just_my_luck found
            # correctly, but gold ("Christopher Whitelaw Pine") only matches
            # via name-variant reasoning, and separately, a path connecting
            # a school to its namesake still needs the SCHOOL's own
            # "located_in_school_district" fact, not just either entity's
            # name -- checking only fetch_label() on the path vertices missed
            # both. gather_facts() (same helper bridge questions use) pulls
            # each path vertex's own facts too, then scores through the same
            # name-variant/relation-aware GraphHitJudge bridge questions use,
            # rather than a bespoke plain-label substring check.
            vertex_ids = {primary, other}
            for edge in path:
                vertex_ids.add(edge["source"])
                vertex_ids.add(edge["target"])
            path_facts = gather_facts(client, sorted(vertex_ids), depth=depth)
            hit = False
            used_fallback_scoring = False
            matched_label = ""
            for gold in golds:
                judgement = judge.judge(case["question"], gold, path_facts)
                if judgement.used_fallback:
                    used_fallback_scoring = True
                    if graph_hit(path_facts, gold):
                        hit = True
                        matched_label = ""
                        break
                    continue
                if judgement.hit:
                    hit = True
                    matched_label = judgement.matched_label
                    break
            return {
                "resolution": "path_found",
                "graph_hit": hit,
                "matched_label": matched_label,
                "neighbor_count": len(path_facts),
                "used_fallback_scoring": used_fallback_scoring,
            }

    centers_facts = []
    for center in center_nodes:
        neighbors = traverse(client, center, depth=depth)
        centers_facts.append({
            "center_node": center,
            "facts": [{"relation": n["rel"], "value": n["label"]} for n in neighbors],
        })

    derivation = planner.derive_relational_answer(case["question"], centers_facts)
    all_facts = [f for c in centers_facts for f in [{"label": fact["value"]} for fact in c["facts"]]]
    if derivation.used_fallback or not derivation.answer:
        # LLM derivation unavailable -- last-resort substring check across
        # the same facts gathered above (no worse than the old scoring,
        # and no self-labels are added here, unlike gather_facts()).
        hit = any(graph_hit(all_facts, gold) for gold in golds)
        return {
            "resolution": "unavailable",
            "graph_hit": hit,
            "matched_label": "",
            "neighbor_count": len(all_facts),
            "used_fallback_scoring": True,
        }

    # Score the derived answer the same way a bridge question's retrieved
    # facts are scored: hand it to GraphHitJudge (name-variant/relation-as-
    # answer/meta-title aware) rather than a bespoke single-label substring
    # check. This also naturally covers "supported by all centers" answers
    # (a shared trait) since the judge only looks at the answer text itself.
    supporting_labels = [fetch_label(client, c) for c in derivation.supporting_center_nodes]
    judge_facts = [{"label": derivation.answer, "rel": "derived_answer"}] + [
        {"label": lbl, "rel": "supporting_center"} for lbl in supporting_labels if lbl
    ]
    hit = False
    used_fallback_scoring = False
    for gold in golds:
        judgement = judge.judge(case["question"], gold, judge_facts)
        if judgement.used_fallback:
            used_fallback_scoring = True
            if graph_hit(judge_facts, gold):
                hit = True
                break
            continue
        if judgement.hit:
            hit = True
            break
    return {
        "resolution": "llm_reasoning",
        "graph_hit": hit,
        "matched_label": derivation.answer,
        "neighbor_count": sum(len(c["facts"]) for c in centers_facts),
        "used_fallback_scoring": used_fallback_scoring,
    }


def run_case(
    client: NebulaGraphClient,
    case: dict[str, Any],
    judge: GraphHitJudge,
    planner: LLMPlanner,
    *,
    depth: int,
    titles_by_qid: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    center_nodes = [n for n in [case.get("center_node")] + list(case.get("additional_center_nodes") or []) if n]
    center_nodes = sorted(set(center_nodes))
    if not center_nodes:
        return {
            "qid": case["qid"],
            "question": case["question"],
            "type": case.get("type", ""),
            "center_node": "",
            "graph_hit": False,
            "resolution": "",
            "neighbor_count": 0,
            "used_fallback_scoring": False,
            "no_center_node": True,
            "latency_ms": 0.0,
            "error": "no center_node (extraction failed)",
        }
    try:
        gold_answer = case.get("answer", "")
        if len(center_nodes) > 1:
            allowed_titles = (titles_by_qid or {}).get(case["qid"])
            outcome = run_multi_center_case(
                client, case, planner, judge, center_nodes, depth=depth, allowed_titles=allowed_titles
            )
        else:
            facts = gather_facts(client, center_nodes, depth=depth)
            judgement = judge.judge(case["question"], gold_answer, facts)
            if judgement.used_fallback:
                hit = any(graph_hit(facts, gold) for gold in gold_candidates(case))
            else:
                hit = judgement.hit
            outcome = {
                "resolution": "judge_fallback" if judgement.used_fallback else "judge",
                "graph_hit": hit,
                "matched_label": judgement.matched_label,
                "neighbor_count": len(facts),
                "used_fallback_scoring": judgement.used_fallback,
            }
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"],
            "question": case["question"],
            "type": case.get("type", ""),
            "center_node": ",".join(center_nodes),
            "graph_hit": outcome["graph_hit"],
            "resolution": outcome["resolution"],
            "matched_label": outcome["matched_label"],
            "neighbor_count": outcome["neighbor_count"],
            "used_fallback_scoring": outcome["used_fallback_scoring"],
            "no_center_node": False,
            "latency_ms": latency_ms,
            "error": "",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "qid": case["qid"],
            "question": case["question"],
            "type": case.get("type", ""),
            "center_node": ",".join(center_nodes),
            "graph_hit": False,
            "resolution": "",
            "neighbor_count": 0,
            "used_fallback_scoring": False,
            "no_center_node": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


def _rate(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    hit_count = sum(1 for r in results if r["graph_hit"])
    return {"count": total, "hit_count": hit_count, "hit_rate": hit_count / total if total else 0.0}


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality_results = [r for r in results if not r.get("no_center_node") and not r.get("error")]
    total = len(quality_results)
    latencies = sorted(float(r["latency_ms"]) for r in quality_results) if quality_results else []
    p95_index = min(len(latencies) - 1, int(round(0.95 * (len(latencies) - 1)))) if latencies else 0
    hit_count = sum(1 for r in quality_results if r["graph_hit"])
    # Split by structure (how many centers this question resolved to), not
    # by HotpotQA's own bridge/comparison label -- a single-center question
    # can be HotpotQA-labeled either way, and so can a multi-center one now
    # that additional_center_nodes comes from entity_mentions rather than
    # case["type"].
    single_center_results = [r for r in quality_results if "," not in (r.get("center_node") or "")]
    multi_center_results = [r for r in quality_results if "," in (r.get("center_node") or "")]
    return {
        "attempted_questions": len(results),
        "total_questions": total,
        "graph_hit_count": hit_count,
        "graph_hit_rate": hit_count / total if total else 0.0,
        "single_center": _rate(single_center_results),
        "multi_center": _rate(multi_center_results),
        "multi_center_resolution_counts": {
            resolution: sum(1 for r in multi_center_results if r.get("resolution") == resolution)
            for resolution in ("path_found", "llm_reasoning", "unavailable")
        },
        "avg_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
        "p95_latency_ms": latencies[p95_index] if latencies else 0.0,
        "no_center_node_count": sum(1 for r in results if r.get("no_center_node")),
        "error_count": sum(1 for r in results if r.get("error") and not r.get("no_center_node")),
        "fallback_scoring_count": sum(1 for r in quality_results if r.get("used_fallback_scoring")),
        "multi_center_count": len(multi_center_results),
        "results": results,
    }


def load_titles_by_qid(raw_sample_path: Path) -> dict[str, set[str]]:
    """Each question's own context passage titles -- the real-entity
    allowlist for path-finding's literal-vertex filter (see
    ``find_path_between_centers``). Loaded straight from the frozen
    HotpotQA sample, no extraction/LLM call needed: a vertex is a real
    entity iff its label is one of these titles verbatim."""
    if not raw_sample_path.exists():
        return {}
    raw_cases = json.loads(raw_sample_path.read_text(encoding="utf-8"))
    return {c["qid"]: {title for title, _ in c.get("context", [])} for c in raw_cases}


def run_benchmark(
    *,
    cases_path: Path,
    space: str,
    nebula_ip: str,
    nebula_port: int,
    nebula_user: str,
    nebula_password: str,
    max_questions: int | None,
    depth: int,
    raw_sample_path: Path | None = None,
    progress_every: int = 10,
) -> dict[str, Any]:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    if max_questions:
        cases = cases[:max_questions]

    titles_by_qid = load_titles_by_qid(raw_sample_path) if raw_sample_path else {}

    judge = GraphHitJudge()
    planner = LLMPlanner()
    client = NebulaGraphClient(ip=nebula_ip, port=nebula_port, user=nebula_user, password=nebula_password, space=space)
    client.connect()
    results = []
    try:
        total = len(cases)
        for idx, case in enumerate(cases, start=1):
            result = run_case(client, case, judge, planner, depth=depth, titles_by_qid=titles_by_qid)
            results.append(result)
            if progress_every and (idx == 1 or idx % progress_every == 0 or idx == total):
                print(
                    f"[HotpotQA-Nebula] {idx}/{total} qid={result['qid']} "
                    f"graph_hit={int(result['graph_hit'])} latency_ms={result['latency_ms']:.0f}"
                )
    finally:
        client.close()

    return summarize(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run HotpotQA Nebula tenant through a real nGQL traversal")
    parser.add_argument("--cases-json", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--space", default=DEFAULT_SPACE)
    parser.add_argument("--nebula-ip", default="127.0.0.1")
    parser.add_argument("--nebula-port", type=int, default=9669)
    parser.add_argument("--nebula-user", default="root")
    parser.add_argument("--nebula-password", default="nebula")
    parser.add_argument("--max-questions", type=int, default=None)
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--results-json", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument(
        "--raw-sample-json", type=Path, default=DEFAULT_SAMPLE_PATH,
        help="Frozen HotpotQA sample this cases-json was materialized from "
             "(supplies each question's own context titles for path-finding's literal-vertex filter)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_benchmark(
        cases_path=args.cases_json,
        space=args.space,
        nebula_ip=args.nebula_ip,
        nebula_port=args.nebula_port,
        nebula_user=args.nebula_user,
        nebula_password=args.nebula_password,
        max_questions=args.max_questions,
        depth=args.depth,
        raw_sample_path=args.raw_sample_json,
    )
    args.results_json.parent.mkdir(parents=True, exist_ok=True)
    args.results_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print(f"\n{'='*60}")
    print("  HotpotQA Nebula-Native Benchmark Report (Aletheia)")
    print(f"{'='*60}")
    print(f"Attempted questions: {report['attempted_questions']}")
    print(f"Quality questions:   {report['total_questions']}")
    print(f"Graph hit: {report['graph_hit_count']}/{report['total_questions']} ({report['graph_hit_rate']:.1%})")
    single_center, multi_center = report["single_center"], report["multi_center"]
    print(f"  Single-center: {single_center['hit_count']}/{single_center['count']} ({single_center['hit_rate']:.1%})")
    print(f"  Multi-center:  {multi_center['hit_count']}/{multi_center['count']} ({multi_center['hit_rate']:.1%})")
    res_counts = report["multi_center_resolution_counts"]
    print(
        f"    resolution: path_found={res_counts['path_found']} "
        f"llm_reasoning={res_counts['llm_reasoning']} unavailable={res_counts['unavailable']}"
    )
    print(f"Scored via substring fallback (LLM judge unavailable): {report['fallback_scoring_count']}")
    print(f"Questions using multi-center traversal: {report['multi_center_count']}")
    print(f"Latency ms: avg={report['avg_latency_ms']:.0f} p95={report['p95_latency_ms']:.0f}")
    print(f"No center_node (extraction failed): {report['no_center_node_count']}")
    print(f"Errors: {report['error_count']}")
    print(f"results_json={args.results_json}")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
