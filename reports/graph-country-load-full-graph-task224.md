# Graph Country Load Full Graph Fix - Task 224

## Issue

On the Graph page, selecting `Country` in the left center selector and clicking `Load full graph` could appear to do nothing.

Root causes:

- `view=all` sampled the tenant-wide graph by type and could omit the selected center country, especially countries outside the first sampled rows such as `CHN`.
- Changing the center type did not immediately clear the old center id, so the UI could temporarily combine `Country` with a stale chokepoint id.
- Deep links could be mounted before the tenant finished loading, allowing the center selector to fall back to the first tenant type.

## Changes

- `GET /api/graph/context?view=all` now accepts the selected `type/id` and forces that center node into the full graph if it exists.
- The Graph UI clears stale center ids when the center type changes.
- `Load full graph` now waits for a valid center node, refreshes the full graph, and focuses the selected center after load.
- Direct Graph links preserve the requested center after tenant metadata loads.
- The UI shows a small status message for loading/focus failures instead of silently doing nothing.

## Verification

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/graph-task224.js --format=iife --global-name=GraphTask224 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`

## API Smoke

`GET /api/graph/context?tenant=maritime-risk&type=Country&id=CHN&view=all&limit=80`

- `approved=true`
- `scope.view=all`
- `center.id=Country:CHN`
- includes `Country:CHN`
- returned 81 nodes and 94 edges

## Browser Smoke

URL: `<http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&type=Country&id=CHN>`

The rendered app body shows:

- `Center = Country:CHN`
- `Load full graph`
- `Focus center in full graph`
- `All approved tenant graph nodes are visible`
- no `Empty graph`

Evidence file: `/tmp/task224-country-dom.html`.

## Boundary

This is a Graph page interaction fix only. It does not change proposed graph review, automatic agent execution, ontology approval, or formal graph writes.
