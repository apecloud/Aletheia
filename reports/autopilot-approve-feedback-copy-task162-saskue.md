# Autopilot Approve Feedback Copy - task #162

## Scope

Fix the feedback shown around `Approve as finding`.

The user reported that after clicking approve, the UI surfaced:

`Human review gate only · Autopilot auto-promote remains blocked`

That reads like an error and exposes implementation detail.

## Changes

- Success message after approval is now:
  - `Finding approved and added to Registry.`
- Candidate review hint is now:
  - `Requires human approval · Autopilot suggests, people approve`
- If approval fails because the candidate has no evidence chain, the UI maps the technical error to:
  - `Missing evidence chain.`

The old implementation wording is gone from `reasoning.jsx`:

- `Human review gate only`
- `auto-promote`
- `promote remains blocked`

## Checks

```bash
rg -n "Human review gate only|auto-promote|auto promote|promote remains blocked|Finding approved and added to Registry|Missing evidence chain|Requires human approval" web/review_workbench/reasoning.jsx
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

All checks passed.

## Notes

This is a narrow copy/feedback fix. It does not change the approval boundary:

- Autopilot still only suggests candidates.
- A human still approves findings.
- Approval still requires an evidence chain.
- The approved finding still goes into the Finding Registry.
