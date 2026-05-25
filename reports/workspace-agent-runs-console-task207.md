# Workspace Agent Runs Console - Task 207

## Scope

Moved automatic run-agent management into Workspace as a compact operator surface. Graph, Ontology, and Reasoning remain detail/review destinations rather than the primary place to manage running agents.

## Entry

Open:

`http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents`

Workspace now has an `Agent Runs` tab next to the Case Inbox tabs.

## Delivered

- Reused the existing `GET /api/agent-runs/console` data source.
- Added a compact Workspace `Agent Runs` panel showing:
  - agent kind: crawl / graph enrichment / reasoning
  - tenant
  - status
  - last run time
  - generated output count
  - skipped / failed count
  - next action
- Added selected-run compact detail:
  - kind / status / started time / output count
  - short timeline with query / hypothesis and extracted counts
  - write-boundary summary
- Added essential controls:
  - `Run once`
  - `Pause` / `Resume`
  - `Open results`
  - `Full run log`
  - `Open reasoning`
- Added review links to the existing destinations:
  - Graph proposed review
  - Ontology candidate review
  - Reasoning candidate findings

## Boundary

Workspace is an agent-run manager, not a second Graph / Ontology / Reasoning detail page.

- Ontology candidates still require Ontology review.
- Graph facts still go to proposed graph space.
- Findings still go to candidate / reviewed finding flow.
- Canonical ontology writes remain disabled.
- Formal graph writes remain disabled.

## Smoke Evidence

- API smoke:
  - `GET /api/agent-runs/console?tenant=maritime-risk&limit=20`
  - Returned the existing `maritime-risk` run feed.
- Browser DOM smoke:
  - URL: `http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents`
  - DOM includes:
    - `Agent Runs`
    - `Automatic agents`
    - `Automatic agent control`
    - `Run once`
    - `Open results`
    - `Full run log`
    - `Write boundary`
    - `Crawl`
    - `Graph enrich`
    - `Reasoning`

## Validation

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task207.js --format=iife --global-name=WorkbenchTask207 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`
- API smoke against 8772
- Chrome headless DOM smoke against 8772
