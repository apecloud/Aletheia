#!/usr/bin/env python3
"""Evaluate reasoning-process loop health for a tenant."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.reasoning_loop_harness import evaluate_reasoning_loop, load_reasoning_loop_config


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Aletheia reasoning loop health.")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--metadata-db-url", default=os.environ.get("ALETHEIA_METADATA_DB_URL", "sqlite:///metadata.db"))
    parser.add_argument("--config")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = evaluate_reasoning_loop(
        args.metadata_db_url,
        args.tenant,
        config=load_reasoning_loop_config(args.config),
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        verdict = report["verdict"]
        metrics = report["metrics"]
        print(f"Reasoning loop: {report['loop_id']} tenant={report['tenant']} status={report['status']}")
        print(f"Next focus: {verdict['next_focus']}")
        print("Reasons: " + "; ".join(verdict.get("reasons") or []))
        print(
            "Metrics: "
            f"runs={metrics['run_count']} completed={metrics['completed_run_ratio']:.2f} "
            f"eval_pass={metrics['eval_pass_ratio']:.2f} evidence={metrics['evidence_path_ratio']:.2f} "
            f"structured={metrics['structured_response_ratio']:.2f} findings={metrics['finding_count']}"
        )
        print(f"Repair items: {report['repair_plan']['item_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
