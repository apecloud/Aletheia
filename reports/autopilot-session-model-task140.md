# Autopilot Session Model/API - task #140

## Scope

Implemented a backend-only Reasoning Autopilot session layer for draft discovery workflows. This does not build UI and does not execute a domain playbook; it gives the Reasoning page a controlled API surface for a future UI and creditcardfraud playbook.

## Data Model

New tenant-scoped metadata tables are created lazily in the tenant metadata database:

- `aletheia_autopilot_sessions`
- `aletheia_autopilot_hypotheses`
- `aletheia_autopilot_candidate_findings`

The model keeps the boundary explicit:

- Session owns objective, scope, budget, safety profile, status, and creator.
- Hypothesis queue owns title, rationale, queue status, priority, evidence plan, linked reasoning task keys, and prune reason.
- Candidate findings own title, conclusion, value/confidence/novelty/impact scores, evidence chain, evidence limits, suggested action, and draft review status.

## API

- `GET /api/reasoning/autopilot/sessions?tenant=<tenant>&status=<status>&limit=<n>`
- `GET /api/reasoning/autopilot/sessions/<session_key>?tenant=<tenant>`
- `POST /api/reasoning/autopilot/sessions?tenant=<tenant>`
- `POST /api/reasoning/autopilot/sessions/<session_key>/hypotheses?tenant=<tenant>`
- `POST /api/reasoning/autopilot/sessions/<session_key>/candidate-findings?tenant=<tenant>`

## Safety Boundary

The API normalizes safety settings server-side:

- `allow_sensitive_fields=false`
- `masked_fields_only=true`
- `write_scope=draft_only`
- `canonical_writes=disabled`
- `auto_approve_findings=false`
- default blocked fields include `cardCVV` and `enteredCVV`

Candidate findings can only be `draft`, `needs_more_evidence`, or `rejected` through this API. A request with `status=promoted` returns `400` and does not create a promoted candidate.

## Validation

Validated on fresh local service `http://127.0.0.1:8772` with tenant `creditcardfraud`.

- Created session `autopilot:creditcardfraud:task140-smoke`.
- Confirmed requested `allow_sensitive_fields=true` is overridden to `false`.
- Added one queued hypothesis linked to `reasoning:creditcardfraud:dataset-risk-profile:v1`.
- Added one draft candidate finding using `credit_card_transactions_safe` evidence.
- Confirmed detail API returns one hypothesis and one draft candidate finding.
- Confirmed promoted candidate write is rejected with `400`.
- Confirmed list API returns the created session.

Checks passed:

- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`

