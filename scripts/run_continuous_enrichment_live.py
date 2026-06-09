#!/usr/bin/env python3
import argparse
import json
import sys
import threading
import time
from datetime import datetime
from urllib import parse, request


def _url(base, path, tenant):
    qs = parse.urlencode({"tenant": tenant})
    return f"{base.rstrip('/')}{path}?{qs}"


def _post_json(url, payload, timeout=None):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url, timeout=20):
    with request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _event_key(event):
    return "|".join(
        str(event.get(key) or "")
        for key in ("created_at", "type", "frontier_key", "query", "run_key")
    )


def _print_event(event):
    etype = event.get("type")
    created = event.get("created_at") or _stamp()
    if etype == "frontier_selected":
        keys = event.get("selected_keys") or []
        print(f"[{created}] frontier_selected count={event.get('selected_count', len(keys))}")
        for key in keys:
            print(f"  frontier: {key}")
    elif etype == "query_search_executed":
        print(
            f"[{created}] query "
            f"frontier={event.get('frontier_key')} "
            f"level={event.get('granularity')} "
            f"results={event.get('result_count')} "
            f"accepted={event.get('accepted_for_trust_filter_count')}"
        )
        print(f"  keywords: {event.get('query')}")
        if event.get("request_url"):
            print(f"  request: {event.get('request_url')}")
    elif etype == "query_ladder_coarsened":
        print(
            f"[{created}] query_ladder_coarsened "
            f"frontier={event.get('frontier_key')} "
            f"reason={event.get('reason')} "
            f"next={event.get('next_granularity')}"
        )
    elif etype == "graph_changed":
        print(
            f"[{created}] graph_changed run={event.get('run_key')} "
            f"proposed={event.get('proposed_count')} "
            f"new_frontier={event.get('new_frontier_count')} "
            f"formal_write={event.get('formal_graph_write')}"
        )
    elif etype == "cycle_completed":
        print(
            f"[{created}] cycle_completed run={event.get('run_key')} "
            f"status={event.get('status')} "
            f"frontier_used={event.get('frontier_used_count')} "
            f"trusted_sources={event.get('trusted_source_count')} "
            f"proposed={event.get('proposed_count')} "
            f"skipped={event.get('skipped_source_count')}"
        )
    elif etype == "autopilot_triggered":
        print(
            f"[{created}] autopilot_triggered "
            f"session={event.get('autopilot_session_key')} "
            f"candidate_findings={event.get('candidate_findings')}"
        )
    elif etype:
        print(f"[{created}] {etype} {json.dumps(event, ensure_ascii=False, sort_keys=True)}")
    sys.stdout.flush()


def _session_events(session):
    if not isinstance(session, dict):
        return []
    return session.get("latest_events") or ((session.get("config") or {}).get("latest_events") or [])


def _print_run_summary(result):
    cycle = result.get("cycle") or {}
    run = result.get("run") or cycle
    session = result.get("session") or {}
    print(
        f"[{_stamp()}] run_summary "
        f"run={cycle.get('run_key') or run.get('run_key')} "
        f"status={cycle.get('status') or run.get('status')} "
        f"proposed={cycle.get('proposed_count') if cycle.get('proposed_count') is not None else run.get('proposed_count')} "
        f"returned={cycle.get('returned_element_count')} "
        f"findings={cycle.get('finding_count') if cycle.get('finding_count') is not None else run.get('finding_count')} "
        f"next_frontier={cycle.get('next_frontier_count')} "
        f"session_status={session.get('status')}"
    )
    if cycle.get("source_trust"):
        trust = cycle.get("source_trust") or {}
        print(
            f"  source_trust: accepted={trust.get('accepted')} skipped={trust.get('skipped')} "
            f"allow_all_public_sources={trust.get('allow_all_public_sources')}"
        )
    blockers = result.get("extraction_blockers") or run.get("extraction_blockers") or cycle.get("extraction_blockers") or {}
    if blockers:
        print(f"  blockers: {json.dumps(blockers, ensure_ascii=False, sort_keys=True)[:1200]}")
    for event in cycle.get("events") or []:
        if event.get("type") in {"frontier_selected", "query_search_executed", "query_ladder_coarsened", "no_new_proposals", "no_frontier_stop", "no_trusted_sources_stop"}:
            _print_event(event)
    for trace in run.get("expansion_trace") or []:
        frontier = trace.get("frontier") or {}
        print(
            f"  trace frontier={frontier.get('key') or trace.get('frontier_key')} "
            f"queries={len(trace.get('search_trace') or [])} "
            f"trusted_sources={len(trace.get('trusted_sources') or [])} "
            f"extracted={len(trace.get('extracted_candidates') or [])}"
        )
        for source in (trace.get("trusted_sources") or [])[:8]:
            print(f"    source: {source.get('url') or source.get('source_url') or source}")
    sys.stdout.flush()


