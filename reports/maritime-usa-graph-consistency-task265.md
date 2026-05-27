# Maritime USA Graph Consistency - task 265

## Summary

Fixed the `maritime-risk` graph context so a selected country center, including `Country:USA`, brings its maritime dependency and systemic-risk context into the approved graph response.

## Root Cause

`view=all` sampled approved nodes per ontology type and then forced the selected center node into the result. For countries late in sort order, such as `USA`, the center node appeared but related `TradeDependency`, `SystemicRiskResult`, `RiskFinding`, `MitigationAction`, and `Chokepoint` nodes were not guaranteed to be in the sampled node set. Edge construction only emits edges when both endpoints are present, so all USA edges were dropped.

## Fix

- When `tenant=maritime-risk` and a center node is requested, load center-related rows from:
  - `maritime_chokepoint_country_dependencies`
  - `maritime_chokepoint_systemic_risk_results`
  - `maritime_chokepoint_risk_indicators`
- Force the related approved graph nodes into the response before edge construction.
- Add the same center-related rows into edge construction so the selected country has visible dependency/risk edges.
- Keep the source as approved import tables and approved ontology artifacts; no finding text is converted into canonical graph facts.

## Smoke Evidence

`GET /api/graph/context?tenant=maritime-risk&type=Country&id=USA&depth=1&limit=200&view=all`

- Before fix: `Country:USA` present, `usa_edges=0`.
- After fix: `Country:USA` present, `usa_edges=97`.
- Examples now visible:
  - `TradeDependency:USA::Bab el-Mandeb Strait -> Country:USA`
  - `TradeDependency:USA::Panama Canal -> Country:USA`
  - `SystemicRiskResult:* -> Country:USA`
  - `RiskFinding:* -> Country:USA`
  - `MitigationAction:* -> Country:USA`

`GET /api/graph/context?tenant=maritime-risk&type=Country&id=CHN&depth=1&limit=200&view=all`

- `Country:CHN` remains visible with `country_edges=97`, covering the existing CHN path.

## Boundary

The fix only changes approved graph response hydration for already-imported source rows. It does not auto-promote candidate findings, does not create proposed graph elements, and does not write canonical ontology or formal graph data.
