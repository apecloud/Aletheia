# Ontology Review Actions - task #137

## Scope
Restore visible review controls on the Ontology page so proposed/draft ontology artifacts can be reviewed in the page that owns ontology governance.

## Changes
- Added an `Ontology review gate` block to the selected artifact header.
- Review actions now appear directly on Ontology detail:
  - `Approve artifact`
  - `Needs changes`
  - `Reject`
  - `Comment`
- `Approve` and `Reject` require a rationale before calling the tenant-scoped review API.
- `Approved` and `Rejected` artifacts no longer show active decision buttons; they only allow comments, avoiding repeated or misleading approval actions.
- Successful actions refresh the artifact list/detail and switch to `Review history` so the user can see the recorded decision.

## Validation
Service restarted fresh on `http://127.0.0.1:8772`.

Chrome smoke URL:
`http://127.0.0.1:8772/?screen=ontology&tenant=creditcardfraud&artifact=object%3Acredit_card_transaction`

Observed:
- `Credit Card Fraud Dataset · creditcardfraud` loaded.
- `Credit Card Transaction` is selected with status `PROPOSED`.
- `Ontology review gate` is visible.
- Buttons are visible: `Approve artifact`, `Needs changes`, `Reject`, `Comment`.
- Smoke clicked `Comment` with rationale `task137 smoke comment`.
- `Review history` refreshed from 0 to 1 and shows the comment event.
- Raw `cardCVV / enteredCVV` did not appear in the Ontology DOM smoke.

Checks:
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `git diff --check`

## Boundary
This restores ontology artifact review inside Ontology only. Reasoning still reviews findings, and Workspace remains a lightweight Case Inbox.
