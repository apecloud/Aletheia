# Task 17 Browser / Operation Review

## Scope

Reviewed the first Review Workbench implementation against the Jobs PRD and Cindy's five rework items.

URL: `http://127.0.0.1:8765`

Artifacts:
- Screenshot: `reports/screenshots/review-workbench-task17-saskue.png`
- API smoke: `reports/task17-api-smoke-saskue.json`

## Checks Performed

- Loaded `/`, `/app.js`, `/styles.css`, and `/api/artifacts`.
- Captured a 1440x1100 Chrome headless screenshot.
- Exercised artifact list/detail API for `object:employee`.
- Exercised review actions and verified audit events refresh through the detail API.
- Exercised valid payload edit through the API.
- Confirmed UI JavaScript blocks empty reason for `reject`, `needs-changes`, and `comment`.
- Confirmed UI JavaScript blocks invalid payload JSON before submit.

## Pass

- Artifact list and filters are present and dense enough for review work.
- Artifact detail shows metadata, payload, evidence, review actions, quick edit, and audit history.
- Evidence panel shows `source_ref`, summary, type, and confidence.
- UI source shows empty reason is blocked for `reject`, `needs-changes`, and `comment`.
- UI source shows invalid payload JSON is blocked before submit.
- API smoke confirms comment with reason and edit create audit history entries.
- Static screenshot confirms the workbench is operational and visually aligned with the dark, compact review-workbench direction.

## Rework Required

1. Current artifact next-step guidance is still too weak. The top status pill says `draft · not canonical`, but there is no explicit "current state -> next action" prompt near the artifact header.
2. Per-artifact approved-only eligibility is not explicit enough. The global canonical gate is visible, but the detail pane should state whether the selected artifact is eligible for default ingestion.
3. Audit History is below the first viewport for the selected artifact in a 1440x1100 screenshot. The reviewer cannot immediately see the latest decision/reason/before-after after acting.
4. Backend/API allows empty reason for `reject` and `needs-changes` even though the UI blocks it. This is not a UI-only blocker, but it is a review/audit integrity risk if API calls bypass the browser.

## Recommendation

Do not close the frontend workbench yet. Task #18 should fix the first three UI issues and should either enforce non-empty reasons for `reject` / `needs-changes` in the API or clearly document that task #17 is validating browser-only behavior.
