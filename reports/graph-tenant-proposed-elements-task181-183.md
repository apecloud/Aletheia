# Graph Tenant Scope and Proposed Elements

Tasks: #181 / #183

## Change

- Graph page center selection is now tenant-aware. It loads object types and center instances from `/api/instances/types?include_draft=1` and `/api/instances/search?include_draft=1`.
- Switching to `maritime-risk` no longer leaves the stale Northwind `Employee:4` center. The default center becomes a real maritime object such as `Chokepoint:Bab el-Mandeb Strait`.
- Graph page now has a `Proposed graph space` panel that exposes the iterative graph enrichment run and draft proposed elements.

## Maritime-risk Smoke

- `GET /api/instances/types?tenant=maritime-risk&include_draft=1` returns tenant objects:
  `Chokepoint`, `Country`, `TradeDependency`, `Hazard`, `RiskIndicator`, `SystemicRiskResult`, `RiskFinding`, `MitigationAction`.
- `GET /api/instances/search?tenant=maritime-risk&type=Chokepoint&include_draft=1` returns real center nodes including `Chokepoint:Bab el-Mandeb Strait`.
- `GET /api/graph/proposed-elements?tenant=maritime-risk&limit=20` returns run `iterative-graph:maritime-risk:20260523163823:17969` with `proposed_count=11`, `finding_count=1`, `pruned_count=1`.
- Proposed element counts: 6 draft nodes, 4 draft edges, 1 draft deep graph finding.

## Boundary

- The proposed graph panel is display-only.
- Proposed elements remain draft/proposed and do not write canonical ontology or the formal graph.
- Official graph context remains approved-only; proposed graph expansion is shown in a separate panel to avoid mixing draft graph elements with canonical graph state.

## Verification

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`
