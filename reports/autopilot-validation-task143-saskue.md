# Autopilot Validation - task #143

## Result

PASS for the Autopilot chain across task #140 API, task #141 UI, and task #142 creditcardfraud playbook.

Validated service: `http://127.0.0.1:8772`

Validated tenant: `creditcardfraud`

Validated session:

`autopilot:creditcardfraud:task142-playbook-smoke`

Reasoning URL:

`http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud`

## API Payload

`GET /api/reasoning/autopilot/sessions/autopilot%3Acreditcardfraud%3Atask142-playbook-smoke?tenant=creditcardfraud`

Observed:

- Session status: `draft`
- Safety profile:
  - `write_scope=draft_only`
  - `canonical_writes=disabled`
  - `auto_approve_findings=false`
  - `masked_fields_only=true`
  - `safe_views_only=true`
  - `blocked_fields=["card_verification_code_fields"]`
- Hypotheses: 6 total
  - 5 completed
  - 1 pruned
- Pruned hypothesis:
  - `Expiration-key mismatch does not clear the value threshold`
  - Reason: expected fraud-rate lift is below candidate threshold and no strong operational action follows from the field alone.
- Candidate findings: 5 total
  - All produced by the playbook as draft candidates before review.
  - After review-gate smoke, one candidate was marked `needs_more_evidence`; the other four remained `draft`.

Candidate finding titles:

- `Card-not-present transactions carry elevated fraud risk`
- `Verification mismatch is a compact fraud-risk signal`
- `Missing POS entry mode should be reviewed as a weak-control pattern`
- `Merchant category concentration reveals high-yield fraud review segments`
- `Same-day duplicate transaction clusters need multi-swipe review`

Every candidate includes an `evidence_chain` pointing to `credit_card_transactions_safe`.

## Sensitive Field Boundary

Checked API payload and rendered DOM for raw sensitive fields:

- `cardCVV`: absent
- `enteredCVV`: absent

The page and payload use the grouped boundary label `card_verification_code_fields` and safe source `credit_card_transactions_safe`.

## Review Gate

Promote/canonical bypass was tested directly:

`POST /api/reasoning/autopilot/sessions/<session>/candidate-findings?tenant=creditcardfraud` with `status=promoted`

Result:

- HTTP `400`
- Error: `candidate findings cannot be auto-promoted by the Autopilot API`

Candidate review smoke:

- Marked one candidate as `needs_more_evidence` through the candidate API path used by the UI.
- The updated payload retained the candidate in Autopilot review state and added reviewer evidence-limit text.

## Browser / DOM Smoke

Headless Chrome opened:

`http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud`

Rendered DOM included:

- `Autopilot`
- `Draft candidate findings`
- `EVIDENCE CHAIN`
- `NEEDS_MORE_EVIDENCE`
- `canonical_writes=disabled`
- `draft_only`
- `card_verification_code_fields`
- `credit_card_transactions_safe`
- Playbook candidate titles and pruned hypothesis reason

Rendered DOM did not include:

- `cardCVV`
- `enteredCVV`
- `Approve candidate`
- `Promote candidate`

Screenshots:

- `/tmp/task143-autopilot.png`
- `/tmp/task143-autopilot-reviewed.png`

DOM captures:

- `/tmp/task143_autopilot_dom.txt`
- `/tmp/task143_autopilot_reviewed_dom.txt`

## Verification Commands

Passed:

```bash
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
git diff --check
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Additional source checks:

- `web/review_workbench/reasoning.jsx` contains `AutopilotWorkspace`
- `Run creditcardfraud playbook` is present
- Review actions are limited to `Needs more evidence` and `Reject candidate`

## Environment Note

The local 8772 service was running against a Homebrew PostgreSQL metadata instance restored for Autopilot smoke. During validation, default Ontology live endpoints returned an environment error because that local metadata DB did not contain `aletheia_ontology_artifacts`. I did not count those endpoints as #143 coverage.

The #143-specific canonical boundary was validated through the Autopilot API itself: candidate promotion is rejected with HTTP 400, and no candidate path writes canonical or auto-approves findings.
