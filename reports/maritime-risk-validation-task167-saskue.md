# Maritime-risk Validation - task #167

## Result

**PASS after fix `171a771` (`Complete maritime risk evidence chains`).**

The previous FAIL was valid: `Single chokepoint dependency creates concentrated country exposure` lacked a hazard step. The rerun now shows all retained maritime-risk candidate findings have complete `hazard -> chokepoint -> dependent country -> trade/risk metric -> recommended action` chains. Import, tenant isolation, live schema, draft-only, and no canonical/graph-write boundaries also pass.

## Source / License / Import

- Source record: https://zenodo.org/records/13841882
- DOI: `10.5281/zenodo.13841882`
- License reported by import: `CC-BY-4.0`
- Source checked against Zenodo record: dataset is open and lists the expected chokepoint CSV files.
- Imported source rows:
  - `maritime_chokepoint_country_dependencies`: 4950
  - `maritime_chokepoint_risk_indicators`: 24
  - `maritime_chokepoint_systemic_risk_results`: 4752
- Row-count check: PASS

## Tenant / Ontology Schema

- Tenant present: `maritime-risk`, graph database `maritime_risk`.
- Ontology artifacts: 15 total, statuses {'draft': 15}; all draft.
- `object:chokepoint` source schema: `live`, table `maritime_chokepoint_risk_indicators`, fields `29`.
- Default and creditcardfraud catalogs do not include maritime/chokepoint artifacts.

## Playbook Evidence Chain

- Validation session: `autopilot:maritime-risk:task167-validation-pass`
- Session status: `draft`
- Safety: `canonical_writes=disabled`, `auto_approve_findings=False`, `write_scope=draft_only`
- Hypotheses: 4 total; 1 volume-only hypothesis is pruned with reason.

### PASS: Bab el-Mandeb risk propagation identifies countries for immediate review
- Status: `draft`; evidence steps: 6
- Kinds: `hazard, hazard, chokepoint, dependent_countries, risk_metric, recommended_action`
- Metrics: `likelihood_conflict, severity_conflict, canal, top_trade_at_risk_v, top_trade_at_risk_v, country_priority_review`
- Non-null hazard values: `0.673076923076923, 0.5`
- Demo chain verified: hazard (`likelihood_conflict`, `severity_conflict`) -> `Bab el-Mandeb Strait` -> CHN/IND/USA dependent countries -> `trade_at_risk_v` / `trade_impacted` -> analyst review action.

### PASS: Hazard-adjusted chokepoint risk should drive review priority
- Status: `draft`; evidence steps: 6
- Kinds: `hazard, chokepoint, dependent_country, risk_metric, risk_metric, recommended_action`
- Metrics: `chokepoint, canal, iso3, trade_at_risk_v, trade_impacted, risk_review_queue`
- Non-null hazard values: `Taiwan Strait`

### PASS: Single chokepoint dependency creates concentrated country exposure
- Status: `draft`; evidence steps: 7
- Kinds: `hazard, hazard, chokepoint, dependent_country, trade_metric, risk_metric, recommended_action`
- Metrics: `likelihood_conflict, likelihood_geopolitical, canal, iso3, v_canal, v_canal / v, portfolio_review`
- Non-null hazard values: `13.333333333333334`
- Previous gap closed: this candidate now includes hazard evidence from `maritime_chokepoint_risk_indicators` before chokepoint/country/trade/action.

## Negative Boundaries

- All candidate findings are `draft`: PASS
- Wrong-tenant maritime playbook calls for `default` and `creditcardfraud` return HTTP 400: PASS
- Approved maritime ontology artifacts before/after playbook: `0 -> 0`: PASS
- Unexpected canonical/graph DB count changes: none: PASS
- Default/creditcardfraud tenant isolation: PASS

## Verification Commands

- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py scripts/import_maritime_risk_dataset.py scripts/bootstrap_demo_environment.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`

## Outcome

Task #167 can move to review. The maritime-risk demo now demonstrates graph reasoning rather than CSV ranking: the retained candidate findings explain how hazards propagate through chokepoints into country exposure metrics and recommended review actions.
