# Task #175 - U.S.-Iran Conflict Web Research Graph Import

## Summary

Implemented a new `us-iran-war` tenant as a web-researched conflict/economic-impact graph demo.

The import is deliberately review-first:

- web research results are stored as source/provenance rows;
- extracted ontology artifacts are all `draft`;
- `graph_database=us_iran_war` is registered as the graph space;
- no canonical ontology artifacts are approved;
- no graph ingestion is performed by the import.

## Source Selection

The web-search snapshot uses public, reviewable sources:

- EIA: <https://www.eia.gov/todayinenergy/detail.php?id=65504>
- IEA: <https://www.iea.org/about/oil-security-and-emergency-reserve/strait-of-hormuz>
- CRS: <https://www.congress.gov/crs-product/R47321>
- IMF: <https://www.imf.org/en/Blogs/Articles/2023/10/24/how-war-in-the-middle-east-could-affect-the-world-economy>
- World Bank commodity markets: <https://www.worldbank.org/en/research/commodity-markets>
- OFAC Iran sanctions: <https://ofac.treasury.gov/sanctions-programs-and-country-information/iran-sanctions>

Every source row includes `query`, `url`, `publisher`, `retrieved_at`, `summary`, `confidence`,
`robots_risk`, and `license_risk`.

## Loaded Source Tables

| Table | Rows | Purpose |
|---|---:|---|
| `us_iran_war_web_sources` | 6 | Web provenance and search metadata |
| `us_iran_war_conflict_events` | 2 | U.S.-Iran conflict/sanctions events |
| `us_iran_war_economic_channels` | 4 | Oil, LNG, macro, sanctions channels |
| `us_iran_war_country_exposures` | 8 | Country/region economic exposure records |
| `us_iran_war_recommended_actions` | 3 | Analyst review / mitigation actions |
| `us_iran_war_graph_edges` | 15 | Graph edges with source and confidence |

Dataset files were written under `datasets/us_iran_war/`; machine report:
`reports/us-iran-war-import-task175.json`.

## Ontology Drafts

Seeded 10 draft ontology artifacts:

- 6 draft objects:
  - `ConflictEvent`
  - `EconomicChannel`
  - `CountryExposure`
  - `RecommendedAction`
  - `SourceDocument`
  - `GraphEdge`
- 4 draft links:
  - `ConflictEvent -> EconomicChannel`
  - `EconomicChannel -> CountryExposure`
  - `CountryExposure -> RecommendedAction`
  - `SourceDocument -> GraphEdge`

Artifact count check:

```json
[
  {"artifact_type": "link", "status": "draft", "count": 4},
  {"artifact_type": "object", "status": "draft", "count": 6}
]
```

Approved artifact count for `us-iran-war`: `0`.

## Graph Reasoning Path

The strongest initial path is:

```text
event_2025_june_us_iran_escalation
  -> channel_hormuz_oil_flow
  -> country_IND
  -> action_energy_importer_stress_test
```

It supports the reasoning claim:

```text
U.S.-Iran escalation can raise Hormuz oil-flow disruption risk;
Hormuz oil-flow disruption has high exposure for India as an Asian energy importer;
therefore India should be included in importer energy exposure stress tests.
```

The same graph pattern also covers China, Japan, South Korea, Qatar, the United States,
Saudi Arabia, and the Euro area through oil, LNG, sanctions, and macro price channels.

## Validation

Validated:

- tenant exists: `tenant_id=us-iran-war`, `namespace=us_iran_war`, `graph_database=us_iran_war`;
- all six source tables loaded with expected row counts;
- source schema for `object:country_exposure` is live with 9 fields;
- relationship schema for `link:economic_channel:1:n:country_exposure` is live;
- multi-hop SQL path resolves event -> channel -> country -> action;
- all ontology artifacts remain `draft`;
- approved/canonical artifact count remains `0`.

Verification commands:

```bash
.venv/bin/python scripts/import_us_iran_war_dataset.py
.venv/bin/python -m unittest tests/test_us_iran_war_import.py
.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_web_enrichment.py tests/test_reasoning_deep_graph.py
.venv/bin/python -m py_compile review_workbench.py scripts/import_us_iran_war_dataset.py agents/tenant_registry.py
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/screens.jsx --bundle --format=esm --outfile=/tmp/task175-screens.js
git diff --check
```

