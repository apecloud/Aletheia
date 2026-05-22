# Reasoning Autopilot UI - task #141

## Scope

Implemented the Reasoning page Autopilot UI on top of the task #140 API contract.

## UI Changes

- Added `Autopilot` tab to the Reasoning page.
- Added Autopilot session list in the left rail.
- Added Start Autopilot controls for objective, budget, and tenant-scoped safety profile.
- Added center workspace for:
  - session summary
  - run trace
  - hypothesis queue
  - draft candidate Finding Inbox
  - candidate review note
- Added right rail safety/budget summary.

## Safety Boundary

The UI only calls the Autopilot API:

- `POST /api/reasoning/autopilot/sessions`
- `GET /api/reasoning/autopilot/sessions`
- `GET /api/reasoning/autopilot/sessions/<session_key>`
- `POST /api/reasoning/autopilot/sessions/<session_key>/candidate-findings`

The UI does not expose any approve/promote/canonical write action for candidate findings. Candidate review buttons are limited to:

- `Needs more evidence`
- `Reject candidate`

The page explicitly shows:

- `write_scope=draft_only`
- `canonical_writes=disabled`
- `auto_approve_findings=false`
- blocked fields `cardCVV, enteredCVV`

## Validation

Local service: `http://127.0.0.1:8772`.

Browser smoke with Playwright/Chrome:

- Opened `/?screen=reasoning&tenant=creditcardfraud`.
- Switched to `Autopilot` tab.
- Started session `Task141 UI smoke: discover valuable draft findings`.
- Confirmed Finding Inbox, safety profile, budget, and no approve/promote buttons.
- Added a smoke candidate through the API and confirmed the UI shows its evidence chain.
- Used the UI review gate to mark the candidate `needs_more_evidence`.
- Confirmed direct `status=promoted` candidate write still returns `400`.

Screenshots:

- `/tmp/task141-autopilot-ui.png`
- `/tmp/task141-autopilot-candidate-review.png`

Checks:

- `node --check web/review_workbench/api.js`
- Babel standalone transform check for `web/review_workbench/reasoning.jsx`
- `git diff --check`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`

