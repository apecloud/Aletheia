# Agent Runs Console - Task 204

## Scope

Added a unified run console so users can inspect automatic crawling, graph expansion, and deep reasoning from one place instead of switching between Ontology, Graph, and Reasoning pages.

## User Entry

Open:

`http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=runs`

The Graph page now has a left-side `Agent runs` tab next to `Approved graph`, `Proposed graph`, and `Saved views`.

## Delivered

- Added `GET /api/agent-runs/console`.
- The API aggregates three run families:
  - `web_enrichment_crawl`
  - `iterative_graph_enrichment`
  - `autopilot_deep_reasoning`
- Added `AL_API.agentRunsConsole`.
- Added Graph `Agent Runs Console` UI:
  - run timeline
  - run kind/status/time/counts
  - continuous enrichment session state
  - frontier
  - query / hypothesis trace
  - visited sources
  - skipped sources with reason
  - extracted nodes / edges / ontology enrichment proposals / candidate findings
  - confidence
  - evidence references
  - review status
  - write boundary
- Added jump from graph run results to the Proposed graph review detail via `Review` / `Open graph review`.

## Smoke Evidence

API smoke:

- `GET /api/agent-runs/console?tenant=maritime-risk&limit=20`
- Returned:
  - sessions: 1
  - runs: 21
  - `autopilot_deep_reasoning`: 10
  - `web_enrichment_crawl`: 7
  - `iterative_graph_enrichment`: 4

Browser DOM smoke:

- URL: `http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=runs`
- DOM includes:
  - `Agent Runs Console`
  - `Unified run timeline`
  - `web 7`
  - `graph 4`
  - `reasoning 10`
  - `Run trace`
  - `Visited / skipped sources`
  - `Open graph review`

## Boundary

The console is read/inspect/review navigation only.

- Web enrichment proposals still require ontology review.
- Proposed graph nodes/edges stay in proposed graph space.
- Autopilot findings stay candidate/draft unless separately reviewed.
- Canonical ontology writes remain disabled.
- Formal graph writes remain disabled.

## Validation

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`
- API smoke against 8772
- Chrome headless DOM smoke against 8772
