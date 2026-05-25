# Workspace IA: Work Queue and Agent Management

Task: #212
Tenant used for validation: `maritime-risk`
URL:
- Work Queue: `http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=workqueue`
- Agent / Auto enriching: `http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=enrichment`
- Agent / Autopilot reasoning: `http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=autopilot`

## Product Changes

- Workspace now has two primary tabs:
  - `Work Queue`: human review queue for proposed approval objects.
  - `Agent`: automatic agent management.
- `Work Queue` aggregates reviewable objects instead of reasoning cases:
  - ontology proposals from the ontology artifact catalog;
  - proposed graph nodes / edges / findings from proposed graph space;
  - candidate findings produced by Autopilot reasoning runs.
- Each Work Queue row shows object type, tenant, status, source/run, and next action.
- Work Queue details provide a direct handoff:
  - ontology proposals -> Ontology review;
  - graph node/edge/finding proposals -> Proposed graph review;
  - candidate findings -> Reasoning/Finding review.
- `Agent` now separates:
  - `Auto enriching`: crawl and graph enrichment runs.
  - `Autopilot reasoning`: deep reasoning runs.
- Each agent page has a compact parameter panel:
  - scope;
  - budget;
  - allowlist/safety;
  - cadence.
- Each agent page exposes bounded controls:
  - `Run once`;
  - `Pause` or `Pause / Resume`;
  - `Open results`;
  - `Full run log`;
  - `Open reasoning`.

## Validation Evidence

API smoke:
- `GET /api/artifacts?tenant=maritime-risk`: 20 artifacts, 15 approved and 5 draft.
- `GET /api/graph/proposed-elements?tenant=maritime-risk&limit=100`: 83 proposed elements, including 62 nodes, 11 edges, and 10 findings.
- `GET /api/agent-runs/console?tenant=maritime-risk&limit=20`: 21 runs, including 7 crawl, 4 graph enrichment, and 10 Autopilot reasoning runs.

Browser DOM smoke:
- `/tmp/workspace-task212-workqueue.html`
- `/tmp/workspace-task212-agents-enrichment.html`
- `/tmp/workspace-task212-agents-autopilot.html`

Observed DOM terms:
- `Work Queue`
- `Agent`
- `Ontology proposal`
- `Graph node`
- `Graph edge`
- `Candidate finding`
- `Review in ontology`
- `Review in graph`
- `Review finding`
- `Auto enriching`
- `Autopilot reasoning`
- `Agent settings`
- `Scope`
- `Budget`
- `Allowlist / safety`
- `Cadence`
- `Run once`
- `Pause`

## Write Boundary

This change only changes Workspace IA and management controls. It does not auto-approve or write review objects.

Current fingerprints after validation:
- Canonical ontology artifacts: approved = 15, draft = 5, total = 20.
- Formal graph context for `Chokepoint:Bab el-Mandeb Strait`: nodes = 1, edges = 0, center status = approved.

The Workspace continues to route decisions to owning review surfaces. Approval/rejection still happens in Ontology, Proposed graph review, or Reasoning/Finding review.

## Commands Run

```bash
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task212.js --format=iife --global-name=WorkbenchTask212 --log-level=warning
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
