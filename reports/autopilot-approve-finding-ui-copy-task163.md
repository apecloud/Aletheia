# Autopilot Approve Finding UI Copy - task #163

## Problem

The user clicked `Approve as finding` and saw:

`Human review gate only · Autopilot auto-promote remains blocked`

This reads like an error even when approval is routed through the human review endpoint.

## Fix

- Keep `Approve as finding` as a human review action.
- Do not require a review note for approval.
- Keep notes required for `Reject candidate` and `Needs more evidence`.
- Replace the engineering status copy with product wording:

`Requires human approval · Autopilot suggests, people approve`

- Update success feedback to:

`Added to Finding Registry. Opened Registry panel.`

- After approval, switch from Autopilot to the main Reasoning panel where `Approved Finding Registry` is visible, and highlight the newly approved finding with a `newly added` label.

## Verification

- Frontend no longer contains `Human review gate only · Autopilot auto-promote remains blocked`.
- Approval path still calls `/api/reasoning/autopilot/candidate-findings/<key>/approve`.
- Empty-note approval returned HTTP 200 and produced an approved finding.
- Chrome DOM shows `Approved Finding Registry` and the approved finding title, without `auto-promote` / `blocked` copy.
- `node --check web/review_workbench/api.js` passed.
- `python3 -m py_compile review_workbench.py agents/tenant_registry.py` passed.
- `.venv/bin/python -m unittest tests/test_ontology_eval.py` passed.
- `git diff --check` passed.
