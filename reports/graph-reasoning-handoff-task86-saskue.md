# Graph Reasoning Handoff Validation - task #86

Result: PASS.

I validated the #85 Graph -> Reasoning handoff with real browser flows. The user path now behaves as expected: select a node or edge in Graph Explorer, click the top `Reasoning` tab, and Reasoning Workbench opens the corresponding scoped reasoning task instead of falling back to the fixed Employee #4 workload task.

## Scope

- User issue: after selecting a node in the graph, switching to the Reasoning tab still showed old/default data.
- Implementation under review: task #85.
- Validation entrypoint: <http://127.0.0.1:8767/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200>.
- Implementation report: `reports/graph-reasoning-handoff-implementation-report.md`.

## Node Handoff

Path tested: Graph -> real click `Employee:4` text label -> top `Reasoning` tab.

- The Reasoning tab URL included `source=graph`, `center_node=Employee%3A4`, `evidence_kind=graph_node`, and `autorun=1`.
- Reasoning URL opened task `reasoning-graph-scope-default-employee-4-d1`.
- Page title: `Scoped reasoning: Employee:4`.
- Breadcrumb: `Reasoning / Graph node Employee:4`.
- Evidence showed `Employee:4`, `graph_node`, and `employeeID=4`.
- Run status: `completed`.
- Finding status: `draft · v1`.
- Trace/eval contained `approved_only=true`, `draft_only=true`, and `draft_reasoning_artifact`.
- It did not display the fixed old `reasoning:employee-4-workload-analysis` as the active task.
- Refreshing the Reasoning URL restored the same scoped node context.

## Edge Handoff

Path tested: Graph -> real click edge line for `Employee:4->Order:10640` -> top `Reasoning` tab.

- The graph click hit a real SVG `line` inside edge `Employee:4->Order:10640`.
- The Reasoning tab URL included `source=graph`, `center_edge_source=Employee%3A4`, `center_edge_target=Order%3A10640`, `evidence_kind=graph_edge`, `ontology_link=link%3Aemployee%3A1%3An%3Aorder`, and `autorun=1`.
- Reasoning URL opened task `reasoning-graph-scope-default-employee-4-toorder-10640-d1`.
- Page title: `Scoped reasoning: Employee:4 -> Order:10640`.
- Breadcrumb: `Reasoning / Graph edge Employee:4 -> Order:10640`.
- Evidence showed `Employee:4->Order:10640`, `graph_edge`, and `orders.employeeID`.
- Run status: `completed`.
- Finding status: `draft · v1`.
- Trace/eval contained `approved_only=true`, `draft_only=true`, and `draft_reasoning_artifact`.
- Refreshing the Reasoning URL restored the same scoped edge context.

## Existing Task Behavior

The handoff uses canonical scoped task keys:

- `reasoning-graph-scope-default-employee-4-d1`
- `reasoning-graph-scope-default-employee-4-toorder-10640-d1`
- `reasoning-graph-scope-default-employee-4-toorder-10250-d1`

When the selected node/edge task already exists, the UI opens/upserts that scoped task key rather than creating unbounded duplicate task keys.

## Regression Checks

- Default graph remains `approved=true`, 157 nodes, 156 edges.
- Employee #4 order checksum remains `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph remains `approved=false`, 0 nodes, 0 edges, missing `object:order` and `link:employee:1:n:order`.
- Sandbox UI remains blocked with 0 `[data-node]` and 0 `[data-edge]`.
- `link:employee:1:n:order` remains `approved` version 6.

## Verification Commands

```bash
python3 /tmp/validate_graph_reasoning_handoff_task86.py
node --check web/review_workbench/graph_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

All commands passed. Browser screenshot: `/tmp/aletheia-graph-reasoning-handoff-task86.png`.

## Verdict

task #86 passes. task #85 is ready for product/engineering acceptance.
