# Live Source Schema Demo Recovery - task #147

## Recovery

Restored the MySQL source database container used by the 8772 demo:

```bash
docker compose -f docker/docker-compose.yml up -d aletheia-mysql
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8772 --ensure-schema
```

Container status:

```text
Container aletheia-mysql Running
```

MySQL source tables available in `aletheia_test_data`:

```text
categories
credit_card_transactions
credit_card_transactions_safe
customers
employees
order_details
orders
products
```

## Code Fix

`ReviewRepository.source_table_schema()` no longer silently downgrades source
DB failures into a generic fallback schema. When the source database is
unreachable, the API now returns:

```json
{
  "schema_source": "degraded",
  "degraded": true,
  "degraded_reason": "source database connection failed",
  "connection_error": "<sanitized error>"
}
```

The connection error is sanitized so known local database passwords are not
returned.

Also added live schema mappings for the creditcardfraud safe view:

- `object:credit_card_transaction`
- `object:account`
- `object:card`
- `object:merchant`
- `link:account:1:n:credit_card_transaction`
- `link:card:1:n:credit_card_transaction`
- `link:merchant:1:n:credit_card_transaction`

These mappings read from `credit_card_transactions_safe`, not the raw table.

## 8772 Smoke URLs

- `http://127.0.0.1:8772/?screen=ontology&tenant=default&artifact=object%3Aemployee`
- `http://127.0.0.1:8772/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder`
- `http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=object%3Acredit_card_transaction`
- `http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=link%3Acard%3A1%3An%3Acredit_card_transaction`

## API Evidence

Smoke summary:

```json
{
  "employee_employeeID_type": "bigint",
  "employee_fields": 18,
  "employee_order_field_properties": 2,
  "employee_order_schema_source": "live",
  "employee_schema_source": "live",
  "fraud_card_fields": 37,
  "fraud_card_schema_source": "live",
  "fraud_link_schema_source": "live",
  "fraud_tx_fields": 37,
  "fraud_tx_schema_source": "live",
  "raw_sensitive_leak": false
}
```

Degraded-path smoke with an intentionally bad MySQL port:

```text
{'table': 'employees', 'schema_source': 'degraded', 'degraded': True, 'degraded_reason': 'source database connection failed'}
has_connection_error True
leaks_password False
```

## Verification

Passed:

```bash
.venv/bin/python -m py_compile review_workbench.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
git diff --check
```
