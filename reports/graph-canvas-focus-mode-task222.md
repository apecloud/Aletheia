# Graph Canvas Focus Mode - Task 222

## Goal

Make the Graph page show the tenant-wide approved graph by default. Selecting a node should focus/highlight that local context while dimming unrelated nodes and edges, and clearing the selection should return to the full-graph contrast view.

## Changes

- Added `view=all` support to `/api/graph/context`.
- Added `InstanceRepository.full_graph()` to sample approved tenant objects and approved graph edges without requiring a center node.
- Added maritime-risk graph edges for hazard, risk indicator, dependency, country, chokepoint, risk result, finding, and mitigation-action paths.
- Changed Graph page default behavior to render all approved tenant graph nodes with no selected node.
- Node click now activates focus contrast:
  - selected node and adjacent nodes stay prominent;
  - unrelated nodes and edges are dimmed;
  - Clear focus returns to the full graph.
- Tenant changes clear the previous selected node so one tenant does not leak focus state into another.
- The center selector remains available as a convenience action: `Focus center in full graph`.
- Updated the node inspector source row to use real `source_table/source_pk` from the selected node instead of hard-coded Northwind text.

## Verification

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/graph-task222.js --format=iife --global-name=GraphTask222 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`

## API Smoke

- `GET /api/graph/context?tenant=maritime-risk&view=all&limit=80`
  - `approved=true`
  - `scope.view=all`
  - `nodes=80`
  - `edges=94`
- `GET /api/graph/context?tenant=default&view=all&limit=80`
  - `approved=true`
  - `scope.view=all`
  - `nodes=68`
  - `edges=36`

## Browser Smoke

- URL: `<http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk>`
- App body rendered the full-graph empty-selection state:
  - `All approved tenant graph nodes are visible`
  - `FOCUS`
  - `ALL`
  - maritime node types including `Chokepoint`, `Country`, and `Hazard`
  - `Focus center in full graph`
- App body did not render `Empty graph`.

Evidence file: `/tmp/task222-graph-all-dom.html`.

## Boundary

This changes graph viewing and contrast behavior only. It does not approve proposed graph elements, write formal graph data, approve ontology artifacts, or change automatic agent execution behavior.
