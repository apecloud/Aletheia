# Proposed Edge Fact Dedup Validation - task #396

## Result

PASS after one validation hardening fix.

The user-reported edge:

`proposed-graph:maritime-risk:edge:111ad78ffd933b10`

no longer appears in the default pending/current proposed list. It remains visible with `status=all`, so history/audit was not deleted.

## Validation scope

Checked the three required cases:

1. Existing approved/pending edge fact should not be shown again in pending proposed edges.
2. Historical duplicate edge facts should remain available in `status=all` for audit.
3. Duplicate endpoint nodes do not imply duplicate edge facts; if the edge fact is new, only the edge is proposed and endpoint dedup evidence remains visible.

## API evidence

Current service: `http://127.0.0.1:8772`, restarted after validation hardening.

Pending/current API:

```text
GET /api/graph/proposed-elements?tenant=maritime-risk&limit=500
total_count: 21
element_type_counts: {"finding": 21}
visible_edges: 0
target edge 111ad78ffd933b10: 0
duplicate visible edge facts: 0
```

Audit/history API:

```text
GET /api/graph/proposed-elements?tenant=maritime-risk&limit=2000&status=all
total_count: 179
element_type_counts: {"finding": 26, "edge": 82, "node": 71}
target edge 111ad78ffd933b10: 1
status: draft
name: KOR has country dependency Hormuz Strait
```

`status=all` still contains duplicate historical KOR/JPN Hormuz edge records, which is expected for audit/history. The pending/current filter hides them.

## Hardening fix

During validation, I found a remaining generic edge identity risk: when an edge candidate had no `schema_edge_key`, metric, or fact hint, `_edge_identity_payload()` still used `source_url` as the last identity fallback. That would allow the same source/relation/target fact to become different candidates when only the source URL changed.

I removed that fallback and added a regression in `tests/test_iterative_graph_enrichment.py`:

- same tenant
- same source endpoint
- same relation
- same target endpoint
- different `source_url`
- no explicit metric/schema fact identity

now produces the same `identity_key` and `candidate_id`.

Source URL still remains in source/evidence fingerprints and audit, not fact identity.

## Commands

Passed:

```bash
.venv/bin/python -m unittest tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_edge_candidate_id_changes_only_when_graph_edge_identity_changes tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_duplicate_endpoint_nodes_are_edge_context_not_node_proposals tests.test_iterative_graph_enrichment.IterativeGraphEnrichmentTest.test_rerun_same_frontier_does_not_duplicate_proposed_nodes_or_edges
.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py
.venv/bin/python -m unittest discover tests
.venv/bin/python -m py_compile agents/iterative_graph_enrichment_agent.py server/workbench_server.py tests/test_iterative_graph_enrichment.py
node --check web/app/api.js
npx --yes esbuild web/app/graph.jsx --bundle --format=iife --global-name=AletheiaGraph --outfile=/tmp/task396-graph.js
git diff --check -- agents/iterative_graph_enrichment_agent.py server/workbench_server.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py
```

## Boundary

No canonical ontology writes were enabled.
No formal graph writes were enabled.
Endpoint dedup evidence remains display/audit context for new edge facts.
