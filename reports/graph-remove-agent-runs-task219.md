# Graph IA Cleanup - Task 219

## Goal

Remove Agent Runs from the Graph page and keep automatic crawl / enrichment / reasoning agent management in Workspace.

## Changes

- Removed the Graph page `Agent runs` tab and in-page Agent Runs Console rendering.
- Kept Graph page focused on `Approved graph`, `Proposed graph`, and `Saved views`.
- Removed the embedded `Continuous enrichment agent` operation block from `Proposed graph`.
- Removed Graph-page `Run cycle`, session/cycle counters, and latest-finding agent status controls.
- Added explicit handling for legacy `graph_tab=runs` deep links:
  - The page falls back to `Proposed graph`.
  - A lightweight notice points users to Workspace Agent management.
- Updated Workspace links that previously targeted `graph_tab=runs` to stay in `Workspace -> Agent`.
- Kept backend `/api/agent-runs/console` unchanged for Workspace Agent usage.

## Verification

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/graph-task219.js --format=iife --global-name=GraphTask219 --log-level=warning`
- `npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task219.js --format=iife --global-name=WorkbenchTask219 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`

## Browser Smoke

- Graph legacy URL:
  - `<http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=runs>`
  - Rendered `Approved graph`, `Proposed graph`, `Saved views`.
  - Rendered `Automatic runs moved` and `Open Workspace agents`.
  - Did not render `Agent runs`, `Agent Runs Console`, `RUN TRACE`, or `web_enrichment_crawl` in the app body.
- Proposed graph URL:
  - `<http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=proposed>`
  - Rendered proposed graph review controls such as `Proposed graph space` and `Batch review`.
  - Did not render `Continuous enrichment agent`, `Run cycle`, `Latest findings`, `session`, or `cycles` in the app body.
- Workspace Agent URL:
  - `<http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=enrichment>`
  - Rendered `Auto enriching`, `Autopilot reasoning`, `Run once`, and `Full run log`.

## API Smoke

- `/api/agent-runs/console?tenant=maritime-risk&limit=1` still returns run data for Workspace Agent.
- `/api/graph/proposed-elements?tenant=maritime-risk&limit=5` still returns proposed graph elements for Graph review.

## Boundary

This is an IA cleanup only. No backend run data was deleted; Graph only links to Workspace Agent management and no longer hosts agent operation controls. No ontology, proposed graph review, batch review, canonical graph, or formal graph write behavior was changed.
