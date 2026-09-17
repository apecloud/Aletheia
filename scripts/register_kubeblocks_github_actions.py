#!/usr/bin/env python3
"""Register a curated set of operational "action" ontology artifacts for the
KubeBlocks GitHub pilot tenant (scripts/import_kubeblocks_github_dataset.py's
node/edge types), one per hand-designed reasoning scenario (see
scripts/seed_kubeblocks_github_reasoning_scenarios.py for the matching
reasoning tasks).

Kept as its own script rather than folded into import_kubeblocks_github_
dataset.py's register_ontology(): these actions are a curated, hand-designed
ontology addition, not mechanical GitHub-API data materialization -- a fresh
data pull/re-import shouldn't need to know about them, and vice versa.

Uses aletheia.ontology.registry.propose_action, added alongside the existing
propose_node_type/propose_edge_type graph-native registrars. status=
"approved" directly, matching the same "auto_approve" governance mode
import_kubeblocks_github_dataset.py already uses for this tenant's node/edge
types (no human-review round trip for a scripted, reviewed-by-a-human-before-
running import).

Run: python scripts/register_kubeblocks_github_actions.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from aletheia.core.tenant_registry import default_metadata_db_url  # noqa: E402
from aletheia.ontology.registry import propose_action  # noqa: E402
from aletheia.ontology.store import ensure_artifact_schema  # noqa: E402

DEFAULT_TENANT_ID = "kubeblocks-github-v1"
EVIDENCE_REF = "kubeblocks_github_reasoning_scenarios"

# One action per scenario in scripts/seed_kubeblocks_github_reasoning_scenarios.py.
ACTIONS: list[dict] = [
    {
        "name": "EscalateSlowPullRequestReview",
        "description": "Retrospective review-latency audit: a pull request had multiple reviewers requested and took a long time to close -- surface the pattern so future PRs with similar reviewer load get escalated earlier.",
        "applies_to": ["PullRequest", "User"],
        "trigger_event": "PullRequest has >=2 REVIEW_REQUESTED edges and the elapsed time from created_at to closed_at/merged_at exceeds a review-latency threshold.",
        "input_parameters": ["pr_number", "requested_reviewer_logins", "review_latency_hours"],
        "expected_effects": [
            "ping additional reviewer",
            "escalate to maintainer for faster turnaround on future PRs with similar reviewer load",
        ],
        "guardrails": [
            "advisory/retrospective only -- never reopens or modifies an already-closed PR",
            "requires maintainer confirmation before escalating a still-open PR",
        ],
    },
    {
        "name": "FlagRegressionRiskForFile",
        "description": "File-level blast-radius risk: a new PR touches a file that Datalog-derived transitive-impact analysis shows is heavily shared across other commits/PRs -- flag it for extra review attention.",
        "applies_to": ["File", "PullRequest", "Commit"],
        "trigger_event": "A PullRequest's commits TOUCH a File that Datalog-derived transitive-impact analysis shows is connected to N other commits/PRs via shared file touches.",
        "input_parameters": ["file_path", "candidate_pr_number", "transitive_impact_count"],
        "expected_effects": [
            "attach a regression-risk label to the PR",
            "request review from users who previously authored commits touching this file",
        ],
        "guardrails": [
            "advisory only -- never blocks CI or merge automatically",
            "risk flag must cite the specific transitively-connected commits as evidence",
        ],
    },
    {
        "name": "SuggestAssigneeForIssue",
        "description": "Contributor routing: suggest an assignee for a newly-labeled issue based on who has authored commits/PRs under the same label.",
        "applies_to": ["Issue", "User", "Label"],
        "trigger_event": "An Issue has state=open, has >=1 Label, and has no ASSIGNED_TO edge after a review window.",
        "input_parameters": ["issue_number", "label_names"],
        "expected_effects": [
            "propose one or more candidate assignees ranked by prior authored PRs/commits under the same label",
            "notify candidate for confirmation",
        ],
        "guardrails": [
            "suggestion only -- a human must confirm the assignment",
            "do not suggest a user already overloaded with open assigned issues",
        ],
    },
    {
        "name": "RequestIssueLinkForOrphanedPullRequest",
        "description": "Traceability gap: a merged pull request has no CLOSES edge to any issue -- request a linked issue or documented rationale.",
        "applies_to": ["PullRequest", "Issue"],
        "trigger_event": "A PullRequest has merged_at set but zero CLOSES edges to any Issue.",
        "input_parameters": ["pr_number"],
        "expected_effects": [
            "comment on the PR requesting a linked issue or rationale",
            "flag for maintainer traceability review",
        ],
        "guardrails": [
            "never reverts or blocks already-merged work",
            "advisory/documentation-focused only",
        ],
    },
    {
        "name": "FlagMergeWithoutReviewGovernanceRisk",
        "description": "Governance gap: a pull request was merged with zero REVIEW_REQUESTED edges -- no review was ever formally requested before merge. Surface the pattern for retroactive audit and to decide whether review-before-merge should be required for the paths this PR touched.",
        "applies_to": ["PullRequest"],
        "trigger_event": "A PullRequest has merged_at set and zero REVIEW_REQUESTED edges.",
        "input_parameters": ["pr_number", "touched_file_paths"],
        "expected_effects": [
            "flag the PR for retroactive governance review",
            "recommend requiring review-before-merge for future changes to the same touched paths",
        ],
        "guardrails": [
            "advisory/retrospective only -- never reverts or blocks an already-merged PR",
            "does not assert the change was wrong, only that it bypassed review",
        ],
    },
    {
        "name": "FlagUndocumentedIssueClosure",
        "description": "Traceability gap, issue side: an issue was closed with no CLOSES edge from any pull request -- there is no recorded code fix explaining the resolution. Complements RequestIssueLinkForOrphanedPullRequest (the PR-side version of the same traceability concern).",
        "applies_to": ["Issue"],
        "trigger_event": "An Issue has state=closed and zero incoming CLOSES edges from any PullRequest.",
        "input_parameters": ["issue_number"],
        "expected_effects": [
            "request a closing comment explaining the resolution (fixed elsewhere, duplicate, won't-fix, stale)",
            "flag for maintainer traceability audit",
        ],
        "guardrails": [
            "advisory only -- never reopens the issue",
            "does not assume the closure was wrong, only that its rationale is undocumented",
        ],
    },
    {
        "name": "FlagFileOwnershipHotspot",
        "description": "Change-coupling risk: a file is touched by an unusually high number of distinct commits/authors -- a coordination hotspot where regressions are more likely and ownership is diffuse.",
        "applies_to": ["File", "Commit", "User"],
        "trigger_event": "A File's TOUCHES fan-in (distinct commits touching it) exceeds a churn threshold relative to other files in the repository.",
        "input_parameters": ["file_path", "touching_commit_count", "distinct_author_count"],
        "expected_effects": [
            "suggest a dedicated owner/reviewer for future changes to this file",
            "recommend stronger test coverage for this file",
        ],
        "guardrails": [
            "advisory only -- never blocks a PR automatically",
            "ownership suggestion must cite the actual prior-touching commits/authors as evidence",
        ],
    },
    {
        "name": "TriageSeverityLabelBacklog",
        "description": "Backlog risk: a severity-implying label (e.g. a bug/priority label, as opposed to a purely organizational one) has a large or growing count of still-open issues/PRs -- may indicate an accumulating systemic problem needing triage attention.",
        "applies_to": ["Label", "Issue", "PullRequest"],
        "trigger_event": "A Label whose name implies severity/priority has a high count of LABELED_WITH edges to still-open issues/PRs.",
        "input_parameters": ["label_name", "open_count", "total_count"],
        "expected_effects": [
            "surface the open backlog under this label to maintainers for triage prioritization",
        ],
        "guardrails": [
            "advisory only -- does not auto-change label assignments or issue state",
        ],
    },
    {
        "name": "FlagReviewerLoadConcentration",
        "description": "Bus-factor risk: one contributor's REVIEW_REQUESTED count is disproportionately higher than every other contributor's -- review capacity is concentrated in a single person.",
        "applies_to": ["User", "PullRequest"],
        "trigger_event": "A User's REVIEW_REQUESTED edge count is far above the next-highest contributor's, across the tenant's pull requests.",
        "input_parameters": ["reviewer_login", "review_requested_count", "next_highest_count"],
        "expected_effects": [
            "suggest broadening the reviewer pool for future pull requests",
            "flag reviewer-concentration risk for maintainer awareness",
        ],
        "guardrails": [
            "advisory only -- never reassigns existing review requests",
        ],
    },
]


def register_actions(session, tenant_id: str) -> None:
    for action in ACTIONS:
        propose_action(
            session,
            tenant_id=tenant_id,
            name=action["name"],
            description=action["description"],
            applies_to=action["applies_to"],
            trigger_event=action["trigger_event"],
            input_parameters=action["input_parameters"],
            expected_effects=action["expected_effects"],
            guardrails=action["guardrails"],
            confidence=1.0,
            evidence=[EVIDENCE_REF],
            status="approved",
        )
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", default=DEFAULT_TENANT_ID)
    args = parser.parse_args()

    metadata_db_url = default_metadata_db_url()
    engine = create_engine(metadata_db_url)
    ensure_artifact_schema(engine)
    session = sessionmaker(bind=engine)()

    print(f"Registering {len(ACTIONS)} actions for tenant {args.tenant_id!r} ...")
    register_actions(session, args.tenant_id)
    for action in ACTIONS:
        print(f"  {action['name']}: applies_to={action['applies_to']}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
