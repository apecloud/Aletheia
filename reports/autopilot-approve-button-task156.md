# Task 156 - Autopilot Approve Button Feedback

## Issue

On the `creditcardfraud` Reasoning Autopilot page, clicking `Approve as finding` on the candidate:

`Card-not-present transactions carry elevated fraud risk`

appeared to do nothing when the review note was empty.

## Fix

- Kept the required human review note for approve/reject/needs-more-evidence decisions.
- Added per-candidate inline feedback when approval is attempted without a note.
- Added an inline review-note textarea for the selected candidate.
- Hide decision buttons after a candidate is reviewed and show `Review recorded`.

## Verification

- Created isolated smoke session: `autopilot:creditcardfraud:task156-smoke-*`.
- Browser smoke:
  - First click on `Approve as finding` shows inline note-required feedback.
  - Filling the inline note and clicking again creates the approved finding.
  - DOM does not contain raw `cardCVV` or `enteredCVV`.
- Screenshots:
  - `/tmp/task156-review-note-required.png`
  - `/tmp/task156-approved.png`

## Commands

```bash
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
