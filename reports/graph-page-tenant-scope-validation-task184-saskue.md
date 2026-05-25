# Graph Page Tenant Scope Validation - task #184

## Result

PASS.

The Graph page no longer restores or displays the default `Employee` center when the current tenant is `maritime-risk`.

## Scope

Validated current commit:

```text
ded779d Fix graph tenant scope and proposed elements
```

Validated local service:

```text
http://127.0.0.1:8782
```

## Evidence

### Direct maritime-risk URL restore

URL tested:

```text
http://127.0.0.1:8782/?screen=graph&tenant=maritime-risk&type=Employee&id=4
```

Browser result:

- Final URL removed the invalid `type=Employee&id=4` scope:
  - `http://127.0.0.1:8782/?screen=graph&tenant=maritime-risk&lang=en&depth=1&limit=200`
- Body did not contain `Employee`.
- Center type selector showed `No tenant types`.
- Center node selector showed `No center nodes` and was disabled.
- Current scope showed `No tenant center`.

### Default tenant still works

URL tested:

```text
http://127.0.0.1:8782/?screen=graph&tenant=default
```

Browser result:

- Center type selector included `Employee / Order / Customer / Product / Category`.
- Center node selector loaded real Northwind employee candidates.
- Graph loaded a valid default center, e.g. `Employee:1`.

### Tenant switch clears stale scope

Browser flow:

```text
default graph page with Employee scope -> tenant selector changed to maritime-risk
```

Result:

- `Employee` disappeared from the page after the switch.
- URL was rewritten to `tenant=maritime-risk` without stale `type/id`.
- Center state became `No tenant center`.

### API guard

`maritime-risk` has no tenant-valid Employee center:

```json
{
  "instances": [],
  "approved": false,
  "reason": "object:employee is not approved for tenant maritime-risk"
}
```

`maritime-risk + Employee:4` graph context is blocked/empty:

```json
{
  "approved": false,
  "missing_approved_artifacts": ["object:employee"],
  "center": null,
  "nodes": [],
  "edges": []
}
```

Default tenant still returns Northwind graph center types:

```text
Employee, Order, Customer, Product, Category
```

## Verification Commands

```bash
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/screens.jsx --bundle --format=esm --outfile=/tmp/task184-screens.js
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py agents/iterative_graph_enrichment_agent.py
git diff --check
```

Browser smoke used Playwright with local Google Chrome because the repo-local Node/Python Playwright packages were not installed.

