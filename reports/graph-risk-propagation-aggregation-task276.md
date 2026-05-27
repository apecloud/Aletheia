# Graph Risk Propagation Aggregation - task 276

## Summary

Changed the Graph page display model for maritime systemic-risk paths: the main graph now aggregates the three low-level projections (`SystemicRiskResult`, `RiskFinding`, `MitigationAction`) into one readable `risk propagation` edge between the impacted `Country` and `Chokepoint`.

## Product Rationale

The previous graph displayed three visually similar edge groups from the same `risk_result_id`:

- `SystemicRiskResult -> Country / Chokepoint`
- `RiskFinding -> Country / Chokepoint`
- `MitigationAction -> Country / Chokepoint`

They are semantically different, but showing them as peer edges made the main graph look duplicated. The graph should emphasize the business relation first: risk propagates between a chokepoint and an exposed country.

## Implementation

Frontend-only display aggregation in `web/review_workbench/graph.jsx`:

- Detect raw `risk_country` and `risk_chokepoint` edges whose source node is one of:
  - `SystemicRiskResult:*`
  - `RiskFinding:*`
  - `MitigationAction:*`
- Pair country and chokepoint edges by the original source node.
- Group them by `Country -> Chokepoint`.
- Render one aggregate edge:
  - `link_key = risk_propagation`
  - `label = risk propagation`
- Preserve the original three-layer semantics in the aggregate edge:
  - `layers[]` contains `SystemicRiskResult`, `RiskFinding`, and `MitigationAction` layer summaries.
  - `raw_edges[]` keeps the original raw graph edge objects for traceability.

## Inspector Detail

When a selected node has a `risk propagation` connection, the Connected edges panel expands:

- `SystemicRiskResult`: model/data result; answers “how much exposure/risk exists”.
- `RiskFinding`: reviewable insight; answers “whether this should become a finding”.
- `MitigationAction`: action layer; answers “who should review or mitigate next”.

## Boundary

- No backend schema changes.
- No canonical ontology writes.
- No formal graph writes.
- No proposed graph review status changes.
- Raw graph facts are still available in `_raw.raw_edges` and `_raw.layers`; only the visual graph edge presentation is aggregated.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task276.js --format=iife --log-level=warning`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/web_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py`
- `git diff --check`
