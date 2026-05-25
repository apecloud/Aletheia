# Continuous Enrichment MVP - Task 201

## Scope

Implemented the first product/system loop for a bounded continuous enrichment agent.

This is not an unbounded background daemon yet. The MVP provides a durable session model, status API, manual run cycle, UI entry point, and the same proposed-only write boundary as the existing graph enrichment pipeline.

## Delivered

- Added continuous enrichment session storage:
  - `aletheia_continuous_enrichment_sessions`
  - session key: `continuous:maritime-risk:us-iran-impact:mvp`
  - status: `idle / paused / stopped / running`
  - persisted objective, frontier, config, cycle count, and last run key
- Added APIs:
  - `GET /api/enrichment/sessions`
  - `GET /api/enrichment/sessions/{session_key}`
  - `POST /api/enrichment/sessions/{session_key}/run-cycle`
  - `POST /api/enrichment/sessions/{session_key}/pause`
  - `POST /api/enrichment/sessions/{session_key}/resume`
  - `POST /api/enrichment/sessions/{session_key}/stop`
- Connected the Graph `Proposed graph` tab to the session:
  - shows agent status, cycle count, latest run, latest findings
  - adds a `Run cycle` button
  - refreshes proposed graph results after a completed cycle
- Reused `IterativeGraphEnrichmentAgent` for the cycle execution.

## Smoke Run

- Tenant: `maritime-risk`
- Session: `continuous:maritime-risk:us-iran-impact:mvp`
- Run key: `iterative-graph:maritime-risk:20260525074247:75452`
- Returned proposed elements: 70
  - nodes: 56
  - edges: 11
  - findings: 3
- Skipped sources: 4 non-allowlisted `example.org` sources

## New Candidate Findings

1. `Bab el-Mandeb Strait risk propagates to CHN, IND, USA`
   - Path: `likelihood_conflict -> Bab el-Mandeb Strait -> CHN, IND, USA -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`
2. `Hormuz Strait risk propagates to JPN, KOR`
   - Path: `shipping disruption -> Hormuz Strait -> JPN, KOR -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`
3. `Malacca Strait risk propagates to CHN, JPN, KOR`
   - Path: `likelihood_geopolitical -> Malacca Strait -> CHN, JPN, KOR -> trade_impacted -> Run analyst review on exposed country/chokepoint path`

## Write Boundary

- Ontology candidates still require review.
- Graph facts are written only to proposed graph space.
- Findings remain candidate/proposed findings.
- Canonical ontology writes are disabled.
- Formal graph writes are disabled.

## Validation

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`
- `GET /api/enrichment/sessions?tenant=maritime-risk`
- `POST /api/enrichment/sessions/continuous%3Amaritime-risk%3Aus-iran-impact%3Amvp/run-cycle?tenant=maritime-risk`
- `GET /api/graph/proposed-elements?tenant=maritime-risk&run_key=iterative-graph%3Amaritime-risk%3A20260525074247%3A75452&limit=120`

## Follow-Up

Next step is replacing the manual cycle with a scheduler/worker loop that respects the same session budget, allowlist, pause/resume/stop state, dedupe rules, and proposed-only write boundary.
