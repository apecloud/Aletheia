# Ontology Proposed Filter - task #138

## Scope
Fix the Ontology page state where `Proposed(3)` could be visible while clicking it showed `No ontology artifacts match this filter`.

## Root Cause
Two count/filter boundaries were inconsistent:
- The left status chips counted all artifact types in the tenant, while the list filters within the currently selected type tab.
- Backend artifacts may still use raw status `draft`, while the UI presents that review state as `proposed`.

For `creditcardfraud`, the remaining 3 review items are LinkType artifacts. Showing `Proposed(3)` while the ObjectType tab is active was misleading because there were no proposed ObjectTypes in that tab.

## Changes
- Ontology status chips now count only artifacts in the active type tab.
- The right-side catalog health distribution still uses whole-catalog counts.
- Backend `/api/artifacts?status=proposed` now includes raw `draft` artifacts as well, matching UI terminology.

## Validation
Service restarted fresh on `http://127.0.0.1:8772`.

API smoke:
- `/api/artifacts?tenant=creditcardfraud&status=proposed` returns the 3 draft LinkType artifacts.
- `/api/artifacts?tenant=creditcardfraud&status=draft` also returns the same 3 artifacts.

Chrome smoke:
- Object Types tab shows `Proposed 0` and does not show the empty-filter message.
- Link Types tab shows `Proposed 3` and lists:
  - `Account 1 N Credit Card Transaction`
  - `Card 1 N Credit Card Transaction`
  - `Merchant 1 N Credit Card Transaction`

Checks:
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`
