# Approved Finding Experience Validation - task #155

## Result

PASS.

Validated task #154 against the product boundary from task #153 and the previously accepted finding-approval contract:

- Approved Finding Registry filtering, sorting, grouping, and active-context behavior work.
- Workspace finding actions persist owner/due/priority/result and support close/reopen without changing the underlying Finding lifecycle.
- Batch revalidation writes explicit per-finding review events for reaffirm, mark stale, and assign owner.
- Experience polish did not bypass the canonical/graph write boundary.
- `creditcardfraud` sensitive raw fields remain hidden from API payloads and browser DOM.

Service under test: <http://127.0.0.1:8772>

## API / DB Validation

Validation script: `/tmp/task155_validate.py`

Machine-readable report: `reports/approved-finding-experience-validation-task155-saskue.json`

Run id: `1779420848`

Fresh test keys:

- Action finding: `finding:approved:candidate-autopilot-autopilot-creditcardfraud-task155-action-1779420848-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern`
- Action key: `action:finding-approved-candidate-autopilot-autopilot-creditcardfraud-task155-action-1779420848-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern:task155-action-1779420848`
- Stale finding: `finding:approved:candidate-autopilot-autopilot-creditcardfraud-task155-stale-1779420848-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern`
- Reaffirm finding: `finding:approved:candidate-autopilot-autopilot-creditcardfraud-task155-reaffirm-1779420848-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern`
- Assign-owner finding: `finding:approved:candidate-autopilot-autopilot-creditcardfraud-task155-owner-1779420848-missing-pos-entry-mode-should-be-reviewed-as-a-weak-control-pattern`

Checks passed: 26/26.

Covered checks:

- `action_created_owner_due`
- `action_transition_start_close_reopen`
- `action_close_requires_result`
- `action_lifecycle_does_not_change_finding`
- `action_review_events_written`
- `registry_action_filter_group`
- `registry_type_source_sort`
- `registry_no_sensitive`
- `batch_reaffirm_review_event`
- `batch_stale_review_event`
- `batch_assign_owner_action_event_only`
- `stale_not_in_active`
- `stale_visible_audit`
- `revalidation_queue_visible`
- `active_registry_safe`
- `ontology_api_fingerprint_unchanged`
- `canonical_db_fingerprint_unchanged`
- `default_graph_fingerprint_unchanged`

## Fingerprint Gates

All canonical and graph fingerprints stayed unchanged after approve/action/batch operations.

| Surface | Before | After |
| --- | --- | --- |
| `creditcardfraud` Ontology catalog API | `c6156bb87f345d49f72cd914a6520e83648b2f7d42cbf46be4f98dc95bcc1eed` | `c6156bb87f345d49f72cd914a6520e83648b2f7d42cbf46be4f98dc95bcc1eed` |
| Canonical DB tables | `eb91003c0906e181879a2a62c6203e45d7ff69b540e7bb557d8acb3400000be4` | `eb91003c0906e181879a2a62c6203e45d7ff69b540e7bb557d8acb3400000be4` |
| Default graph API | `b11193f8a7abad6578e32b0f387817921116a8a3b087e5f32781ed875ca425ea` | `b11193f8a7abad6578e32b0f387817921116a8a3b087e5f32781ed875ca425ea` |

Canonical DB row counts were stable:

- `aletheia_ontology_artifacts`: 7
- `aletheia_artifact_reviews`: 7
- `aletheia_business_objects`: 0
- `aletheia_business_links`: 0

Default graph stayed `approved=true`, `nodes=157`, `edges=156`.

## Browser Smoke

URL:

<http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud>

Captured artifacts:

- Screenshot: `/tmp/task155-approved-finding-experience.png`
- DOM/HTML: `/tmp/task155_approved_finding_experience_dom.txt`

Browser DOM contained the expected experience controls:

- `Approved Finding Registry`
- `Open action`
- `Due for review`
- `Action due date`
- `Reaffirm selected`
- `Mark stale`
- `Assign owner`
- `Create action`

Browser DOM/HTML did not contain `cardCVV` or `enteredCVV`.

## Boundary Notes

- Action `close` requires a `result`.
- Action `close` and `reopen` write action/review events only and do not mutate the underlying Finding `status` or `version`.
- Batch `reaffirm`, `mark_stale`, and `assign_owner` each leave explicit per-finding review/action events.
- `stale` findings are excluded from `context=active` and visible through explicit status/audit queries.
- Sensitive evidence remains sourced from `credit_card_transactions_safe`.

## Verification Commands

```bash
.venv/bin/python /tmp/task155_validate.py
python3 <browser DOM smoke for task155>
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py
node --check web/review_workbench/api.js
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

## Worktree Note

This validation adds:

- `reports/approved-finding-experience-validation-task155-saskue.md`
- `reports/approved-finding-experience-validation-task155-saskue.json`

Existing unrelated dirty files and older untracked reports were left untouched.
