#!/usr/bin/env python3
"""Seed 4 hand-designed reasoning scenarios as real, runnable Reasoning
tasks for the KubeBlocks GitHub pilot tenant. Each scenario's center node is
a real vertex confirmed via GraphInstanceRepository.full_graph() against the
live imported dataset (not synthetic), and each maps to one of the
"action" ontology artifacts registered by scripts/register_kubeblocks_
github_actions.py.

Uses ReasoningRepository.create_question_task -- the same entry point the
Reasoning screen's "+ Ask question" button calls -- so these tasks are
indistinguishable from ones a user created by hand, and show up in the
Reasoning screen's "From Graph" tab immediately. Each task is run once via
run_task right after creation, so a finding is already materialized rather
than left to run on first click.

Run: python scripts/seed_kubeblocks_github_reasoning_scenarios.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from aletheia.core.tenant_registry import TenantRegistry  # noqa: E402
from aletheia.interfaces.api.repositories.instance import InstanceRepository  # noqa: E402
from aletheia.interfaces.api.repositories.reasoning import ReasoningRepository  # noqa: E402

TENANT_ID = "kubeblocks-github-v1"

# One scenario per action in scripts/register_kubeblocks_github_actions.py.
SCENARIOS: list[dict] = [
    {
        "action": "EscalateSlowPullRequestReview",
        "question": (
            "For this pull request, how many reviewers were requested, and does the gap "
            "between when review was requested and when the PR closed suggest this reviewer "
            "load pattern should trigger earlier escalation next time?"
        ),
        "center_node": "PullRequest:kb_pr_10246",
    },
    {
        "action": "FlagRegressionRiskForFile",
        "question": (
            "Which other commits and pull requests are transitively connected to this file "
            "through shared file touches, and could be affected by a new change to it?"
        ),
        "center_node": "File:kb_file_config_crd_bases_apps_kubeblocks_io_clusters_yaml",
    },
    {
        "action": "SuggestAssigneeForIssue",
        "question": (
            "Based on who has authored commits and pull requests labeled kind_bug, who should "
            "be suggested as an assignee for this issue?"
        ),
        "center_node": "Issue:kb_issue_10056",
    },
    {
        "action": "RequestIssueLinkForOrphanedPullRequest",
        "question": (
            "This pull request was merged but has no CLOSES edge to any issue -- is this "
            "undocumented work that needs a traceability link?"
        ),
        "center_node": "PullRequest:kb_pr_10233",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=TENANT_ID)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "0")  # deterministic, no live LLM calls needed to seed/run these

    registry = TenantRegistry.load()
    tenant = registry.get(args.tenant_id)

    instance_repository = InstanceRepository(registry, ensure_schema=False)
    reasoning_repository = ReasoningRepository(registry, instance_repository, ensure_schema=False)
    instance_repository.reasoning_repository = reasoning_repository

    for scenario in SCENARIOS:
        print(f"\n=== {scenario['action']} ===")
        print(f"  question: {scenario['question']}")
        print(f"  center_node: {scenario['center_node']}")
        result = reasoning_repository.create_question_task(
            tenant,
            {
                "question": scenario["question"],
                "scope": {"center_node": scenario["center_node"], "depth": args.depth, "limit": args.limit},
            },
        )
        task_key = result["task"]["canonical_key"]
        print(f"  task_key: {task_key}")
        run_result = reasoning_repository.run_task(tenant, task_key)
        print(f"  run approved={run_result.get('approved')} findings={len(run_result.get('findings') or [])}")
        for finding in run_result.get("findings") or []:
            print(f"    finding: {finding.get('title')}")

    print("\nDone. Open the Reasoning screen's \"From Graph\" tab for tenant "
          f"{args.tenant_id!r} to see all {len(SCENARIOS)} seeded tasks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
