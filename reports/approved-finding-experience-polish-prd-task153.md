# Approved Finding Experience Polish PRD - task #153

## Purpose

Task #149-#152 closed the Finding approval governance loop. Task #153 is an
experience polish layer: make approved Findings easier to scan, assign, follow
up, and revalidate.

This task must not change the governance boundary:

- approved Findings remain `prior_finding / reviewed_inference`;
- inactive Findings stay out of default active context;
- actions and proposals do not write canonical ontology or graph;
- `creditcardfraud` raw sensitive fields stay absent from API and DOM.

## MVP Scope

MVP should cover three surfaces:

1. Approved Finding Registry
2. Workspace action follow-up
3. Stale / reaffirmed revalidation queue

The goal is to help a reviewer answer:

- Which approved Findings matter now?
- Which Findings need action?
- Which approved Findings are stale or need reaffirmation?
- Who owns the next step and what was the outcome?

## 1. Approved Finding Registry

### Filters

Add filters that can be combined:

- tenant;
- status: `approved`, `stale`, `superseded`, `rejected`,
  `needs_more_evidence`;
- active context: `active`, `audit/history`;
- finding type: risk pattern, operational anomaly, quality issue, ontology
  conflict, investigation prompt;
- source: Reasoning, Autopilot, manual review;
- action state: no action, open action, overdue action, closed action;
- freshness: reaffirmed recently, due for revalidation, stale;
- confidence/value score range.

### Sorting

MVP sorting:

- value score descending;
- newest approved/reaffirmed first;
- oldest unrevalidated first;
- action due date ascending;
- confidence descending.

### Grouping

MVP grouping:

- by tenant;
- by status;
- by finding type;
- by action state;
- by source session/run.

### Row/Card Display

Each row should show:

- title and concise conclusion;
- status badge;
- active-context badge: `active prior insight` or `audit only`;
- confidence/value score;
- evidence count;
- source label: Reasoning / Autopilot;
- latest review event: approved, reaffirmed, stale, superseded;
- linked action summary: owner, due date, state.

Every approved row must still show that it is a reviewed inference, not a raw
fact.

## 2. Workspace Action Follow-Up

Approved Findings can create Workspace actions. The current bridge proves the
link exists; this polish task makes the action usable.

### Action Fields

MVP action fields:

```json
{
  "action_key": "...",
  "finding_key": "...",
  "tenant_id": "...",
  "title": "...",
  "action_type": "investigate | request_evidence | rerun_autopilot | propose_change | monitor",
  "owner": "@person-or-agent",
  "due_at": "...",
  "priority": "high | medium | low",
  "status": "open | in_progress | blocked | closed | reopened",
  "result": null,
  "created_from": "approved_finding",
  "canonical_write": false
}
```

### Action Workflow

Required transitions:

```text
open -> in_progress
open -> blocked
in_progress -> blocked
in_progress -> closed
blocked -> in_progress
closed -> reopened
reopened -> in_progress
```

Closing an action requires a result:

- confirmed risk;
- false positive;
- evidence added;
- proposal created;
- no action needed;
- rerun scheduled.

Closing or reopening an action appends a Finding usage/review event. It must not
silently mutate the Finding status. If the result affects the Finding, the UI
should offer an explicit follow-up: reaffirm, mark stale, supersede, reject, or
create proposal.

## 3. Stale / Reaffirmed Batch Revalidation

Approved Findings should be revalidated when their basis changes or when they
age out.

### Batch Queue

Add a revalidation queue with:

- candidate Findings due for review;
- reason: source changed, ontology changed, rule changed, evidence degraded,
  manual review, aging threshold;
- current status and last reaffirmed time;
- affected downstream reasoning/actions;
- suggested batch operation.

### Batch Actions

MVP batch actions:

- reaffirm selected;
- mark stale selected;
- create rerun task for selected;
- assign revalidation owner;
- export selected to audit report.

Each batch action must write per-Finding review events. There must be no hidden
bulk canonical write.

### Reaffirmed Visibility

Reaffirmed Findings remain active, but users must see:

- latest reaffirmed time;
- who reaffirmed;
- basis snapshot/hash;
- what changed since original approval;
- whether action state changed.

## MVP Acceptance Criteria

Implementation is complete when:

- Approved Finding Registry supports filter, sort, and group controls for the
  MVP dimensions above.
- Registry rows clearly distinguish active reviewed insight from audit-only
  Findings.
- Workspace actions created from Findings support owner, due date, priority,
  status, result, close, and reopen.
- Closing an action records a result and does not silently change Finding status.
- Stale/reaffirmed queue shows due-for-review Findings and supports at least
  reaffirm selected, mark stale selected, and assign owner.
- Batch revalidation writes explicit per-Finding review events.
- Default active reasoning context remains unchanged: only active
  approved/reaffirmed Findings are included.
- Canonical ontology/graph fingerprints remain unchanged after registry filters,
  action changes, and batch revalidation operations.
- `creditcardfraud` API/DOM continues to omit `cardCVV` and `enteredCVV`.

## Suggested Phase 2

Defer these until MVP is stable:

- SLA dashboards for overdue Finding actions;
- per-role work queues for reviewer, analyst, ontology maintainer, data engineer;
- saved registry views;
- bulk export to external ticketing systems;
- impact analysis graph for Finding dependencies;
- automatic stale detection from source/ontology/rule diff events;
- notification preferences and reminders;
- advanced duplicate/similar Finding clustering.

## Implementation Handoff

Recommended next implementation task:

```text
Approved Finding Experience Implementation:
Registry filters/sort/group, action owner/due/result workflow, stale/reaffirmed batch queue.
```

Recommended validation task:

```text
Approved Finding Experience Validation:
UI/API smoke for registry controls, action close/reopen, batch revalidation,
active-context filtering, canonical write negative gate, sensitive-field boundary.
```

