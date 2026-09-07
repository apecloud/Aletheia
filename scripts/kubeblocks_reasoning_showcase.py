#!/usr/bin/env python3
"""Consolidated showcase: all three reasoning modes explored on top of the
KubeBlocks GitHub pilot tenant --
  1. Causal-chain / timeline reasoning (existing ReasoningEngine capability)
  2. Datalog transitive-impact reasoning (new: aletheia.reasoning.datalog_reasoner)
  3. Graph community detection + centrality ranking (new: InstanceRepository.
     graph_leiden_communities / graph_centrality_ranking)

Run: python scripts/kubeblocks_reasoning_showcase.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "scripts"))

from aletheia.core.tenant_registry import TenantRegistry  # noqa: E402
from aletheia.graph_store.instance_repository import GraphInstanceRepository  # noqa: E402
from aletheia.interfaces.api.repositories.instance import InstanceRepository  # noqa: E402
from aletheia.reasoning.datalog_reasoner import DatalogReasoner  # noqa: E402
from aletheia.reasoning.engine import ReasoningEngine  # noqa: E402
from aletheia.reasoning.graph_facts import load_facts  # noqa: E402

TENANT_ID = "kubeblocks-github-v1"
EXAMPLE_ISSUE = "kb_issue_10816"
EXAMPLE_PR = "kb_pr_10822"
EXAMPLE_COMMIT = "kb_commit_370847046cef57cbfb2ebff162b0ba26383e765d"


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main() -> int:
    os.environ.setdefault("ALETHEIA_LLM_PLANNER_ENABLED", "0")
    registry = TenantRegistry.load()
    tenant = registry.get(TENANT_ID)

    low_level_repo = GraphInstanceRepository(
        space=tenant.graph_database, nebula_ip=tenant.graph_ip, nebula_port=tenant.graph_port,
        nebula_user=tenant.graph_user, nebula_password=tenant.graph_password,
        relation_catalog_db_url=tenant.metadata_db_url, relation_catalog_scope=tenant.relation_catalog_scope,
    )
    engine = ReasoningEngine(low_level_repo)

    # 1. Causal chain / timeline -----------------------------------------
    section("1. Causal chain: Issue -> closing PR -> merged commit (ReasoningEngine.analyze)")
    for label, center in [("Issue", f"Issue:{EXAMPLE_ISSUE}"), ("PullRequest", f"PullRequest:{EXAMPLE_PR}"), ("Commit", f"Commit:{EXAMPLE_COMMIT}")]:
        result = engine.analyze(tenant, center_node=center, depth=1, limit=50)
        print(f"\n[{label}] {result['profile_summary'] if result else '(not found)'}")
        for fact in (result or {}).get("key_facts", []):
            print(f"    {fact['label']}: {fact['value']}")

    events = []
    for vid, role in [(EXAMPLE_ISSUE, "issue"), (EXAMPLE_PR, "pull_request"), (EXAMPLE_COMMIT, "commit")]:
        vertex = low_level_repo._fetch_vertex(vid)
        for field in ("created_at", "merged_at", "closed_at", "authored_at"):
            ts = (vertex.get("properties") or {}).get(field) if vertex else None
            if ts:
                events.append((ts, field, role, vid))
    print("\nTimeline:")
    for ts, field, role, vid in sorted(events):
        print(f"    {ts}  [{field}]  {role}:{vid}")

    # 2. Datalog transitive impact ----------------------------------------
    section("2. Datalog: transitive indirect-impact reasoning (shared-touched-file propagation)")
    entity_config = low_level_repo.reasoning_entity_config(TENANT_ID)
    graph = low_level_repo.full_graph(entity_config, node_limit=6000, edge_limit=10000)
    print(f"Loaded full graph snapshot: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges")

    reasoner = DatalogReasoner()
    fact_count = load_facts(reasoner, graph["nodes"], graph["edges"])
    reasoner.add_rule("shares_module(?A, ?B) :- touches(?A, ?M), touches(?B, ?M).")
    reasoner.add_rule("affects(?A, ?B) :- shares_module(?A, ?B).")
    reasoner.add_rule("affects(?A, ?B) :- shares_module(?A, ?C), affects(?C, ?B).")
    print(f"Loaded {fact_count} base facts; deriving fixpoint (this takes a few minutes at this scale) ...")
    derived = reasoner.derive_all()
    affects_count = sum(1 for f in derived if f.startswith("affects("))
    print(f"Derived {len(derived)} total facts ({affects_count} 'affects' pairs)")

    impacted = reasoner.query(f"affects({EXAMPLE_COMMIT}, ?B)")
    print(f"\nCommits transitively affected (via shared touched files) by the commit that fixed issue #10816: {len(impacted)}")
    for row in impacted[:10]:
        print(f"    {row['B']}")

    # 3. Community detection + centrality ----------------------------------
    section("3. Graph community detection (Leiden) + centrality ranking")
    server_repo = InstanceRepository(registry, ensure_schema=False)
    communities = server_repo.graph_leiden_communities(tenant, limit=300, resolution=1.0)
    print(f"Leiden partition over a {communities['scope']['node_count']}-node sample: "
          f"{communities['community_count']} communities, modularity={communities['modularity']:.3f}")

    for method in ("betweenness", "pagerank"):
        ranking = server_repo.graph_centrality_ranking(tenant, limit=300, method=method, top_n=5)
        print(f"\nTop 5 by {method}:")
        for entry in ranking["ranking"]:
            print(f"    {entry['score']:.4f}  {entry['id']}")

    low_level_repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
