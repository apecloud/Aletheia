# Finding Approval Validation - task #151

Result: **PASS**

Validated service: <http://127.0.0.1:8772>

## Scope

Validated task #150 against the frozen task #149 contract:

- Candidate/Finding approve creates an approved finding that can be reused as `prior_finding / reviewed_inference`.
- Approved finding is visible in the Finding Registry and can produce Workspace next action / change proposal bridge.
- Finding approval does not write canonical ontology or graph state.
- Active context only includes active approved/reaffirmed findings; inactive statuses stay in audit/history queries.
- `creditcardfraud` raw sensitive fields `cardCVV / enteredCVV` are not exposed in Registry/API/DOM.

## Positive Chain

Fresh validation run: `1779419584`

Approved finding:
`finding:approved:candidate-autopilot-autopilot-creditcardfraud-task151-1779419584-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern`

Observed:

- Autopilot playbook produced draft candidate findings with evidence from `credit_card_transactions_safe`.
- Candidate approve created a formal `approved` finding with preserved evidence chain and review history.
- Active registry returned `context_label=prior_finding` and `reasoning_use.label=reviewed_inference`.
- Workspace action returned `canonical_write=false` / `writes_canonical=false`.
- Change proposal bridge returned `writes_canonical=false` and requires governance review.
- A later default scoped question included approved finding context as `kind=prior_finding`, `label=reviewed_inference`, not raw fact.

## Negative Chain

Canonical fingerprints before/after approve were unchanged:

- Ontology catalog API: `c6156bb87f345d49f72cd914a6520e83648b2f7d42cbf46be4f98dc95bcc1eed` -> `c6156bb87f345d49f72cd914a6520e83648b2f7d42cbf46be4f98dc95bcc1eed`
- Canonical DB tables: `eb91003c0906e181879a2a62c6203e45d7ff69b540e7bb557d8acb3400000be4` -> `eb91003c0906e181879a2a62c6203e45d7ff69b540e7bb557d8acb3400000be4`
- Default graph API: `c9fefe8f2db913601ce31a814d03044a20ec4b4ad241682fea7588e1f183cc54` -> `c9fefe8f2db913601ce31a814d03044a20ec4b4ad241682fea7588e1f183cc54`

Direct Autopilot `status=promoted` remained blocked with HTTP 400.

## State Filtering

Validated inactive filtering:

- `stale` excluded from `context=active`, visible through `status=stale`.
- `superseded` excluded from `context=active`, visible through `status=superseded`.
- `needs_more_evidence` excluded from active context.
- `rejected` excluded from active context.
- `reaffirmed` review event is visible and the finding remains active as an approved reviewed inference.

## Browser Smoke

Screenshots:

- `/tmp/task151-finding-approval.png`
- `/tmp/task151-autopilot-review-gate.png`

DOM captures:

- `/tmp/task151_finding_approval_dom.txt`
- `/tmp/task151_autopilot_review_gate_dom.txt`

Browser confirmed:

- Approved Finding Registry visible.
- Autopilot review gate visible with `Approve as finding`, `Needs more evidence`, `Reject candidate`.
- `draft_only`, `canonical_writes=disabled`, and no raw `cardCVV / enteredCVV` in DOM.

## Verification Commands

```bash
.venv/bin/python /tmp/task151_validate.py
.venv/bin/python /tmp/task151_extra.py
python3 <browser Playwright smoke for task151>
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
node --check web/review_workbench/api.js
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

## Notes

The repository already had unrelated dirty files before this validation. This task only adds Saskue validation reports and does not commit/push.
