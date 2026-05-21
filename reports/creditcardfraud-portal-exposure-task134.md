# Creditcardfraud Portal Exposure - task #134

## Scope
Expose the `creditcardfraud` tenant imported in task #133 through the Portal UI so reviewers can open its ontology artifacts and reasoning result without relying on CLI reports.

## Changes
- `TenantRegistry` now merges active tenants from `aletheia_tenants`, so metadata-created tenants such as `creditcardfraud` appear in `/api/tenants` and the Portal tenant switcher.
- Portal URL bootstrap reads `?tenant=...`, enabling direct links such as `/?screen=ontology&tenant=creditcardfraud` and `/?screen=reasoning&tenant=creditcardfraud&task=...`.
- Ontology catalog auto-switches from `approved` to `all` when a tenant only has draft/proposed artifacts, so the 7 imported draft artifacts are visible.
- Reasoning deep link reads `?task=...` and opens `reasoning:creditcardfraud:dataset-risk-profile:v1` directly.
- Reasoning current answer now renders a fraud risk summary panel with fraud rate, card-not-present/CVV/POS-entry risk patterns, high-risk merchant categories, high-risk examples, and evidence/privacy boundary.

## Validation
Service: `http://127.0.0.1:8772` after killing the stale process and starting a fresh `screen` session.

API smoke:
- `/api/tenants` includes `creditcardfraud`.
- `/api/artifacts?tenant=creditcardfraud` returns 7 draft artifacts: Credit Card Transaction, Account, Card, Merchant, and three 1:N links to Transaction.
- `/api/reasoning/tasks/reasoning%3Acreditcardfraud%3Adataset-risk-profile%3Av1?tenant=creditcardfraud` returns one finding.
- Reasoning payload does not contain raw `cardCVV` or `enteredCVV` fields.

Chrome smoke with a fresh headless Chrome process:
- Ontology URL: `http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=object%3Acredit_card_transaction`
  - Shows tenant `Credit Card Fraud Dataset · creditcardfraud`.
  - Shows `OBJECT TYPES 4`, `LINK TYPES 3`, `PROPOSED 7`, `ALL 7`.
  - Shows `Source & Schema` and field properties for Credit Card Transaction.
  - Does not render raw `cardCVV` or `enteredCVV` field names in the DOM smoke.
- Reasoning URL: `http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud&task=reasoning%3Acreditcardfraud%3Adataset-risk-profile%3Av1`
  - Shows fraud rate `1.58%`, fraud transactions `12,417`, fraud average amount `$225.22`.
  - Shows card-not-present `2.07%`, `cvvMatch=false` `2.89%`, POS entry missing `6.64%`.
  - Shows high-risk examples `tx 571924`, `tx 149886`, `tx 783498`.
  - Shows evidence boundary: deterministic SQL aggregates over the safe transaction view; raw CVV values are not required for this reasoning surface.

Command checks:
- `node --check web/review_workbench/api.js` passed. (`node --check` does not accept `.jsx` files in Node 22; JSX syntax was validated by the Chrome/Babel smoke.)
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py` passed.
- `git diff --check` passed.

## Isolation note
Task #133 could not create a physically separate MySQL database because the current MySQL user lacks `CREATE DATABASE` permission. The dataset is isolated as an independent table/safe view in the existing source DB, while Aletheia metadata is isolated by tenant `creditcardfraud`. UI reasoning uses safe derived signals such as `cvvMatch` and does not need raw CVV values.
