# Graph Country Search - Task 225

## Issue

The Graph page center selector could not select `CHN` after choosing `Country`.

Root cause: the center node dropdown loaded only the first visible candidates. For `Country`, the first page is alphabetic (`ABW`, `AFG`, ...), so `CHN` was not reachable from the selector.

## Changes

- Added a center search/input field under the center selector.
- The candidate dropdown now searches the selected tenant/type using the input text instead of always loading the first fixed page.
- For `Country`, the placeholder explicitly shows `Search or type ISO3, e.g. CHN`.
- Typing a valid id such as `CHN` sets the center id immediately, so `Load full graph` can load and focus that country even when it was not in the first dropdown page.
- Changing center type clears stale center id/search text to avoid mixed scopes.

## Verification

- `GET /api/instances/search?tenant=maritime-risk&type=Country&q=CHN&limit=50&include_draft=1`
  - returns `Country:CHN`
- `GET /api/graph/context?tenant=maritime-risk&type=Country&id=CHN&view=all&limit=80`
  - returns `center.id=Country:CHN`
  - includes `Country:CHN`
  - returns 81 nodes / 94 edges
- Browser smoke:
  - URL: `<http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&type=Country&id=CHN>`
  - rendered `Center = Country:CHN`
  - rendered search placeholder `Search or type ISO3, e.g. CHN`
  - rendered `Load full graph` and `Focus center in full graph`
  - did not render `Empty graph`

## Checks

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/graph-task225.js --format=iife --global-name=GraphTask225 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`

## Boundary

This is a selector/search interaction fix only. It does not change graph writes, proposed graph review, ontology approval, or agent execution behavior.
