# Agent Chaining Implementation - Task 215

## Scope

Implemented the Auto enriching -> Reasoning Autopilot handoff in Workspace without bypassing review gates.

## Changes

- Continuous enrichment session config now persists cadence and scheduling parameters:
  - `manual`, `hourly`, `daily`, `custom`
  - `custom_interval_minutes`
  - `rate_limit_per_cycle`
  - `stop_condition`
  - `next_run_at`
- Added session configure API:
  - `POST /api/enrichment/sessions/{session_key}/configure`
- Continuous run-cycle now uses incremental frontier:
  - runs against stored frontier items first;
  - newly generated proposed graph nodes/edges become next-cycle frontier;
  - visited frontier keys are tracked to avoid re-crawling the same object.
- Each run-cycle emits chain events:
  - `graph_changed`
  - `new_evidence_available`
  - `autopilot_triggered`
- `new_evidence_available` triggers Reasoning Autopilot with a linked session key:
  - `autopilot:{tenant}:continuous-enrichment:{run_key}`
  - findings stay candidate/draft only.
- Workspace Agent tab now exposes:
  - cadence selector including custom interval;
  - custom minutes;
  - stop condition;
  - Save settings;
  - latest agent-chain events.

## Smoke Evidence

Configured the maritime-risk continuous session:

```json
{
  "cadence": "custom",
  "custom_interval_minutes": 5,
  "allowlist": ["zenodo.org"],
  "max_frontier": 3,
  "next_run_at": "2026-05-25T16:53:43.025844"
}
```

First run-cycle:

```json
{
  "run_key": "iterative-graph:maritime-risk:20260525164853:98165",
  "returned_element_count": 42,
  "frontier_used_count": 2,
  "new_frontier_count": 39,
  "event_types": ["graph_changed", "new_evidence_available", "autopilot_triggered"],
  "autopilot_session_key": "autopilot:maritime-risk:continuous-enrichment:iterative-graph-maritime-risk-20260525164853-98165"
}
```

Second run-cycle proved incremental frontier reuse:

```json
{
  "run_key": "iterative-graph:maritime-risk:20260525164903:98165",
  "frontier_used": [
    "CHN depends on Bab el-Mandeb Strait",
    "CHN depends on Malacca Strait"
  ],
  "frontier_sources": ["proposed_graph", "proposed_graph"],
  "event_types": ["graph_changed", "new_evidence_available", "autopilot_triggered"]
}
```

Agent Runs Console now shows the linked chain:

```json
{
  "latest_events": [
    "new_evidence_available",
    "autopilot_triggered",
    "graph_changed",
    "new_evidence_available",
    "autopilot_triggered"
  ],
  "autopilot_linked": [
    "autopilot:maritime-risk:continuous-enrichment:iterative-graph-maritime-risk-20260525164903-98165",
    "autopilot:maritime-risk:continuous-enrichment:iterative-graph-maritime-risk-20260525164853-98165"
  ]
}
```

Chrome DOM smoke:

- URL: <http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=enrichment>
- DOM capture: `/tmp/task215-workspace-agent-dom.html`
- Screenshot: `/tmp/task215-workspace-agent-chain.png`
- Required text found: `Auto enriching agent`, `Custom minutes`, `Stop condition`, `Agent chain`, `graph_changed`, `new_evidence_available`, `autopilot_triggered`, `Canonical writes`.

## Boundaries

- Ontology candidates still require review.
- Proposed graph nodes/edges stay in proposed graph space.
- Autopilot produces candidate findings only.
- `canonical_write=false`.
- `formal_graph_write=false`.
- No automatic finding approval.

## Verification

```bash
.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task215.js --format=iife --global-name=WorkbenchTask215 --log-level=warning
.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py
git diff --check
```

All commands passed. Server `8772` was restarted on the updated code.
