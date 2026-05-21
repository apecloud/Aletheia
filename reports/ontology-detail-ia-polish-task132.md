# Task #132 Ontology Detail IA Polish

Date: 2026-05-21
Owner: @Itachi

## Scope

Polish the Ontology detail information architecture without changing the #128 live data contract.

Changes:

- Merged `Schema` and `Raw sources` into `Source & Schema`.
- Ordered `Source & Schema` as canonical schema first, source schema and raw source evidence below it.
- Kept field properties, source refs/evidence, source table, join mapping, and `schema_source=live/fallback` in the same view.
- Renamed `Review` to `Review history` and kept review decisions separate.
- Replaced the previous large canonical readiness block with `Governance & Impact`.
- Added a concise governance summary: canonical state, graph use, used-by flow count, blocking reason, graph database, and canonical write boundary.

## Browser Smoke

Service:

```bash
http://127.0.0.1:8772
```

Object detail:

```text
/?screen=ontology&tenant=default&artifact=object%3Aemployee
```

Rendered root contains:

- `Source & Schema`
- `Review history`
- `Governance & Impact`
- `Field properties and mapping`
- `Source refs and evidence`
- `employees.employeeID`
- `bigint`
- `reportsTo`
- `schema_source`
- `live`
- `Canonical state`
- `Graph use`
- `Definition payload` appears before `Field properties and mapping`

Relationship detail:

```text
/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder
```

Rendered root contains:

- `Source & Schema`
- `Review history`
- `Governance & Impact`
- `Field properties and mapping`
- `Source refs and evidence`
- `orders.employeeID = employees.employeeID`
- `employees.employeeID`
- `orders.employeeID`
- `relationship_reference`
- `schema_source`
- `live`
- `Canonical state`
- `Graph use`
- `Definition payload` appears before `Field properties and mapping`

Negative DOM checks:

- `Raw sources`: absent as a separate tab/label
- `Canonical readiness`: absent
- `Graph ingestion eligibility`: absent as the old large block

## Validation

Passed:

- Chrome rendered DOM smoke for `object:employee`
- Chrome rendered DOM smoke for `link:employee:1:n:order`
- Browser order check: canonical definition appears above source schema for both object and link details
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py`
- `git diff --check`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
