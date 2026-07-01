#!/usr/bin/env python3
import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agents.graph_search_loop_harness import evaluate_graph_search_loop, load_graph_search_loop_config
from server.aletheia_server import InstanceRepository
from tenant_registry import TenantRegistry


def main():
    parser = argparse.ArgumentParser(description="Evaluate approved graph search quality and emit repair frontier suggestions.")
    parser.add_argument("--tenant", default="maritime-risk")
    parser.add_argument("--tenants-file", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--sample-size", type=int, default=20, help="Number of approved graph objects to evaluate locally; use 0 for all.")
    parser.add_argument("--query-eval-mode", choices=["fast", "real"], default="fast")
    parser.add_argument("--question", action="append", default=[], help="Query sample to route through graph_rag_query_context; can be repeated.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    registry = TenantRegistry.load(args.tenants_file)
    tenant = registry.get(args.tenant)
    repo = InstanceRepository(registry)
    config = load_graph_search_loop_config(args.config)
    report = evaluate_graph_search_loop(
        repo,
        tenant,
        config=config,
        limit=args.limit,
        sample_size=None if args.sample_size == 0 else args.sample_size,
        query_samples=args.question or None,
        query_eval_mode=args.query_eval_mode,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
