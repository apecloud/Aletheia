# Maritime-risk Dataset Import - task #165

## Result

Imported the Zenodo maritime chokepoint dataset into the Aletheia demo source DB and registered a new tenant:

- Tenant: `maritime-risk`
- Display name: `Maritime Chokepoint Risk`
- Source record: <https://zenodo.org/records/13841882>
- DOI: `10.5281/zenodo.13841882`
- Access right: open
- License: `CC-BY-4.0`

The import is repeatable through:

```bash
.venv/bin/python scripts/import_maritime_risk_dataset.py
```

The script downloads missing CSV files, loads MySQL source tables, registers the tenant in metadata Postgres, and upserts draft ontology artifacts.

## Source Files And Tables

| Source file | MySQL table | Rows | Notes |
| --- | --- | ---: | --- |
| `chokepoint_country_dependencies.csv` | `maritime_chokepoint_country_dependencies` | 4,950 | Country-to-chokepoint dependency rows; import adds `dependency_id = iso3::canal`. |
| `chokepoint_risk_indicators.csv` | `maritime_chokepoint_risk_indicators` | 24 | One row per chokepoint with hazard likelihood/timescale/severity indicators; import adds `risk_indicator_id = canal`. |
| `chokepoint_systemic_risk_results.csv` | `maritime_chokepoint_systemic_risk_results` | 4,752 | Country/chokepoint systemic risk results; import adds `risk_result_id = iso3::canal`. |

Generated JSON evidence:

- `reports/maritime-risk-import-task165.json`

## Ontology Draft Artifacts

Object draft artifacts:

- `object:chokepoint`
- `object:country`
- `object:trade_dependency`
- `object:hazard`
- `object:risk_indicator`
- `object:systemic_risk_result`
- `object:risk_finding`
- `object:mitigation_action`

Link draft artifacts:

- `link:country:n:m:chokepoint_dependency`
- `link:chokepoint:1:n:risk_indicator`
- `link:country:1:n:systemic_risk_result`
- `link:trade_dependency:n:1:country`
- `link:trade_dependency:n:1:chokepoint`
- `link:risk_finding:n:m:evidence`
- `link:mitigation_action:n:1:risk_finding`

All 15 artifacts are `draft`. None of this import approves canonical ontology or writes canonical graph state.

## Source Mapping

- `Country` maps to `maritime_chokepoint_country_dependencies.iso3`.
- `Chokepoint` and `Hazard/RiskIndicator` map to `maritime_chokepoint_risk_indicators.canal`.
- `TradeDependency` maps to `maritime_chokepoint_country_dependencies.dependency_id`.
- `SystemicRiskResult` maps to `maritime_chokepoint_systemic_risk_results.risk_result_id`.
- `RiskFinding` and `MitigationAction` are reasoning/action surfaces grounded in systemic risk rows; they remain draft abstractions until reviewed.

## Verification

Validated on local `http://127.0.0.1:8772`:

- `/api/tenants` includes `maritime-risk`.
- `/api/artifacts?tenant=maritime-risk` returns 15 artifacts: 8 object drafts and 7 link drafts.
- `/api/ontology/object:chokepoint?tenant=maritime-risk` returns `schema_source=live`, table `maritime_chokepoint_risk_indicators`, 29 fields.
