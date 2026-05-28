# Graph Collapse Off-Trail Edges - task #324

## Change

- Added a Graph Explorer state for collapsing off-trail edges while `Hide unrelated to trail` is active.
- The canvas now keeps trail nodes and clickable one-hop neighbor nodes visible, but only renders:
  - trail edges, and
  - up to 10 top related edges for the currently selected node.
- Extra selected-node related edges are summarized as a non-semantic badge such as `87 edges collapsed`.
- Inspector `Connected edges` remains the complete edge list, so the UI does not lose evidence or provenance.

## Boundaries

- Frontend-only Graph Explorer change.
- No graph data, ontology artifacts, proposed graph review state, formal graph, or canonical ontology writes changed.
- The collapsed badge is a visual grouping cue only; it is not a graph relation.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph.js --format=iife --global-name=AletheiaGraph --log-level=warning`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py server/workbench_server.py`
- `git diff --check`
