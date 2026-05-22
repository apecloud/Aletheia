# Approved Finding Experience Implementation - task #154

## Scope

Implemented the experience polish layer on top of the completed Finding approval loop:

- Approved Finding Registry filters, sorting, grouping metadata, and richer row cards.
- Persistent Workspace action follow-up for approved findings: owner, due date, priority, status, result, close/reopen.
- Stale / reaffirmed batch revalidation APIs.

This does not change the governance contract from task #150:

- approved findings remain `prior_finding / reviewed_inference`;
- non-active findings do not enter default active context;
- action lifecycle changes do not silently mutate Finding lifecycle;
- actions and revalidation write no canonical ontology or graph state.

## Backend

Added `aletheia_finding_actions` as a tenant-scoped operational table:

- `action_key`, `finding_key`, `title`, `action_type`
- `owner`, `due_at`, `priority`
- `status`: `open`, `in_progress`, `blocked`, `closed`, `reopened`
- `result`, `result_detail`
- `canonical_write=false`, `graph_write=false`

Action transitions:

- `open -> in_progress`
- `open -> blocked`
- `in_progress -> blocked`
- `in_progress -> closed`
- `blocked -> in_progress`
- `closed -> reopened`
- `reopened -> in_progress`

Closing an action requires one of:

- `confirmed_risk`
- `false_positive`
- `evidence_added`
- `proposal_created`
- `no_action_needed`
- `rerun_scheduled`

Close/reopen/update writes `aletheia_reasoning_reviews` usage events such as `action_close`, while preserving Finding status/version.

## API

Registry:

- `GET /api/reasoning/findings?tenant=<id>&status=<status>&context=<active|audit>&finding_type=<type>&source=<source>&action_state=<state>&freshness=<state>&sort=<sort>&group=<group>`

Action workflow:

- `POST /api/reasoning/findings/<finding_key>/actions`
- `POST /api/reasoning/finding-actions/<action_key>/start`
- `POST /api/reasoning/finding-actions/<action_key>/block`
- `POST /api/reasoning/finding-actions/<action_key>/close`
- `POST /api/reasoning/finding-actions/<action_key>/reopen`

Batch revalidation:

- `GET /api/reasoning/findings/revalidation-queue?tenant=<id>`
- `POST /api/reasoning/findings/revalidation-batch` with `action=reaffirm | mark_stale | assign_owner`

## UI

Reasoning page Approved Finding Registry now includes:

- status / active-context / finding type / action state / freshness / sort filters;
- owner and due date inputs;
- batch buttons: `Reaffirm selected`, `Mark stale`, `Assign owner`;
- row cards showing active/audit label, source, finding type, confidence, value, evidence count, freshness, and linked action state;
- action controls: create, start, block, close with result, reopen.

## Validation

Static checks:

- `python3 -m py_compile review_workbench.py`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `node --check web/review_workbench/api.js`
- `PYTHONPATH=. .venv/bin/python tests/test_ontology_eval.py`
- `git diff --check`

Smoke on `http://127.0.0.1:8772`:

- Created a fresh creditcardfraud Autopilot session and approved three candidate findings.
- Registry filter/sort/group sample returned filtered rows and `groups=[{"group":"open_action","count":2}]`.
- Created an action with owner `@Saskue`, due date `2026-05-29T12:00:00`, and priority `high`.
- Transitioned action `open -> in_progress -> closed -> reopened`; close required `result=confirmed_risk`; response reported `finding_status_unchanged=true`.
- Batch reaffirm wrote per-finding `reaffirmed` review event.
- Batch assign owner created a revalidation action owned by `@Jobs`.
- Batch mark stale wrote per-finding `stale` review event.
- Active context excluded the stale finding; explicit `status=stale` audit query returned it.
- Ontology artifact fingerprint before/after action + batch operations was unchanged.
- Default graph API fingerprint before/after action + batch operations was unchanged.
- API and Chrome DOM smoke did not contain raw `cardCVV` or `enteredCVV`.

Chrome DOM artifact:

- `/tmp/task154-reasoning-dom.html`

## Demo

- Reasoning page: <http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud>
- Registry API: <http://127.0.0.1:8772/api/reasoning/findings?tenant=creditcardfraud&context=active>
- Revalidation queue API: <http://127.0.0.1:8772/api/reasoning/findings/revalidation-queue?tenant=creditcardfraud>
