#!/usr/bin/env python3
"""Demo: causal-chain / timeline reasoning over the KubeBlocks GitHub pilot
tenant, using Aletheia's existing ReasoningEngine.analyze() and
GraphInstanceRepository -- no new reasoning code (per plan Phase 3: this
reuses existing capability as-is).

KNOWN PRE-EXISTING BUG found while building this demo: ReasoningEngine.
analyze(..., additional_center_nodes=[...]) -> _analyze_multi_center() ->
_find_path_between_centers() compares the full "Type:id" center-node string
against bare vertex ids from GraphInstanceRepository.neighborhood()'s
nodes/edges (which are never "Type:"-prefixed) -- so the equality check
`other == target_center_node` can never match, and multi-center path-finding
always silently falls through to the LLM-fallback branch, even when a real
graph path exists (verified directly: Issue:kb_issue_10816's own
neighborhood(depth=2) DOES contain both the closing PR and merged commit as
plain node ids). This affects every graph-native tenant, not just this one --
flagged to the user as an out-of-plan-scope finding, not fixed here.

Workaround used below: build the causal chain from sequential single-center
analyze() calls (which work correctly and give real graph-grounded
explanations, demonstrated per-node) plus a direct edge/property read for
the chain summary and timeline, instead of the broken multi-center path call.

Run: python scripts/kubeblocks_reasoning_demo.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from aletheia.core.tenant_registry import TenantRegistry  # noqa: E402
from aletheia.graph_store.instance_repository import GraphInstanceRepository  # noqa: E402
from aletheia.reasoning.engine import ReasoningEngine  # noqa: E402

TENANT_ID = "kubeblocks-github-v1"

# A real chain found in the pilot data: Issue #10816 was closed by PR #10822,
# which merged commit 370847... touching 2 files.
EXAMPLE_ISSUE = "kb_issue_10816"
EXAMPLE_PR = "kb_pr_10822"
EXAMPLE_COMMIT = "kb_commit_370847046cef57cbfb2ebff162b0ba26383e765d"


def build_reasoning_engine(tenant_config):
    repo = GraphInstanceRepository(
        space=tenant_config.graph_database,
        nebula_ip=tenant_config.graph_ip,
        nebula_port=tenant_config.graph_port,
        nebula_user=tenant_config.graph_user,
        nebula_password=tenant_config.graph_password,
        relation_catalog_db_url=tenant_config.metadata_db_url,
        relation_catalog_scope=tenant_config.relation_catalog_scope,
    )
    return ReasoningEngine(repo), repo


TIMESTAMP_FIELDS = ("created_at", "merged_at", "closed_at", "authored_at")


def causal_chain_and_timeline(repo, node_ids_in_order: list[tuple[str, str]]) -> list[dict]:
    """node_ids_in_order: [(vid, causal_role), ...]. Fetches each vertex's
    real properties directly (GraphInstanceRepository._fetch_vertex) and
    returns a timestamp-sorted event list -- the causal ORDER comes from the
    real graph edges (CLOSES/MERGES) the caller already walked to build this
    list, timestamps just confirm/display chronology."""
    events = []
    for vid, role in node_ids_in_order:
        vertex = repo._fetch_vertex(vid)
        if not vertex:
            continue
        props = vertex.get("properties") or {}
        for field in TIMESTAMP_FIELDS:
            if props.get(field):
                events.append({
                    "role": role, "type": (vertex.get("types") or [None])[0], "id": vid,
                    "field": field, "timestamp": props[field],
                })
    return sorted(events, key=lambda e: e["timestamp"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", default=EXAMPLE_ISSUE)
    parser.add_argument("--pr", default=EXAMPLE_PR)
    parser.add_argument("--commit", default=EXAMPLE_COMMIT)
    args = parser.parse_args()

    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "0")  # deterministic, no live LLM calls needed for this demo

    tenant_config = TenantRegistry.load().get(TENANT_ID)
    engine, repo = build_reasoning_engine(tenant_config)

    print("=== Per-node graph-grounded reasoning (ReasoningEngine.analyze, single center) ===")
    for label, center_node in [("Issue", f"Issue:{args.issue}"), ("PullRequest", f"PullRequest:{args.pr}"), ("Commit", f"Commit:{args.commit}")]:
        result = engine.analyze(tenant_config, center_node=center_node, depth=1, limit=50)
        print(f"\n--- {label}: {center_node} ---")
        if result:
            print(result["profile_summary"])
            for fact in result["key_facts"]:
                print(f"  {fact['label']}: {fact['value']}")
        else:
            print("  (not found)")

    print("\n=== Causal chain (Issue <-CLOSES- PR -MERGES-> Commit, from real graph edges) ===")
    print(f"  Issue:{args.issue}  <--CLOSES--  PullRequest:{args.pr}  --MERGES-->  Commit:{args.commit}")

    print("\n=== Timeline (sorted by real timestamps across the chain) ===")
    for event in causal_chain_and_timeline(repo, [(args.issue, "issue"), (args.pr, "pull_request"), (args.commit, "commit")]):
        print(f"  {event['timestamp']}  [{event['field']}]  {event['type']}:{event['id']}")

    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
