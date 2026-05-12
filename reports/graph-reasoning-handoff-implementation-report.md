# Graph Reasoning Handoff Implementation

Task: #85 Graph -> Reasoning Handoff

## Summary

Graph Explorer now carries the current graph selection into Reasoning Workbench. Selecting a node or edge and opening Reasoning no longer falls back to the fixed Employee #4 workload task.

## Changes

- Updated `web/review_workbench/graph_app.js`
  - Builds a `source=graph` Reasoning URL from the selected node or edge.
  - Includes tenant, center node/edge, depth, limit, question, graph URL, and evidence path metadata.
  - Updates the top Reasoning tab and Inspector reasoning link after selection.
  - Makes the scoped reasoning button open the selected graph context directly.

- Updated `web/review_workbench/reasoning_app.js`
  - Detects `source=graph` URL context.
  - Creates or updates a draft-only scoped reasoning task through `/api/reasoning/tasks/from-graph`.
  - Opens the created scoped task instead of the default fixed task.
  - Auto-runs the scoped task once when no latest run exists, producing a draft finding only.
  - Displays scoped node/edge labels in the title, breadcrumb, and task list.

- Updated `web/review_workbench/graph.html`
  - Renamed the action from "Create scoped reasoning task" to "Open scoped reasoning".

## Local Validation

- Real Chrome/Playwright user flow:
  - Graph select `Employee:4` node label -> Reasoning tab -> `Scoped reasoning: Employee:4`.
  - Reasoning evidence contains `Employee:4`.
  - Draft finding appears with scoped graph evidence.
  - Graph select `Employee:4->Order:10640` edge line coordinate -> Reasoning tab -> `Scoped reasoning: Employee:4 -> Order:10640`.
  - Reasoning evidence contains `Employee:4->Order:10640` and `orders.employeeID`.
  - Trace contains `draft_reasoning_artifact`.

- Static and regression checks:
  - `node --check web/review_workbench/graph_app.js`
  - `node --check web/review_workbench/reasoning_app.js`
  - `node --check web/review_workbench/app.js`
  - `node --check web/review_workbench/instance_app.js`
  - `node --check web/review_workbench/settings_app.js`
  - `git diff --check`
  - `.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests`
  - `.venv/bin/python -m unittest tests/test_ontology_eval.py`

- Safety regression:
  - Default graph remains `approved=True`, 157 nodes, 156 edges.
  - Sandbox graph remains `approved=False`, 0 nodes, 0 edges.
  - `link:employee:1:n:order` remains approved version 6.

## Boundary

The handoff creates and runs draft-only scoped reasoning. It does not approve findings, ingest new ontology artifacts, or change canonical graph/review state.
