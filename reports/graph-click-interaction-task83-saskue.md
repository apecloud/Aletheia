# Graph Click Interaction Validation - task #83

Result: PASS.

I validated the #82 click-interaction fix with real browser mouse coordinates. This was not a `dispatch_event` test: the script moved the mouse to actual rendered SVG coordinates and clicked node circles, node text labels, and an edge line/hit area.

## Scope

- User issue: clicking graph nodes and edges appeared to do nothing.
- Fix under review: #82, touching `web/review_workbench/graph_app.js` and `web/review_workbench/styles.css`.
- Validation entrypoint: <http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200>.

## Real Click Results

### Node Circle

Target: `Order:10250` rendered circle.

- Real click point: `(987.4, 648.39)`.
- URL changed to include `node=Order%3A10250`.
- Status bar changed to `selected node Order:10250`.
- SVG selected node class: `Order:10250`.
- Adjacent edge highlight count: 1.
- Inspector changed to `ORDER NODE`.
- Inspector title: `Order #10250`.

### Node Text Label

Target: `Employee:4` rendered text label.

- Real click point: `(685.0, 669.07)`.
- URL changed to include `node=Employee%3A4`.
- Status bar changed to `selected node Employee:4`.
- SVG selected node class: `Employee:4`.
- Adjacent edge highlight count: 156.
- Inspector changed to `EMPLOYEE NODE`.
- Inspector title: `Margaret Peacock`.

### Edge Line / Hit Area

Target: real SVG line coordinate. `elementFromPoint` resolved to a `line` inside edge `Employee:4->Order:10640`.

- Real click point: `(902.73, 648.39)`.
- URL changed to include `edge=Employee%3A4-%3EOrder%3A10640`.
- Status bar changed to `selected edge Employee:4->Order:10640`.
- SVG selected edge class: `Employee:4->Order:10640`.
- Endpoint nodes highlighted: `Employee:4`, `Order:10640`.
- Inspector changed to `GRAPH EDGE`.
- Inspector title: `Employee:4->Order:10640`.
- Inspector provenance contains `orders.employeeID = employees.employeeID`.
- Inspector provenance contains `link:employee:1:n:order`.

### URL Restore

Reloading the selected edge URL restored:

- selected edge: `Employee:4->Order:10640`.
- endpoint nodes: `Employee:4`, `Order:10640`.
- Inspector kind: `GRAPH EDGE`.
- Inspector title: `Employee:4->Order:10640`.

## Regression Checks

- Default graph API still returns `approved=true`, 157 nodes, 156 edges.
- Employee #4 order checksum remains `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox API remains blocked with 0 nodes / 0 edges.
- Sandbox UI remains `blocked` with 0 `[data-node]` and 0 `[data-edge]`.
- `link:employee:1:n:order` remains `approved` version 6.

## Verification Commands

```bash
python3 /tmp/validate_graph_click_task83.py
node --check web/review_workbench/graph_app.js && node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
curl -sS 'http://127.0.0.1:8767/api/graph/context?tenant=default&type=Employee&id=4&depth=1&limit=200'
```

All commands passed. Browser screenshot: `/tmp/aletheia-graph-click-task83.png`.

## Verdict

task #83 passes. #82 is ready for product/engineering acceptance.
