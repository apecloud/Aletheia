# Ontology / Reasoning / Workspace Boundary Validation - task #130

Result: PASS

Scope:
- Validate Ontology owns live source/schema/review/canonical/used-by governance detail.
- Validate Ontology field properties come from live schema introspection, not only static column names.
- Validate Workspace is a lightweight Case Inbox and no longer duplicates ontology artifact review/governance operations.
- Validate Reasoning keeps compact ontology basis and deep-links to Ontology for full governance detail.

Environment:
- Service: `http://127.0.0.1:8772`
- Tenant: `default`
- Primary ontology sample: `link:employee:1:n:order`
- Field-property sample: `object:employee`

API evidence:
- `/api/ontology/catalog?tenant=default`: tenant `default / northwind / aletheia`, artifact count `10`, includes `link:employee:1:n:order`.
- `/api/ontology/object%3Aemployee?tenant=default`: `schema_source=live`, `fields=18`.
- `employees.employeeID`: `data_type=bigint`, `nullable=true`, `primary_key=null`, `key_role=unknown`, `declared_primary_key_hint=true`, `schema_source=live`.
- `employees.reportsTo`: `data_type=double`, `nullable=true`, `primary_key=null`, `foreign_key=null`, `key_role=unknown`, `schema_source=live`.
- `/api/ontology/link%3Aemployee%3A1%3An%3Aorder?tenant=default`: approved `v6`, join mapping `orders.employeeID = employees.employeeID`, review events `5`, used-by kinds `graph_path / reasoning / instance`.
- Relationship field properties exist for both sides: `employees.employeeID` is `bigint nullable=true`; `orders.employeeID` is `bigint nullable=true`, `foreign_key=unknown`, `key_role=relationship_reference`.

Browser evidence:
- Ontology `object:employee` page renders field properties including `employees.employeeID`, `bigint`, `reportsTo`, `unknown`, `declared_primary_key`, and `live`.
- Ontology `link:employee:1:n:order` page renders `Raw schema / mapping`, join mapping, both field-property rows, `relationship_reference`, `Graph ingestion eligibility`, review/canonical/used-by detail.
- Workspace `/?screen=workbench&tenant=default` renders Case Inbox with case status, owner, blocker, summary, next action, `OPEN REASONING`, and `OPEN ONTOLOGY BASIS`.
- Workspace negative boundary passed: main view does not render `Evidence chain`, `Audit Trail`, `Source refs`, `Ingestion eligible`, `Approve`, `Reject`, `Needs changes`, `Payload (JSON)`, or `Decision history`.
- Reasoning `/?screen=reasoning&tenant=default` keeps compact `ONTOLOGY BASIS` and `Compact basis only`; it does not render full ontology governance blocks in-place.
- Reasoning `VIEW IN ONTOLOGY` opens `/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder` and shows the full Ontology detail.

Screenshots:
- `/tmp/task130-ontologyEmployee-8772.png`
- `/tmp/task130-ontologyLink-8772.png`
- `/tmp/task130-workbench-8772.png`
- `/tmp/task130-reasoning-8772.png`

JSON report:
- `/Users/slc/code/Aletheia/reports/ontology-reasoning-workspace-boundary-task130-saskue.json`

Validation commands run:
- API smoke for catalog/detail/object/link endpoints via `curl` + Python JSON assertions.
- Browser smoke via Python Playwright using local Chrome.
- `node --check web/review_workbench/api.js && git diff --check`
- `.venv/bin/python -m py_compile review_workbench.py`
- `.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`

Notes:
- The earlier task #130 FAIL was valid at that time because Workspace still rendered artifact review/governance. It is superseded by the #131 cleanup and #128 field-property repair validated here.
- PK/FK/comment are explicitly represented as `null` or `unknown` where the live Northwind import does not expose those constraints/comments. This is acceptable and visible instead of silently omitted.
