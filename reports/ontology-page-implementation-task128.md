# Task #128 Ontology Page Implementation Evidence

Date: 2026-05-21
Owner: @Itachi

## Scope

Move ontology-owned governance information into the Ontology page:

- raw data source and source schema mapping
- canonical schema definition
- review/audit process
- canonical readiness and graph ingestion eligibility
- used-by / impact links into reasoning and graph contexts

Workspace remains lightweight, and Reasoning links to Ontology instead of rebuilding governance details.

## API Smoke

Service used for smoke test:

```bash
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8771
```

Catalog:

```bash
curl -sS 'http://127.0.0.1:8771/api/ontology/catalog?tenant=default'
```

Observed key fields:

- tenant: `default / northwind / aletheia`
- artifact count: `10`
- first keys include `link:employee:1:n:order`

Detail:

```bash
curl -sS 'http://127.0.0.1:8771/api/ontology/link%3Aemployee%3A1%3An%3Aorder?tenant=default'
```

Observed key fields:

- artifact: `link:employee:1:n:order`
- status/version: `approved v6`
- source schema join: `orders.employeeID = employees.employeeID`
- canonical readiness: `approved`, `graph_ingestion_eligible=true`
- review history count: `5`
- used-by kinds: `graph_path`, `reasoning`, `instance`

Field property repair smoke:

```bash
curl -sS 'http://127.0.0.1:8772/api/ontology/object%3Aemployee?tenant=default'
```

Observed key fields:

- `source_schema.schema_source`: `live`
- `source_schema.fields`: `18`
- `employees.employeeID`: `data_type=bigint`, `nullable=true`, `primary_key=null`, `key_role=unknown`, `declared_primary_key_hint=true`
- `employees.reportsTo`: `data_type=double`, `nullable=true`, `foreign_key=null`, `key_role=unknown`

```bash
curl -sS 'http://127.0.0.1:8772/api/ontology/link%3Aemployee%3A1%3An%3Aorder?tenant=default'
```

Observed key fields:

- `source_schema.schema_source`: `live`
- join condition: `orders.employeeID = employees.employeeID`
- `employees.employeeID`: `data_type=bigint`, `nullable=true`, `primary_key=null`, `key_role=unknown`, `declared_primary_key_hint=true`
- `orders.employeeID`: `data_type=bigint`, `nullable=true`, `foreign_key=unknown`, `key_role=relationship_reference`

## Browser Smoke

URL:

```text
http://127.0.0.1:8771/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder
```

Rendered root contains:

- `Ontology Catalog`
- `Employee 1:N Order`
- `Raw schema / mapping`
- `orders.employeeID = employees.employeeID`
- `Graph ingestion eligibility`
- `Review`
- `Used by`

Field property browser smoke:

- `object:employee` rendered root contains `employees.employeeID`, `bigint`, `reportsTo`, `unknown`, `declared_primary_key`, `live`.
- `link:employee:1:n:order` rendered root contains `employees.employeeID`, `orders.employeeID`, `bigint`, `relationship_reference`, `live`.

The browser was loaded from the same origin as the API server. `web/review_workbench/api.js` now defaults to `window.location.origin` unless a local override is explicitly configured, so the page uses the live tenant API instead of a hardcoded local mock endpoint.

## Boundary Note

Ontology owns raw source, schema, review/audit, canonical readiness, and used-by impact.

Reasoning only cites ontology objects as compact basis and links to Ontology for governance detail. Workspace should remain a lightweight Case Inbox / cross-Case queue until a concrete multi-Case management requirement exists.

## Validation

Passed:

- `curl` API smoke for `/api/ontology/catalog?tenant=default`
- `curl` API smoke for `/api/ontology/link:employee:1:n:order?tenant=default`
- Chrome headless browser smoke for Ontology rendered root
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py`
- `git diff --check`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- field property API smoke for `object:employee`
- field property API smoke for `link:employee:1:n:order`
- Chrome rendered DOM smoke for Ontology field property table
