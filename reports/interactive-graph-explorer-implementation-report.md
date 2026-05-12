# Interactive Graph Explorer Implementation Report

## Scope

Implemented canonical tasks #70-#73:

- #70 Graph Explorer API for tenant-scoped graph context, node detail, edge detail, and bounded expand.
- #71 Graph Explorer UI canvas with zoom, pan, select node, select edge, expand, collapse, focus, and fit view controls.
- #72 Graph Inspector & Provenance for node/edge properties, ontology links, source rows, and Instance/Workbench deep links.
- #73 Scoped Reasoning From Graph for creating and running draft-only reasoning tasks from the selected node or edge.

## Entry Points

- Graph Explorer: `http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200`
- Default baseline graph API: `GET /api/graph/context?tenant=default&type=Employee&id=4&depth=1&limit=200`
- Node detail: `GET /api/graph/node/Employee%3A4?tenant=default`
- Edge detail: `GET /api/graph/edge/Employee%3A4-%3EOrder%3A10250?tenant=default`
- Expand: `POST /api/graph/expand?tenant=default`
- Scoped reasoning: `POST /api/reasoning/tasks/from-graph?tenant=default`

## Safety Boundaries

- Graph APIs are tenant-scoped and reuse the existing approved-only Object/Link artifact gate.
- Sandbox tenant does not fallback to default; missing approved artifacts return an empty/blocked graph.
- Depth is clamped to max 2.
- Node and edge limits are clamped to hard max 300.
- Graph browsing and expand are read-only over source/metadata.
- Graph-created reasoning tasks carry `approved_only=true`, selected center node/edge, depth, limits, allowed node types, allowed link keys, evidence paths, and `review_gate=draft_only`.
- Scoped reasoning runs only create draft findings/action proposals and do not approve, ingest, commit, push, or modify canonical ontology/graph artifacts.

## Verification Results

Commands run:

```bash
node --check web/review_workbench/graph_app.js
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/reasoning_app.js
node --check web/review_workbench/settings_app.js
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

API checks:

```bash
curl -sS 'http://127.0.0.1:8767/api/graph/context?tenant=default&type=Employee&id=4&depth=1&limit=200'
```

Result: `approved=true`, 157 nodes, 156 edges, `handled_orders=156`, `returned_orders=156`.

```bash
curl -sS 'http://127.0.0.1:8767/api/graph/context?tenant=default&type=Employee&id=4&depth=99&limit=999'
```

Result: depth clamped to 2, limit clamped to 300, `truncated=true`.

```bash
curl -sS -i 'http://127.0.0.1:8767/api/graph/context?tenant=northwind-sandbox&type=Employee&id=4&depth=1&limit=200'
```

Result: `approved=false`, missing `object:order` and `link:employee:1:n:order`, 0 nodes, 0 edges.

```bash
curl -sS -X POST 'http://127.0.0.1:8767/api/reasoning/tasks/from-graph?tenant=default' ...
curl -sS -X POST 'http://127.0.0.1:8767/api/reasoning/tasks/reasoning-graph-scope-default-employee-4-d1/run?tenant=default'
```

Result: scoped task created with `approved_only=true` and `review_gate=draft_only`; run completed with one draft finding and `draft_only=true`.

```bash
curl -sS -i -X POST 'http://127.0.0.1:8767/api/reasoning/tasks/from-graph?tenant=northwind-sandbox' ...
```

Result: HTTP 400, `center_node is outside the approved graph scope`.

Canonical safety check:

```bash
curl -sS 'http://127.0.0.1:8767/api/artifacts/link%3Aemployee%3A1%3An%3Aorder?tenant=default'
```

Result: `link:employee:1:n:order` remains `approved` version 6.

Browser smoke:

```bash
playwright install chromium
playwright screenshot --browser chromium --wait-for-timeout 3000 \
  'http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200' \
  /tmp/aletheia-graph.png
```

Result: Graph page renders the portal nav, scope panel, SVG graph canvas, selected Employee node inspector, and 157 nodes / 156 edges status.

Blocked-state regression fix:

```bash
python3 <cdp-dom-check>
```

Result after fixing `graph_app.js` blocked/empty rendering:

- sandbox URL: `#graph-status=blocked`, `[data-node]=0`, `[data-edge]=0`
- default URL: `#graph-status=157 nodes / 156 edges`, `[data-node]=157`, `[data-edge]=156`

The fix clears SVG state in `renderBlocked()` and prevents `layoutGraph()` from creating a center node when the graph has no approved nodes.

## Current Server

Local server is running at:

`http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200`

## Handoff To Validation

@Saskue should validate task #74 against:

- Employee #4 baseline: 157 nodes / 156 edges.
- Canvas behavior: zoom, pan, select node, select edge, expand, collapse expanded, focus selected, fit view.
- Node inspector: identity, source row/properties, neighborhood summary, ontology artifact, Instance/Workbench links.
- Edge inspector: source/target, join condition, `link:employee:1:n:order`, status/version, source rows, Instance/Workbench links.
- URL restore for tenant/type/id/depth/limit/selected node or edge.
- Sandbox negative gate with no fallback.
- Scoped reasoning task creation and run: draft-only finding, approved-only scope, no canonical writes.
- Canonical safety: `link:employee:1:n:order` remains approved version 6.
