# Live Source Schema Validation - task #148

## Result

PASS for task #147 live source schema recovery.

Validated service: `http://127.0.0.1:8772`

Current code baseline:

`bd64569 Recover live source schema demo`

## Source Service State

Docker/MySQL is running:

```text
aletheia-mysql Up ... 0.0.0.0:3306->3306/tcp
```

Workbench health:

`GET /api/tenants`: HTTP 200

## Live Schema API Checks

Validated four required Ontology details:

| Tenant | Artifact | HTTP | schema_source | Key evidence |
|---|---|---:|---|---|
| `default` | `object:employee` | 200 | `live` | 18 fields, `employees.employeeID` is `bigint`, nullable `true` |
| `default` | `link:employee:1:n:order` | 200 | `live` | `employees.employeeID` + `orders.employeeID`, both `bigint`; target role `relationship_reference` |
| `creditcardfraud` | `object:credit_card_transaction` | 200 | `live` | 37 fields from `credit_card_transactions_safe`; `transaction_id`, `cvvMatch`, `cardLast4Digits` visible |
| `creditcardfraud` | `link:card:1:n:credit_card_transaction` | 200 | `live` | field properties from `credit_card_transactions_safe.cardLast4Digits` |

The fraud artifacts use `credit_card_transactions_safe` as the source surface. API payloads did not contain raw `cardCVV` or `enteredCVV`.

API captures:

- `/tmp/task148_employee.json`
- `/tmp/task148_employee_order.json`
- `/tmp/task148_fraud_tx.json`
- `/tmp/task148_fraud_link.json`

## Degraded Path

Started a temporary validation service on port `8777` with a bad MySQL port:

```bash
.venv/bin/python review_workbench.py \
  --host 127.0.0.1 \
  --port 8777 \
  --ensure-schema \
  --source-db-url 'mysql+pymysql://root:super-secret-password@127.0.0.1:3999/aletheia_test_data'
```

Then requested:

`GET /api/ontology/object%3Aemployee?tenant=default`

Observed:

- HTTP 200
- `schema_source=degraded`
- `degraded=true`
- `degraded_reason=source database connection failed`
- `connection_error` is present and sanitized
- No password leak:
  - `super-secret-password`: absent
  - `aletheia_pg_password`: absent
  - `root:`: absent
  - `3999`: absent

The temporary 8777 process was stopped after validation.

Degraded payload capture:

- `/tmp/task148_bad_employee.json`

## Browser / DOM Smoke

Headless Chrome validated the four required 8772 pages:

- `http://127.0.0.1:8772/?screen=ontology&tenant=default&artifact=object%3Aemployee`
- `http://127.0.0.1:8772/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder`
- `http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=object%3Acredit_card_transaction`
- `http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=link%3Acard%3A1%3An%3Acredit_card_transaction`

DOM checks:

- All four pages render `SOURCE & SCHEMA`, `SOURCE SCHEMA`, and `live`.
- Default link page renders both `employees.employeeID` and `orders.employeeID`.
- Fraud pages render `credit_card_transactions_safe` and `cardLast4Digits`.
- Fraud pages do not render raw `cardCVV` or `enteredCVV`.
- Live pages do not render `degraded`.

Screenshots:

- `/tmp/task148-employee-live.png`
- `/tmp/task148-employee-order-live.png`
- `/tmp/task148-fraud-tx-live.png`
- `/tmp/task148-fraud-link-live.png`

DOM captures:

- `/tmp/task148_employee_dom.txt`
- `/tmp/task148_employee_order_dom.txt`
- `/tmp/task148_fraud_tx_dom.txt`
- `/tmp/task148_fraud_link_dom.txt`

## Verification Commands

Passed:

```bash
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py scripts/bootstrap_demo_environment.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
git diff --check
```

## Residual Notes

The repository working tree still contains unrelated pre-existing local changes and untracked reports. I did not revert or mix them into this validation.
