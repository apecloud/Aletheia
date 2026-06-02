# Proposed Edge Fact Dedup - task #395

## Issue

The proposed edge `proposed-graph:maritime-risk:edge:111ad78ffd933b10`
(`South Korea (KOR) has country dependency Hormuz Strait`) appeared as a
draft even though the same edge fact had already been proposed or reviewed in
earlier enrichment runs.

## Root Cause

The enrichment identity for edge candidates could use `source_url` as part of
the edge identity when no stronger stable source identity was present. That
made the same graph fact appear new when a later run found it through a
different source URL or evidence ordering.

Endpoint-node dedup was working: KOR and Hormuz Strait were correctly detected
as existing proposals. The edge fact dedup was the broken layer.

## Changes

- Updated edge candidate identity to prefer stable fact identity:
  `fact_node_hint`, schema edge key, endpoint/relation, and metric identity.
- Kept metric identity in the edge identity so the same endpoints/relation with
  genuinely different metrics remain separate.
- Rebuilt stale URL-based edge identity index rows for `maritime-risk`.
- Added pending Proposed Graph filtering for duplicate edge facts:
  - hide draft edge facts that already have an approved edge fact;
  - hide repeated pending draft edge facts within the same pending result set;
  - keep `status=all` history/audit rows visible.

## Validation

- Pending API:
  `/api/graph/proposed-elements?tenant=maritime-risk&limit=200`
  now returns `target_present=false` for
  `proposed-graph:maritime-risk:edge:111ad78ffd933b10`.
- History API:
  `/api/graph/proposed-elements?tenant=maritime-risk&limit=200&status=all`
  still returns the target row for audit/history.
- `maritime-risk` identity index rebuilt with stable identity including
  `edge:maritime-risk:kor:has country dependency:hormuz strait:has_country_dependency:KOR::Hormuz Strait`.
- 8772 restarted and serving the patched backend.

## Tests

- `.venv/bin/python -m unittest -v tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_edge_candidate_id_changes_only_when_graph_edge_identity_changes tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_duplicate_endpoint_nodes_are_edge_context_not_node_proposals tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_vector_dedup_edge_structural_conflict_requires_review_not_duplicate`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py`
- `.venv/bin/python -m py_compile agents/iterative_graph_enrichment_agent.py server/workbench_server.py`
- `node --check web/app/api.js`
- `npx esbuild web/app/graph.jsx --bundle --format=iife --global-name=GraphExplorer --outfile=/tmp/aletheia-graph-task395.js`
- `git diff --check -- agents/iterative_graph_enrichment_agent.py tests/test_iterative_graph_enrichment.py server/workbench_server.py`
