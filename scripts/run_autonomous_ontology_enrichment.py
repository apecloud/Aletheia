#!/usr/bin/env python3
import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from datetime import datetime

from agents.enrichment_loop_harness import evaluate_enrichment_loop, load_loop_config
from server.aletheia_server import InstanceRepository
from tenant_registry import TenantRegistry


DEFAULT_OBJECTIVE = (
    "Continuously enrich approved ontology objects until each concrete object has "
    "evidence-backed properties, semantic items, and at least one approved relation; "
    "automatically review deterministic low-risk proposals using review settings."
)


def stamp():
    return datetime.utcnow().isoformat()


def autonomous_config(args):
    loop_config = load_loop_config(args.loop_config)
    return {
        "research_provider": "gpt_researcher",
        "research_mode": "frontier_enrichment",
        "execution_goal": args.objective or DEFAULT_OBJECTIVE,
        "loop_engineering": {
            "enabled": True,
            "config_path": args.loop_config,
            "loop_id": loop_config.get("loop_id"),
            "coverage_targets": loop_config.get("coverage_targets") or {},
            "latency_targets": loop_config.get("latency_targets") or {},
            "next_action_policy": loop_config.get("next_action_policy") or {},
        },
        "max_frontier": args.max_frontier,
        "max_results_per_query": args.max_results_per_query,
        "max_iterations": args.max_iterations,
        "gpt_researcher_max_report_chars": args.gpt_researcher_max_report_chars,
        "budget": {
            "max_cycles": None,
            "max_frontier_per_cycle": args.max_frontier,
            "max_results_per_query": args.max_results_per_query,
            "max_iterations_per_cycle": args.max_iterations,
        },
        "instance_coverage_min_edges": args.min_relation_edges,
        "instance_coverage_min_enrichment_items": args.min_enrichment_items,
        "instance_coverage_per_type_limit": args.per_type_limit,
        "instance_coverage_detail_fallback": args.instance_coverage_detail_fallback,
        "frontier_cooldown_minutes": args.frontier_cooldown_minutes,
        "frontier_selector": args.frontier_selector,
        "frontier_max_per_cluster": args.frontier_max_per_cluster,
        "auto_approve_low_duplicate_proposals": True,
        "auto_approve_min_confidence": args.auto_approve_min_confidence,
        "auto_approve_max_duplicate_score": args.auto_approve_max_duplicate_score,
        "auto_review_similar_proposals": True,
        "auto_review_llm_verifier": args.auto_review_llm_verifier,
        "auto_reject_similarity_threshold": args.auto_reject_similarity_threshold,
        "auto_review_reviewer": "Continuous Enrichment Agent",
        "stop_policy": {
            "pause_on_no_frontier": False,
            "pause_on_budget_exhausted": False,
        },
    }


def print_json_event(event):
    print(json.dumps({"ts": stamp(), **event}, ensure_ascii=False, sort_keys=True), flush=True)


def append_jsonl(path, event):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": stamp(), **event}, ensure_ascii=False, sort_keys=True) + "\n")


def loop_report(args, tenant, run_key=None):
    config = load_loop_config(args.loop_config)
    return evaluate_enrichment_loop(
        tenant.metadata_db_url,
        tenant.tenant_id,
        run_key=run_key,
        config=config,
        session_key=args.session_key,
    )


def run_cycle_child(queue, tenant_id, session_key, force, trigger_autopilot):
    try:
        registry = TenantRegistry.load()
        tenant = registry.get(tenant_id)
        repo = InstanceRepository(registry)
        result = repo.run_continuous_enrichment_cycle(
            tenant,
            session_key,
            {
                "force": force,
                "trigger_autopilot": trigger_autopilot,
            },
        )
        queue.put({"ok": True, "result": result})
    except BaseException as exc:
        queue.put({"ok": False, "error": str(exc), "error_type": type(exc).__name__})


