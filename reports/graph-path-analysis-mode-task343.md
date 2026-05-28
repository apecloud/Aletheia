# Graph Path Analysis Mode - task #343

## Change

Graph Explorer now treats high-degree node browsing as path-first analysis instead of rendering every adjacent edge on the canvas.

- Added explicit trail state for both nodes and selected edges.
- When `Hide unrelated to trail` is enabled, the canvas renders only:
  - the current trail nodes,
  - explicitly selected trail edges,
  - at most 20 ranked candidate edges from the selected node,
  - one aggregate badge for the selected node's remaining connected edges.
- Connected edges remain available in the right inspector, with search and sort controls.
- Clicking an edge in the right inspector adds that edge and its opposite endpoint to the trail, highlights the selected edge, and keeps path focus active.
- `Show nearby candidates`, `Back`, `Clear trail`, and `Show all graph nodes` support the path workflow.

## Boundary

This is a Graph Explorer rendering and interaction change only. It does not change graph data, SchemaGraphModelingAgent projection, reasoning, ontology review, proposed graph review, or formal graph write behavior.

## Smoke Evidence

- API graph context for `maritime-risk / Country:CHN / view=all / limit=300` returned `projection_source=SchemaGraphModelingAgent`, `222` nodes, `900` edges.
- The same response contains high-degree chokepoints such as `MaritimeChokepoint:Bab el-Mandeb Strait` with `198` connected edges.
- The served `graph.jsx` on port `8772` contains the new controls and text:
  - `Canvas limit: trail plus top 20 candidate edges`
  - `Search edges, endpoints, source rows`
  - `list-only`
  - `candidateEdgeKeys`
  - `selectedEdgeKey`

## Validation

- `npx --yes esbuild web/app/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task343.js --format=iife --global-name=AletheiaGraph --log-level=warning`
- `node --check web/app/api.js`
- `.venv/bin/python -m py_compile server/workbench_server.py reasoning_engine.py review_workbench.py`
- `.venv/bin/python -m unittest tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py`
- `git diff --check`
