# Tenant-aware Scoped Question Validation - task #160

## Scope

Independent validation for task #158, focused on the two product gaps called out by Jobs:

- Browser switch from `default` to `creditcardfraud` must clear old Employee scope.
- Suggested questions must bind to current tenant real instances, not `Type:*` or cross-tenant pseudo nodes.

Cindy also asked to verify switching back to `default` restores Northwind suggestions and does not keep `creditcardfraud` state.

## Result

PASS.

## Browser Smoke

Service:

- `http://127.0.0.1:8780`

Flow:

1. Open Reasoning page with `tenant=default`.
2. Confirm Ask scoped question uses Northwind types.
3. Change tenant selector to `creditcardfraud` in the same browser session.
4. Confirm old Employee scope is cleared and fraud tenant scope is loaded.
5. Change tenant selector back to `default`.
6. Confirm Northwind scope returns and fraud terms do not leak.

Evidence files:

- `/tmp/task160_browser_switch.json`
- `/tmp/task160-fraud-after-switch.png`
- `/tmp/task160-default-after-switch-back.png`
- `/tmp/task160_fraud_after_switch_dom.txt`
- `/tmp/task160_default_after_switch_back_dom.txt`

Observed browser state:

- Initial `default`:
  - tenant selector: `default`
  - center selector: `Employee`
  - center type list: `Employee / Order / Customer / Product / Category`
  - no `CreditCardTransaction`
  - no `Type:*`
- After switching to `creditcardfraud`:
  - URL: `?screen=reasoning&tenant=creditcardfraud`
  - tenant selector: `creditcardfraud`
  - center selector: `CreditCardTransaction`
  - center type list: `CreditCardTransaction / Account / Card / Merchant`
  - question value: `Explain fraud risk signals for Transaction #3`
  - suggested questions all reference `Transaction #3`
  - no `Employee:`, `Employee #`, `Which Employees`, `Order:`, or `Type:*`
- After switching back to `default`:
  - URL: `?screen=reasoning&tenant=default`
  - tenant selector: `default`
  - center selector: `Employee`
  - no `CreditCardTransaction`
  - no `Transaction #`
  - no `Type:*`

Machine check:

```json
{
  "fraud_center_creditcard": true,
  "fraud_no_employee_scope": true,
  "fraud_no_type_star": true,
  "fraud_has_real_tx_suggestions": true,
  "default_restored_employee": true,
  "default_no_fraud_leak": true,
  "default_no_type_star": true
}
```

## API Smoke

Positive tenant type checks:

- `/api/instances/types?tenant=default` returns `Employee / Order / Customer / Product / Category`.
- `/api/instances/types?tenant=creditcardfraud` returns `CreditCardTransaction / Account / Card / Merchant`.

Negative tenant boundary:

- `/api/instances/search?tenant=creditcardfraud&type=Employee&q=&limit=5` returns `approved=false`, empty instances, reason `object:employee is not approved for tenant creditcardfraud`.
- `POST /api/reasoning/questions?tenant=creditcardfraud` with `center_node=Employee:1` returns HTTP 400: `center_node Employee:1 is not an approved object type for tenant creditcardfraud`.

Positive scoped question checks:

- `creditcardfraud + CreditCardTransaction:3` creates a scoped task with:
  - `center_node=CreditCardTransaction:3`
  - `allowed_node_types=["CreditCardTransaction"]`
  - `allowed_link_keys=[]`
  - graph URL under `tenant=creditcardfraud&type=CreditCardTransaction&id=3`
- `default + Employee:1` creates a scoped task with:
  - `center_node=Employee:1`
  - `allowed_node_types=["Employee","Order"]`
  - `allowed_link_keys=["link:employee:1:n:order"]`
  - graph URL under `tenant=default&type=Employee&id=1`

## Verdict

task #158 passes the requested independent validation. Ask scoped question is tenant-aware for:

- question defaults
- center node type list
- node candidates
- suggested questions
- tenant switch/reset
- backend submission validation

No commit or push was performed by Saskue. This was a validation-only task.