def poll_events(base, tenant, session_key, stop_event, seen, poll_seconds):
    session_url = _url(base, f"/api/enrichment/sessions/{parse.quote(session_key, safe='')}", tenant)
    while not stop_event.is_set():
        try:
            data = _get_json(session_url, timeout=20)
            session = data.get("session") if isinstance(data.get("session"), dict) else data
            for event in _session_events(session):
                key = _event_key(event)
                if key not in seen:
                    seen.add(key)
                    _print_event(event)
        except Exception as exc:
            print(f"[{_stamp()}] trace_poll_error {exc}")
            sys.stdout.flush()
        stop_event.wait(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="Run continuous enrichment cycles and print live query trace.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8772")
    parser.add_argument("--tenant", default="maritime-risk")
    parser.add_argument("--session-key", default="continuous:maritime-risk:us-iran-impact:mvp")
    parser.add_argument("--sleep-seconds", type=float, default=30)
    parser.add_argument("--poll-seconds", type=float, default=3)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run forever")
    parser.add_argument("--trigger-autopilot", action="store_true")
    args = parser.parse_args()

    run_url = _url(
        args.base_url,
        f"/api/enrichment/sessions/{parse.quote(args.session_key, safe='')}/run-cycle",
        args.tenant,
    )
    seen = set()
    cycle = 0
    print(f"[{_stamp()}] live_runner_start session={args.session_key} tenant={args.tenant}")
    sys.stdout.flush()
    try:
        session_url = _url(args.base_url, f"/api/enrichment/sessions/{parse.quote(args.session_key, safe='')}", args.tenant)
        data = _get_json(session_url, timeout=20)
        session = data.get("session") if isinstance(data.get("session"), dict) else data
        for event in _session_events(session):
            seen.add(_event_key(event))
        print(f"[{_stamp()}] trace_baseline events_seen={len(seen)}")
        sys.stdout.flush()
    except Exception as exc:
        print(f"[{_stamp()}] trace_baseline_error {exc}")
        sys.stdout.flush()
    while args.max_cycles <= 0 or cycle < args.max_cycles:
        cycle += 1
        stop_event = threading.Event()
        poller = threading.Thread(
            target=poll_events,
            args=(args.base_url, args.tenant, args.session_key, stop_event, seen, args.poll_seconds),
            daemon=True,
        )
        print(f"[{_stamp()}] cycle_start #{cycle}")
        sys.stdout.flush()
        poller.start()
        try:
            result = _post_json(run_url, {"trigger_autopilot": args.trigger_autopilot}, timeout=None)
            _print_run_summary(result)
        except KeyboardInterrupt:
            print(f"[{_stamp()}] interrupted")
            stop_event.set()
            poller.join(timeout=5)
            return 130
        except Exception as exc:
            print(f"[{_stamp()}] cycle_error #{cycle}: {exc}")
            sys.stdout.flush()
        finally:
            stop_event.set()
            poller.join(timeout=5)
        if args.max_cycles > 0 and cycle >= args.max_cycles:
            break
        print(f"[{_stamp()}] cycle_sleep seconds={args.sleep_seconds}")
        sys.stdout.flush()
        time.sleep(args.sleep_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
