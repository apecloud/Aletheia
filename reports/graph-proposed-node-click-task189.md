# Graph Proposed Node Click Fix

Task: #189
Date: 2026-05-24

## Issue

In the Graph page `Proposed graph` scope, the `nodes / edges / findings` chips looked clickable but were static labels. Clicking `nodes` produced no visible response even though individual proposal rows could open review detail.

## Change

- Converted the proposal kind chips into real filters: `all / nodes / edges / findings`.
- Clicking a kind filter now updates the list immediately.
- If the current selected proposal is not in the new filter, the first matching proposal is selected automatically and its review detail opens.
- Existing node / edge / finding row review behavior remains unchanged: selected proposals show provenance, evidence refs, confidence, path, write boundary, and review actions.

## Verification

- `py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `tests/test_iterative_graph_enrichment.py`
- `tests/test_ontology_eval.py`
- API smoke for `maritime-risk` proposed graph elements: 11 elements, 6 nodes, 4 edges, 1 finding, first node `Bab el-Mandeb Strait`.
- Static UI smoke verified `kindFilter`, `selectKind("node")`, automatic first-item selection, and filtered list rendering exist in `web/review_workbench/graph.jsx`.
- `git diff --check`

## Boundary

This is a UI interaction fix. It does not change canonical ontology, formal graph writes, or proposed graph review write boundaries.
