# Instance Explorer MVP Verification

## Scope

Implemented the Employee -> Orders Instance Explorer MVP only. This is not a global graph browser.

Completed task coverage:

- #22 Instance API MVP: approved ontology driven type/search/detail/neighborhood APIs.
- #23 Instance Explorer UI MVP: Employee search, 1-hop Orders neighborhood, right-side detail/provenance.
- #24 Edge Detail / Provenance: Employee-Order edges explain `orders.employeeID`, join condition, and ontology link.
- #25 Review Workbench Deep Link: artifact -> instance explorer and instance node/edge -> artifact links.

## API Endpoints

- `GET /api/instances/types`
- `GET /api/instances/search?type=Employee&q=Margaret`
- `GET /api/instances/Employee/4`
- `GET /api/instances/Employee/4/neighborhood?depth=1&limit=200`
- `GET /api/instances/edge?source=Employee%3A4&target=Order%3A10250`

## Gate Negative Path

Temporarily set `link:employee:1:n:order` to `rejected` and called the default neighborhood API.

Result:

- `approved=false`
- missing approved artifact: `link:employee:1:n:order`
- nodes returned: 0
- edges returned: 0

This proves default instance browsing does not bypass draft/rejected ontology artifacts.

## Approved Positive Path

Restored `link:employee:1:n:order` to `approved` and called the same API.

Result:

- Employee search returns `Employee:4 / Margaret Peacock`.
- `GET /api/instances/Employee/4` returns source row from `employees`, PK `employeeID=4`.
- 1-hop neighborhood returns:
  - nodes: 157
  - edges: 156
  - handled orders: 156
  - returned orders: 156
  - order ID checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`

This matches the SQL baseline for `orders.employeeID = 4`.

## Edge Provenance

Sample edge: `Employee:4->Order:10250`.

- source field: `orders.employeeID`
- target field: `employees.employeeID`
- join condition: `orders.employeeID = employees.employeeID`
- ontology link: `link:employee:1:n:order`
- source instance: `Employee:4`
- target instance: `Order:10250`

## UI Verification

- URL: <http://127.0.0.1:8765/instances.html?type=Employee&id=4>
- Screenshot: `reports/screenshots/instance-explorer-employee-orders.png`
- Review Workbench artifact deep links:
  - `object:employee` -> Employee instances.
  - `link:employee:1:n:order` -> Employee #4 -> Orders example.
- Instance detail deep links:
  - Employee/Order nodes link back to their Object artifacts.
  - Employee-Order edge links back to `link:employee:1:n:order`.

## Command Verification

- `.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py query_metadata.py evals tests`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `node --check web/review_workbench/app.js`
- `node --check web/review_workbench/instance_app.js`
- `git diff --check`
