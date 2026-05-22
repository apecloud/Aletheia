# Creditcardfraud Autopilot Discovery Playbook - task #142

## Scope

Implemented a fixed `creditcardfraud` discovery playbook that writes only through the task #140 Autopilot API model:

- Autopilot session
- hypothesis queue
- pruned hypothesis with reason
- draft candidate findings with evidence chains

It does not create formal reasoning findings, does not write canonical ontology/graph data, and does not approve anything.

## API

Added:

- `POST /api/reasoning/autopilot/playbooks/creditcardfraud/run?tenant=creditcardfraud`

The UI can trigger this from the Reasoning page Autopilot tab via `Run creditcardfraud playbook`.

## Smoke Session

Validation session:

- `autopilot:creditcardfraud:task142-playbook-smoke`
- Page URL: `http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud`

## Produced Hypotheses

- Completed: card-not-present transactions concentrate fraud risk.
- Completed: verification mismatch transactions have elevated fraud rate.
- Completed: missing POS entry mode may identify a weak-control channel.
- Completed: merchant categories concentrate fraud exposure.
- Completed: same account/merchant/amount/day duplicate clusters indicate multi-swipe risk.
- Pruned: expiration-key mismatch does not clear the value threshold.
  - Reason: expected fraud-rate lift is below candidate threshold and no strong operational action follows from the field alone.

## Draft Candidate Findings

- `Card-not-present transactions carry elevated fraud risk`
- `Verification mismatch is a compact fraud-risk signal`
- `Missing POS entry mode should be reviewed as a weak-control pattern`
- `Merchant category concentration reveals high-yield fraud review segments`
- `Same-day duplicate transaction clusters need multi-swipe review`

Each candidate includes:

- `status=draft`
- value/confidence/novelty/impact scores
- evidence chain entries pointing at `credit_card_transactions_safe`
- evidence limits
- suggested next action

## Safety Validation

- API payload for the smoke session does not contain raw field names `cardCVV` or `enteredCVV`.
- Page DOM for the smoke session does not contain raw field names `cardCVV` or `enteredCVV`.
- Safety profile uses blocked field group `card_verification_code_fields`.
- Direct `status=promoted` candidate write returns `400`.
- UI still has no `Approve candidate` or `Promote candidate` button.

Screenshot:

- `/tmp/task142-creditcardfraud-playbook.png`

Checks passed:

- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `node --check web/review_workbench/api.js`
- Babel transform for `web/review_workbench/reasoning.jsx`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`