def recover_session(tenant_id, session_key, reason):
    try:
        registry = TenantRegistry.load()
        tenant = registry.get(tenant_id)
        repo = InstanceRepository(registry)
        session = repo.update_continuous_enrichment_session_status(tenant, session_key, "idle")
        return {"ok": bool(session), "reason": reason}
    except Exception as exc:
        return {"ok": False, "reason": reason, "error": str(exc)}


def debug_snapshot(tenant_id, session_key):
    try:
        from sqlalchemy import create_engine, text

        registry = TenantRegistry.load()
        tenant = registry.get(tenant_id)
        repo = InstanceRepository(registry)
        session_payload = repo.continuous_enrichment_session(tenant, session_key) or {}
        session_data = session_payload.get("session") or {}
        engine = create_engine(tenant.metadata_db_url)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT run_key, status, started_at, finished_at, proposed_count,
                           safety_profile_json
                    FROM aletheia_iterative_graph_enrichment_runs
                    WHERE project_id = :tenant_id
                    ORDER BY started_at DESC, id DESC
                    LIMIT 1
                    """
                ),
                {"tenant_id": tenant.tenant_id},
            ).mappings().first()
        latest_run = None
        if row:
            safety = json.loads(row["safety_profile_json"] or "{}")
            latest_run = {
                "run_key": row["run_key"],
                "status": row["status"],
                "started_at": str(row["started_at"]),
                "finished_at": str(row["finished_at"]) if row["finished_at"] else None,
                "proposed_count": row["proposed_count"],
                "last_runtime_event": safety.get("last_runtime_event"),
            }
        return {
            "session_status": session_data.get("status"),
            "cycle_count": session_data.get("cycle_count"),
            "last_run_key": session_data.get("last_run_key"),
            "last_started_at": (session_data.get("config") or {}).get("last_started_at"),
            "last_finished_at": (session_data.get("config") or {}).get("last_finished_at"),
            "frontier_queue_count": ((session_data.get("runtime_state") or {}).get("frontier_queue") or {}).get("total_count"),
            "latest_event": ((session_data.get("latest_events") or [None])[-1]),
            "latest_run": latest_run,
        }
    except Exception as exc:
        return {"snapshot_error": str(exc)}


def run_cycle_with_timeout(args):
    queue = mp.Queue(maxsize=1)
    proc = mp.Process(
        target=run_cycle_child,
        args=(queue, args.tenant, args.session_key, args.force, args.trigger_autopilot),
    )
    proc.start()
    deadline = time.time() + max(1, int(args.cycle_timeout_seconds))
    next_trace = time.time() + max(1, int(args.trace_poll_seconds))
    while proc.is_alive() and time.time() < deadline:
        wait_for = min(1.0, max(0.0, deadline - time.time()))
        proc.join(wait_for)
        if proc.is_alive() and time.time() >= next_trace:
            snapshot = debug_snapshot(args.tenant, args.session_key)
            print_json_event(
                {
                    "type": "cycle_heartbeat",
                    "child_pid": proc.pid,
                    "elapsed_seconds": round(max(0.0, time.time() - (deadline - max(1, int(args.cycle_timeout_seconds)))), 3),
                    "remaining_seconds": round(max(0.0, deadline - time.time()), 3),
                    "snapshot": snapshot,
                }
            )
            latest_run = (snapshot or {}).get("latest_run") or {}
            if snapshot.get("session_status") == "idle" and latest_run.get("status") == "completed":
                proc.terminate()
                proc.join(10)
                return {
                    "ok": True,
                    "result": {
                        "cycle": {
                            "status": "completed",
                            "run_key": latest_run.get("run_key"),
                            "proposed_count": latest_run.get("proposed_count"),
                            "returned_element_count": None,
                            "frontier_used": [],
                            "next_frontier_count": snapshot.get("frontier_queue_count"),
                            "events": [snapshot.get("latest_event")] if snapshot.get("latest_event") else [],
                        }
                    },
                }
            next_trace = time.time() + max(1, int(args.trace_poll_seconds))
    if proc.is_alive():
        proc.terminate()
        proc.join(10)
        if proc.is_alive():
            proc.kill()
            proc.join(10)
        recovery = recover_session(
            args.tenant,
            args.session_key,
            f"cycle exceeded timeout {args.cycle_timeout_seconds}s",
        )
        return {
            "timeout": True,
            "exitcode": proc.exitcode,
            "recovery": recovery,
        }
    if not queue.empty():
        return queue.get()
    if proc.exitcode == 0:
        return {"ok": False, "error": "cycle process exited without result", "exitcode": proc.exitcode}
    return {"ok": False, "error": "cycle process failed before returning result", "exitcode": proc.exitcode}


def main():
    parser = argparse.ArgumentParser(description="Run autonomous ontology coverage enrichment cycles.")
    parser.add_argument("--tenant", default="maritime-risk")
    parser.add_argument("--session-key", default="continuous:maritime-risk:default")
    parser.add_argument("--sleep-seconds", type=float, default=45)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run forever")
    parser.add_argument("--objective", default=DEFAULT_OBJECTIVE)
    parser.add_argument("--configure", action="store_true", help="Apply autonomous review and coverage settings before running.")
    parser.add_argument("--reset-frontier", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--trigger-autopilot", action="store_true")
    parser.add_argument("--cycle-timeout-seconds", type=int, default=900)
    parser.add_argument("--trace-poll-seconds", type=int, default=30)
    parser.add_argument("--max-frontier", type=int, default=1)
    parser.add_argument("--max-results-per-query", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=1)
    parser.add_argument("--gpt-researcher-max-report-chars", type=int, default=12000)
    parser.add_argument("--min-relation-edges", type=int, default=1)
    parser.add_argument("--min-enrichment-items", type=int, default=2)
    parser.add_argument("--per-type-limit", type=int, default=300)
    parser.add_argument("--instance-coverage-detail-fallback", action="store_true")
    parser.add_argument("--frontier-cooldown-minutes", type=int, default=0)
    parser.add_argument("--frontier-selector", default="deterministic")
    parser.add_argument("--frontier-max-per-cluster", type=int, default=2)
    parser.add_argument("--auto-approve-min-confidence", type=float, default=0.8)
    parser.add_argument("--auto-approve-max-duplicate-score", type=float, default=0.5)
    parser.add_argument("--auto-reject-similarity-threshold", type=float, default=0.92)
    parser.add_argument("--auto-review-llm-verifier", action="store_true")
    parser.add_argument("--semantic-llm-provider", choices=["gemini", "openrouter"], default=None)
    parser.add_argument("--semantic-openrouter-model", default=None)
    parser.add_argument("--dedup-verifier-provider", choices=["gemini", "openrouter"], default=None)
    parser.add_argument("--dedup-openrouter-model", default=None)
    parser.add_argument("--loop-config", default="config/enrichment_loop.maritime-risk.json")
    parser.add_argument("--loop-report-file", default=None)
    parser.add_argument("--evaluate-only", action="store_true", help="Evaluate the latest or specified run without starting a new cycle.")
    parser.add_argument("--run-key", default=None, help="Run key to evaluate with --evaluate-only.")
    args = parser.parse_args()
    if args.auto_review_llm_verifier:
        os.environ.setdefault("ALETHEIA_DEDUP_LLM_VERIFIER", "1")
    else:
        os.environ["ALETHEIA_DEDUP_LLM_VERIFIER"] = "0"
    if args.semantic_llm_provider:
        os.environ["ALETHEIA_RESEARCH_SEMANTIC_LLM_PROVIDER"] = args.semantic_llm_provider
    if args.semantic_openrouter_model:
        os.environ["ALETHEIA_RESEARCH_SEMANTIC_OPENROUTER_MODEL"] = args.semantic_openrouter_model
    if args.dedup_verifier_provider:
        os.environ["ALETHEIA_DEDUP_VERIFIER_PROVIDER"] = args.dedup_verifier_provider
    if args.dedup_openrouter_model:
        os.environ["ALETHEIA_DEDUP_VERIFIER_OPENROUTER_MODEL"] = args.dedup_openrouter_model

    registry = TenantRegistry.load()
    tenant = registry.get(args.tenant)
    repo = InstanceRepository(registry)
    if args.evaluate_only:
        report = loop_report(args, tenant, run_key=args.run_key)
        event = {"type": "loop_report", "cycle": None, "report": report}
        print_json_event(event)
        append_jsonl(args.loop_report_file, event)
        return 0
    if args.configure:
        body = autonomous_config(args)
        if args.reset_frontier:
            body["reset_frontier_visit_state"] = True
        session = repo.configure_continuous_enrichment_session(tenant, args.session_key, body)
        if not session:
            raise SystemExit(f"session not found: {args.session_key}")
        print_json_event(
            {
                "type": "configured",
                "tenant": args.tenant,
                "session_key": args.session_key,
                "config": {
                    key: (session["session"]["config"] or {}).get(key)
                    for key in (
                        "instance_coverage_min_edges",
                        "instance_coverage_min_enrichment_items",
                        "auto_approve_low_duplicate_proposals",
                        "auto_review_similar_proposals",
                        "auto_review_llm_verifier",
                    )
                },
            }
        )

    cycle = 0
    print_json_event({"type": "runner_start", "tenant": args.tenant, "session_key": args.session_key})
    while args.max_cycles <= 0 or cycle < args.max_cycles:
        cycle += 1
        print_json_event({"type": "cycle_start", "cycle": cycle})
        try:
            cycle_outcome = run_cycle_with_timeout(args)
            if cycle_outcome.get("timeout"):
                print_json_event(
                    {
                        "type": "cycle_timeout",
                        "cycle": cycle,
                        "timeout_seconds": args.cycle_timeout_seconds,
                        "exitcode": cycle_outcome.get("exitcode"),
                        "recovery": cycle_outcome.get("recovery"),
                    }
                )
            elif not cycle_outcome.get("ok"):
                print_json_event(
                    {
                        "type": "cycle_error",
                        "cycle": cycle,
                        "error": cycle_outcome.get("error"),
                        "error_type": cycle_outcome.get("error_type"),
                        "exitcode": cycle_outcome.get("exitcode"),
                    }
                )
            else:
                result = cycle_outcome.get("result") or {}
                cycle_result = result.get("cycle") or {}
                events = cycle_result.get("events") or []
                print_json_event(
                    {
                        "type": "cycle_done",
                        "cycle": cycle,
                        "status": cycle_result.get("status"),
                        "run_key": cycle_result.get("run_key"),
                        "proposed_count": cycle_result.get("proposed_count"),
                        "returned_element_count": cycle_result.get("returned_element_count"),
                        "frontier_used": [
                            item.get("key") for item in cycle_result.get("frontier_used") or []
                        ],
                        "next_frontier_count": cycle_result.get("next_frontier_count"),
                        "auto_review": [
                            {
                                "type": event.get("type"),
                                "reviewed_count": event.get("reviewed_count"),
                                "skipped_count": event.get("skipped_count"),
                            }
                            for event in events
                            if event.get("type") in {"auto_review_similar_proposals", "auto_approve_low_duplicate_proposals"}
                        ],
                    }
                )
                loop_event = {
                    "type": "loop_report",
                    "cycle": cycle,
                    "report": loop_report(args, tenant, run_key=cycle_result.get("run_key")),
                }
                print_json_event(loop_event)
                append_jsonl(args.loop_report_file, loop_event)
        except KeyboardInterrupt:
            print_json_event({"type": "interrupted"})
            return 130
        except Exception as exc:
            print_json_event({"type": "cycle_error", "cycle": cycle, "error": str(exc)})
        if args.max_cycles > 0 and cycle >= args.max_cycles:
            break
        time.sleep(max(1.0, args.sleep_seconds))
    print_json_event({"type": "runner_stop", "cycles": cycle})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
