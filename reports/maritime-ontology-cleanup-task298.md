# Maritime Ontology Cleanup - task #298

## Result

Cleaned `maritime-risk` ontology-related metadata after the raw data reimport.

Deleted:

- 15 `maritime-risk` ontology artifacts
  - 8 draft object artifacts
  - 7 draft link artifacts
- 30 linked ontology evidence rows
- 0 artifact reviews
- 0 web enrichment proposals/runs
- 0 business object/link/action projection rows

Preserved:

- `maritime_chokepoint_country_dependencies`: 4,950 rows
- `maritime_chokepoint_risk_indicators`: 24 rows
- `maritime_chokepoint_systemic_risk_results`: 4,752 rows
- tenant registry entry for `maritime-risk`
- other tenant ontology artifacts

## Verification

API smoke:

- `GET /api/artifacts?tenant=maritime-risk` returns `artifacts=[]` and `stats=[]`.
- `GET /api/artifacts?tenant=default` still returns default tenant artifacts.
- `GET /api/tenants` still includes `maritime-risk`.

Database boundary:

- source table counts before and after are identical.
- other tenant artifact counts after cleanup:
  - `default`: 8
  - `creditcardfraud`: 7
  - `us-iran-war`: 10

Machine report: `reports/maritime-ontology-cleanup-task298.json`.
