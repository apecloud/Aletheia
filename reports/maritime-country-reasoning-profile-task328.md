# Maritime Country Reasoning Profile - task #328

## Scope

Improve scoped reasoning for `maritime-risk` country centers so a country such as `Country:CHN` does not produce an existence-only finding like "0 related entities".

## Change

- Added a source-key profile aggregation path in `reasoning_engine.py`.
- When legacy graph relationship rankings are sparse, the reasoning engine now inspects current tenant approved ontology source refs, finds source tables sharing the center key, and aggregates:
  - source-key row degree,
  - connected source tables,
  - distinct connected path labels,
  - top chokepoint/risk paths ranked by risk/trade metric columns.
- Updated reasoning run evidence paths in `server/workbench_server.py` so source-key country profiles are labeled as `Maritime Exposure Profile` and cite `degree + source-key metric aggregation`.

## Evidence

Direct smoke for `maritime-risk / Country:CHN`:

- Title: `CHN Maritime Exposure Profile: 49 source rows, top path Taiwan Strait`
- Summary: `CHN has 49 source rows across 2 related source table(s), covering 49 distinct path labels.`
- Connected source tables:
  - `maritime_chokepoint_country_dependencies`: 25 rows, 25 distinct `canal`
  - `maritime_chokepoint_systemic_risk_results`: 24 rows, 24 distinct `canal`
- Top paths:
  - `Taiwan Strait` by `v_canal`
  - `Malacca Strait` by `v_canal`
  - `Korea Strait` by `v_canal`
  - `Bohai Strait` by `v_canal`
  - `Bab el-Mandeb Strait` by `v_canal`

Boundary smoke:

- `default / Employee:4` still uses the existing `Business Profile` ranking path.
- `maritime-risk` source-key aggregation only uses source tables referenced by approved ontology artifacts for that tenant, so `us_iran_war_*` source tables are not mixed into the maritime profile.
- No canonical ontology or formal graph writes are introduced.

## Validation

- `.venv/bin/python -m py_compile reasoning_engine.py server/workbench_server.py review_workbench.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_schema_graph_modeling_agent.py`
- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task328.js --format=iife --global-name=AletheiaReasoning --log-level=warning`
- HTTP smoke after restarting 8772:
  - created `reasoning:graph-scope:maritime-risk-question-center-graph-node-country-chn-d1-n200-e200-q0137446113`
  - reran the task through `/api/reasoning/tasks/<task>/run`
  - returned `CHN Maritime Exposure Profile`
  - returned `49 source rows`
  - did not return `0 related entities`
- `git diff --check`
