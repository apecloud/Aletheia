#!/usr/bin/env python3
"""Curate per-entity-type "reasoning focus" for the KubeBlocks GitHub pilot
tenant -- what a reviewer actually cares about when reasoning about an
Issue (urgency, response time) versus a PullRequest (review latency,
reviewer responsiveness, size/blast radius) versus other types.

This is the data-side half of steering LLM-synthesized insights
(aletheia/llms/planner.py's LLMPlanner.synthesize_relation_insight) toward
what matters for a given entity type: the insight-synthesis call reads
each type's approved reasoning_focus (via reasoning_entity_config, see
traversal.py's _reasoning_focus_dimensions) and, when present, is
explicitly told to prioritize those dimensions over generic observations.
No business vocabulary ("urgency", "review latency", ...) is hardcoded in
that shared code; it all comes from here.

propose_node_type upserts by (project_id, canonical_key) -- re-running this
against an existing approved node type just updates its payload in place,
no re-review needed (see aletheia/ontology/store.py's upsert_artifact).
Existing `properties` (registered by import_kubeblocks_github_dataset.py)
are passed through unchanged here so this script only adds reasoning_focus,
never drops the schema those properties define.

Run: python scripts/update_kubeblocks_github_reasoning_focus.py
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
from aletheia.ontology.registry import propose_node_type  # noqa: E402
from aletheia.ontology.store import ensure_artifact_schema  # noqa: E402

DEFAULT_TENANT_ID = "kubeblocks-github-v1"
EVIDENCE_REF = "kubeblocks_github_reasoning_focus"

# properties copied verbatim from import_kubeblocks_github_dataset.py's
# NODE_TYPES so re-running this doesn't narrow any type's registered schema
# -- this script only adds reasoning_focus.
NODE_PROPERTIES: dict[str, list[str]] = {
    "Repository": ["name"],
    "Issue": ["number", "title", "state", "created_at", "closed_at"],
    "PullRequest": ["number", "title", "state", "created_at", "closed_at", "merged_at", "base_ref"],
    "Commit": ["sha", "message", "authored_at"],
    "User": ["login"],
    "Label": ["name"],
    "File": ["file_path"],
}

# Multiple dimensions per type are supported -- each is independently
# descriptive metadata (name/description/signals), read generically by
# _reasoning_focus_dimensions/synthesize_relation_insight. Designed from
# common GitHub-project review concerns (issue triage/response, PR review
# governance, commit/file blast radius, contributor concentration, ...),
# not from any one specific incident in this tenant's data.
REASONING_FOCUS: dict[str, list[dict]] = {
    "Issue": [
        {
            "name": "urgency_and_response_time",
            "description": "How quickly was this issue triaged and closed, and does its label set suggest severity? A long-open issue or one carrying a severity/priority label deserves more attention than a routine one.",
            "signals": ["state", "created_at", "closed_at", "LABELED_WITH", "ASSIGNED_TO"],
        },
        {
            "name": "resolution_traceability",
            "description": "Was this issue closed by an actual code fix (a PullRequest linked to it via CLOSES), or closed with no linked fix at all (possibly a duplicate, unreproducible, or bot-cleaned)? The former is a real resolution; the latter may mean it was shelved or closed in error.",
            "signals": ["state", "closed_at", "CLOSES"],
        },
        {
            "name": "triage_and_ownership_gap",
            "description": "Was this issue ever assigned to a specific person? The gap between creation and assignment reflects triage efficiency; an issue that sits unassigned for a long time risks being forgotten.",
            "signals": ["ASSIGNED_TO", "created_at", "state"],
        },
    ],
    "PullRequest": [
        {
            "name": "review_latency_and_responsiveness",
            "description": "How long did this PR sit before merge, how many reviewers were requested, and did review actually happen before merge (versus being merged with no engagement)?",
            "signals": ["created_at", "merged_at", "closed_at", "REVIEW_REQUESTED", "AUTHORED"],
        },
        {
            "name": "size_and_blast_radius",
            "description": "How many commits/files does this PR touch, and are those files shared with a lot of other commits/PRs (a wide blast radius means higher regression risk, even for a small diff)?",
            "signals": ["MERGES", "TOUCHES"],
        },
        {
            "name": "merge_without_review_risk",
            "description": "Was this PR merged with no REVIEW_REQUESTED record at all? Merging without any requested review is a governance risk signal worth calling out explicitly.",
            "signals": ["REVIEW_REQUESTED", "merged_at", "state"],
        },
        {
            "name": "traceability_to_issue",
            "description": "Does this PR link to a specific issue via CLOSES? A linked PR has a traceable origin for its change; an unlinked ('orphan') PR may be missing context on why the change was made.",
            "signals": ["CLOSES"],
        },
    ],
    "Commit": [
        {
            "name": "change_hotspot_and_shared_impact",
            "description": "Are the files this commit touches shared with a large number of other commits? A change to a highly-shared file carries more regression risk even if the diff itself is small.",
            "signals": ["TOUCHES", "PARENT_COMMIT"],
        },
        {
            "name": "merge_vs_direct_commit",
            "description": "Did this commit come in through a reviewed PullRequest (a MERGES edge points to it), or was it committed directly with no such record? The former went through the normal collaboration process; the latter may have bypassed it.",
            "signals": ["MERGES", "PARENT_COMMIT"],
        },
        {
            "name": "authorship_and_review_context",
            "description": "Who authored this commit, and did it arrive via a PR that had review activity, or with no linked review context at all?",
            "signals": ["AUTHORED", "MERGES"],
        },
    ],
    "User": [
        {
            "name": "contribution_role_pattern",
            "description": "Is this user's activity mostly authoring commits/PRs (an implementer), mostly reporting issues (a reporter), or frequently requested as a reviewer? Different roles should be read through different lenses.",
            "signals": ["AUTHORED", "REVIEW_REQUESTED", "ASSIGNED_TO"],
        },
        {
            "name": "engagement_breadth",
            "description": "Is this user's activity concentrated on a handful of issues/PRs/repositories (focused), or spread across many different objects (broad participation)?",
            "signals": ["AUTHORED", "BELONGS_TO"],
        },
    ],
    "Label": [
        {
            "name": "severity_signal_strength",
            "description": "Does the label's own name imply severity or priority (e.g. containing 'bug', 'critical', 'P0'), or is it a purely organizational label (e.g. 'good-first-issue', 'stale')? This determines how much weight an issue/PR carrying this label should get.",
            "signals": ["name", "LABELED_WITH"],
        },
        {
            "name": "label_volume_and_trend",
            "description": "How many issues/PRs currently carry this label, and what fraction are still open? A sudden rise in open issues under one label may indicate an accumulating systemic problem.",
            "signals": ["LABELED_WITH"],
        },
    ],
    "Repository": [
        {
            "name": "issue_and_pr_health_ratio",
            "description": "What is this repository's ratio of open to closed issues/PRs, and what's the average time-to-close? These reflect the project's overall operational health, not any single issue/PR.",
            "signals": ["BELONGS_TO", "state", "created_at", "closed_at"],
        },
        {
            "name": "contributor_concentration",
            "description": "Is this repository's contribution concentrated among a small number of core maintainers, or spread across a broad contributor base? High concentration is a single-point-of-failure risk (losing a key person has outsized impact).",
            "signals": ["AUTHORED", "BELONGS_TO"],
        },
    ],
    "File": [
        {
            "name": "churn_and_ownership_hotspot",
            "description": "How many distinct commits and authors have modified this file? A file with high change frequency and many different authors is typically a coordination hotspot prone to regressions.",
            "signals": ["TOUCHES", "AUTHORED"],
        },
        {
            "name": "change_coupling_risk",
            "description": "Does this file tend to be modified together with other specific files in the same commits? This kind of implicit change coupling often reveals architectural dependencies beyond the explicit relations, worth noting when assessing change impact.",
            "signals": ["TOUCHES"],
        },
    ],
}


def update_reasoning_focus(session, tenant_id: str) -> None:
    for name, focus in REASONING_FOCUS.items():
        properties = NODE_PROPERTIES.get(name, [])
        propose_node_type(
            session, tenant_id=tenant_id, name=name,
            description=f"KubeBlocks GitHub pilot: {name} node type.",
            properties=[{"name": f, "data_type": "string"} for f in properties],
            reasoning_focus=focus,
            confidence=1.0, evidence=[EVIDENCE_REF], status="approved",
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

    print(f"Updating reasoning_focus for {len(REASONING_FOCUS)} node type(s) on tenant {args.tenant_id!r} ...")
    update_reasoning_focus(session, args.tenant_id)
    for name, focus in REASONING_FOCUS.items():
        print(f"  {name}:")
        for dim in focus:
            print(f"    - {dim['name']}: {dim['description']}")

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
