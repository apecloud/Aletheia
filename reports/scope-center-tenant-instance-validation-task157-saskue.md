# Scope Center Tenant Instance Validation - task #157

## Result

PASS on the current worktree, using isolated validation service <http://127.0.0.1:8778>.

The scoped reasoning center picker and backing instance APIs now use the current tenant's approved object artifacts and live source instances instead of falling back to the Northwind Employee demo list.

## Fix Scope

- Backend `InstanceRepository` now exposes tenant-relevant object types for `creditcardfraud`:
  - `CreditCardTransaction`
  - `Account`
  - `Card`
  - `Merchant`
- These object types search real rows from `credit_card_transactions_safe`.
- `Employee` remains blocked in `creditcardfraud`; it does not silently fall back to `default`.
- Reasoning question creation now derives `allowed_node_types`, `allowed_link_keys`, and `graph_url` from the selected tenant graph scope instead of hardcoded `Employee -> Order`.
- Frontend picker state resets when the tenant/type list changes so stale `Employee:4` selection does not survive a switch to `creditcardfraud`.

## API Evidence

Machine-readable report: `reports/scope-center-tenant-instance-validation-task157-saskue.json`

Checks passed: 9/9.

Key API observations:

- `GET /api/instances/types?tenant=creditcardfraud` returns:
  - `CreditCardTransaction`
  - `Account`
  - `Card`
  - `Merchant`
- `GET /api/instances/search?tenant=creditcardfraud&type=CreditCardTransaction&limit=3` returns real IDs such as `CreditCardTransaction:1`.
- `GET /api/instances/search?tenant=creditcardfraud&type=Account&limit=3` returns real account IDs from `credit_card_transactions_safe`.
- `GET /api/instances/search?tenant=creditcardfraud&type=Employee&limit=3` returns `approved=false`, empty instances, and no fallback data.
- `POST /api/reasoning/questions?tenant=creditcardfraud` with `CreditCardTransaction:1` succeeds and stores:
  - `center_node=CreditCardTransaction:1`
  - `allowed_node_types=["CreditCardTransaction"]`
  - `allowed_link_keys=[]`
  - `graph_url=/graph.html?tenant=creditcardfraud&type=CreditCardTransaction&id=1...`
- The same endpoint with `Employee:4` returns HTTP 400.
- `default` tenant still returns Northwind `Employee / Order / Customer / Product / Category`.

## Browser Smoke

URL:

<http://127.0.0.1:8778/?screen=reasoning&tenant=creditcardfraud>

Captured artifacts:

- Screenshot: `/tmp/task157-creditcardfraud-ask-scope-picker.png`
- DOM/HTML: `/tmp/task157_creditcardfraud_ask_scope_picker_dom.txt`

Visible Ask page checks:

- Object type list contains `CreditCardTransaction / Account / Card / Merchant`.
- Suggested questions are fraud-tenant specific, for example `Explain fraud risk signals for Transaction #1`.
- Visible text does not contain `Employee:4`, `Employee #4`, or Northwind employee suggested-question text.

## Boundary Notes

- This validation is scoped to task #157's tenant-backed center object list and submit boundary.
- task #158 is separately owned by @Itachi for broader Ask page question/suggestion UX polish; current worktree already shows the intended fraud-specific question/suggestion behavior, but #158 should be the final owner for that UX item.
- No commit/push was performed because the repository already contains unrelated dirty files and overlapping in-progress frontend edits.

## Verification Commands

```bash
.venv/bin/python review_workbench.py --port 8778 --ensure-schema
python3 <task157 API validation script>
python3 <task157 browser smoke>
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
node --check web/review_workbench/api.js
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
