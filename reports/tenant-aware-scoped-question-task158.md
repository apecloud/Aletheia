# Tenant-aware Scoped Question Validation - task #158

## Scope

Make the Ask a scoped question surface follow the active tenant for:

- Question defaults and placeholders
- Center node type selection
- Instance candidates
- Suggested question templates
- Submission validation

## Changes

- Reasoning page now bootstraps center-node types from `/api/instances/types?tenant=<tenant>`.
- Tenant switch clears stale scope when the selected node type is not valid for the new tenant.
- Suggested questions use tenant-aware templates:
  - `default`: Employee/Order-style operational questions.
  - `creditcardfraud`: Transaction/Account/Card/Merchant fraud-risk questions.
- Submit path blocks center nodes whose object type does not belong to the current tenant.
- Backend `/api/reasoning/questions` now accepts the top-level `center_node/depth/limit` payload sent by the UI and validates tenant object membership before creating a task.

## Verification

- `creditcardfraud` API types: `CreditCardTransaction`, `Account`, `Card`, `Merchant`.
- `creditcardfraud` search for `CreditCardTransaction` returns real `credit_card_transactions_safe` rows.
- `creditcardfraud` search for `Employee` returns rejected/empty.
- `creditcardfraud` scoped question with `CreditCardTransaction:2` creates a task scoped to `CreditCardTransaction:2`.
- `creditcardfraud` scoped question with `Employee:1` returns HTTP 400.
- `default` scoped question with `Employee:1` still creates an Employee/Order-scoped task.
- Chrome DOM smoke:
  - `creditcardfraud` shows fraud tenant/type vocabulary and does not show `Employee #4` or an Employee option.
  - `default` shows Northwind Employee/Order vocabulary and does not show creditcardfraud transaction rows.

## Commands

```bash
python3 -m py_compile review_workbench.py agents/tenant_registry.py
node --check web/review_workbench/api.js
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

The global `python3 -m unittest tests/test_ontology_eval.py` path failed in this environment because system Python cannot import the local `tests` package; `.venv/bin/python` passed.
