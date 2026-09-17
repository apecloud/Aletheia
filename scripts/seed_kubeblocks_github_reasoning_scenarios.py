#!/usr/bin/env python3
"""Seed hand-designed reasoning scenarios as real, runnable Reasoning tasks
for the KubeBlocks GitHub pilot tenant. Each scenario's center node is a
real vertex confirmed via direct Nebula queries against the live imported
dataset (not synthetic), and each maps to one of the "action" ontology
artifacts registered by scripts/register_kubeblocks_github_actions.py.

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
# Questions are Chinese to match this tenant's established convention (every
# other manually- or scenario-seeded task in kubeblocks-github-v1 asks its
# question in Chinese with scope.language="zh") -- an English question with
# no language set previously produced an English conclusion that looked
# inconsistent against everything else in the tenant.
SCENARIOS: list[dict] = [
    {
        "action": "EscalateSlowPullRequestReview",
        "question": (
            "这个 PR 请求了多少位 reviewer？从请求 review 到 PR 关闭之间的时间间隔，"
            "是否说明这种 reviewer 负载模式下次应该更早升级？"
        ),
        "center_node": "PullRequest:kb_pr_10246",
    },
    {
        "action": "FlagRegressionRiskForFile",
        "question": (
            "哪些其他 commit 和 PR 通过共享的文件改动与这个文件存在传递关联，"
            "可能会受到对它的新改动影响？"
        ),
        "center_node": "File:kb_file_config_crd_bases_apps_kubeblocks_io_clusters_yaml",
    },
    {
        "action": "SuggestAssigneeForIssue",
        "question": (
            "根据谁提交过带 kind_bug 标签的 commit 和 PR，应该为这个 issue 推荐哪位负责人？"
        ),
        "center_node": "Issue:kb_issue_10056",
    },
    {
        "action": "RequestIssueLinkForOrphanedPullRequest",
        "question": (
            "这个 PR 已合并但没有关联到任何 issue 的 CLOSES 边——这是否是需要补充"
            "可追溯链接的未记录工作？"
        ),
        "center_node": "PullRequest:kb_pr_10233",
    },
    {
        "action": "FlagMergeWithoutReviewGovernanceRisk",
        "question": (
            "这个 PR 在从未被请求过 review 的情况下就被合并了——这是否是一个应该触发"
            "追溯审查的治理缺口，未来对它改动过的文件是否应该强制要求 review？"
        ),
        "center_node": "PullRequest:kb_pr_10240",
    },
    {
        "action": "FlagUndocumentedIssueClosure",
        "question": (
            "这个 issue 被关闭时没有关联任何解释修复方式的 PR——已批准图谱能看出它"
            "实际上是如何（或是否）被解决的吗？"
        ),
        "center_node": "Issue:kb_issue_10041",
    },
    {
        "action": "FlagFileOwnershipHotspot",
        "question": (
            "这个文件被改动的 commit 数量几乎是全仓库最多的——是谁在持续修改它，"
            "它是否需要专属负责人或更强的测试覆盖？"
        ),
        "center_node": "File:kb_file_config_crd_bases_apps_kubeblocks_io_clusters_yaml",
    },
    {
        "action": "TriageSeverityLabelBacklog",
        "question": (
            "这个标签在所有暗示严重程度的标签中，关联的未关闭 issue/PR 数量最多——"
            "当前这个标签下的存量是否需要分诊处理？"
        ),
        "center_node": "Label:kb_label_kind_bug",
    },
    {
        "action": "FlagReviewerLoadConcentration",
        "question": (
            "这位贡献者收到的 review 请求数量远超仓库里其他任何人——review 负载是否"
            "过度集中在这一个人身上，是否应该扩大 reviewer 池？"
        ),
        "center_node": "User:kb_user_leon-ape",
    },
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=TENANT_ID)
    parser.add_argument("--depth", type=int, default=1)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--action", action="append", dest="actions",
        help="Only seed the scenario(s) for this action name (repeatable). "
             "create_question_task is not idempotent -- re-running with no filter "
             "re-seeds every scenario as a fresh duplicate task, so use this to "
             "seed only newly-added scenarios against an already-seeded tenant.",
    )
    args = parser.parse_args()

    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "0")  # deterministic, no live LLM calls needed to seed/run these

    registry = TenantRegistry.load()
    tenant = registry.get(args.tenant_id)

    instance_repository = InstanceRepository(registry, ensure_schema=False)
    reasoning_repository = ReasoningRepository(registry, instance_repository, ensure_schema=False)
    instance_repository.reasoning_repository = reasoning_repository

    scenarios = SCENARIOS
    if args.actions:
        wanted = set(args.actions)
        scenarios = [s for s in SCENARIOS if s["action"] in wanted]
        missing = wanted - {s["action"] for s in scenarios}
        if missing:
            print(f"Warning: unknown action name(s), skipped: {sorted(missing)}")

    for scenario in scenarios:
        print(f"\n=== {scenario['action']} ===")
        print(f"  question: {scenario['question']}")
        print(f"  center_node: {scenario['center_node']}")
        result = reasoning_repository.create_question_task(
            tenant,
            {
                "question": scenario["question"],
                "scope": {
                    "center_node": scenario["center_node"], "depth": args.depth, "limit": args.limit,
                    "language": scenario.get("language", "zh"),
                },
            },
        )
        task_key = result["task"]["canonical_key"]
        print(f"  task_key: {task_key}")
        run_result = reasoning_repository.run_task(tenant, task_key)
        print(f"  run approved={run_result.get('approved')} findings={len(run_result.get('findings') or [])}")
        for finding in run_result.get("findings") or []:
            print(f"    finding: {finding.get('title')}")

    print("\nDone. Open the Reasoning screen's \"From Graph\" tab for tenant "
          f"{args.tenant_id!r} to see the {len(scenarios)} seeded task(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
