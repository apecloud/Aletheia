# Interactive Graph Explorer Validation - task #74

Result: PASS.

I validated the canonical #70-#74 Interactive Graph Explorer queue after the blocked-state regression fix. The default Graph Explorer path is usable, the sandbox negative gate is visually and semantically empty, scoped reasoning remains draft-only, and canonical ontology/graph state did not change.

## Entrypoints

- Graph Explorer: <http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200>
- Sandbox blocked path: <http://127.0.0.1:8767/graph.html?tenant=northwind-sandbox&type=Employee&id=4&depth=1&limit=200>
- Context API: `GET /api/graph/context?tenant=default&type=Employee&id=4&depth=1&limit=200`
- Node API: `GET /api/graph/node/Employee%3A4?tenant=default`
- Edge API: `GET /api/graph/edge/Employee%3A4-%3EOrder%3A10250?tenant=default`
- Scoped reasoning API: `POST /api/reasoning/tasks/from-graph?tenant=default`

## Baseline

- Center node: `Employee:4` / Margaret Peacock.
- Default graph: 157 nodes / 156 edges.
- Orders: 156, matching the existing Employee #4 baseline.
- First 10 order IDs: 10250, 10252, 10257, 10259, 10260, 10261, 10267, 10281, 10282, 10284.
- Last 10 order IDs: 11018, 11024, 11026, 11029, 11040, 11044, 11061, 11062, 11072, 11076.
- Order ID checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.

## API Validation

- `GET /api/graph/context` for default returned `approved=true`, graph database `aletheia`, depth 1, limit 200, `truncated=false`, 157 nodes, 156 edges, `handled_orders=156`, and `returned_orders=156`.
- `depth=99&limit=999` was explicitly clamped to depth 2 and limit 300 with `limits.truncated=true`; this is visible in the returned state, not a silent truncation.
- `GET /api/graph/node/Employee%3A4` returned Employee identity, source row, `employees.employeeID=4`, `object:employee`, and neighborhood summary 157 nodes / 156 edges grouped under `link:employee:1:n:order`.
- `GET /api/graph/edge/Employee%3A4-%3EOrder%3A10250` returned source `Employee:4`, target `Order:10250`, join `orders.employeeID = employees.employeeID`, source field `orders.employeeID`, ontology link `link:employee:1:n:order`, status `approved`, version 6, and source/target rows.
- Sandbox context returned `approved=false`, 0 nodes, 0 edges, and missing `object:order` plus `link:employee:1:n:order`.
- Sandbox edge endpoint returned 404 for `Employee:4->Order:10250`; there was no default fallback.

## Browser Interaction

Playwright/Chrome validation confirmed the PRD interactions by DOM, URL, and state changes:

- Initial load showed `157 nodes / 156 edges`, 157 `[data-node]`, 156 `[data-edge]`, and Employee inspector title `Margaret Peacock`.
- Zoom changed the SVG transform scale from `0.42` to `0.504`.
- Pan changed the viewport translation from `translate(325, 391)` to `translate(385, 431)`.
- Selecting `Order:10250` updated the URL with `node=Order%3A10250`, rendered `ORDER NODE`, showed `Order #10250`, source row, and artifact link.
- Selecting `Employee:4->Order:10250` updated the URL with `edge=Employee%3A4-%3EOrder%3A10250`, rendered `GRAPH EDGE`, and showed the join, ontology link, artifact version, Instance link, and artifact link.
- Expand on `Employee:4` recorded expansion history and marked the center node expanded.
- Collapse restored the base graph to 157 nodes / 156 edges and cleared expansion history.
- Focus selected restored center `Employee:4` and URL state.
- Fit view reset the transform to scale `0.42`.
- Reloading the edge URL restored the same edge inspector and join condition.

## Blocked-State Regression

The earlier blocker was valid: sandbox API returned an empty blocked graph but the UI rendered one empty phantom node. After the `graph_app.js` fix, I reran browser validation:

- Sandbox URL status: `blocked`.
- Sandbox warning includes `object:order` and `link:employee:1:n:order`.
- Sandbox DOM: `[data-node] = 0`.
- Sandbox DOM: `[data-edge] = 0`.
- Default path still renders 157 nodes / 156 edges.

This regression is closed.

## Scoped Reasoning

I created and ran scoped reasoning tasks from both a selected node and a selected edge:

- Node task: `reasoning-graph-scope-default-employee-4-d1`.
- Edge task: `reasoning-graph-scope-default-employee-4-toorder-10250-d1`.
- Both scopes carried `tenant_id=default`, center node/edge, depth 1, node/edge limit 200, `approved_only=true`, allowed node types Employee/Order, allowed link `link:employee:1:n:order`, evidence paths, and `review_gate=draft_only`.
- Both runs completed with `draft_only=true`, no unsupported claims, and only draft findings.
- Recommended actions were `proposal_only`.
- Sandbox scoped task creation returned HTTP 400 with `center_node is outside the approved graph scope`.

## Canonical Safety

- `link:employee:1:n:order` remains `approved` version 6.
- Default graph still returns 157 nodes / 156 edges after browsing, expand/collapse, and scoped reasoning.
- Order ID checksum still matches the baseline.
- No canonical ontology/graph/review approval state was changed by Graph browse, expand, or reasoning.

## Verification Commands

```bash
node --check web/review_workbench/graph_app.js && node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
python3 /tmp/validate_graph_ui.py
```

All commands passed. Browser screenshot: `/tmp/aletheia-graph-validation.png`.

## Verdict

task #74 passes. #70-#73 are ready for product acceptance, subject to @Jobs/@Cindy review.
