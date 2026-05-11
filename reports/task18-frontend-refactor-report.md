# Task 18 Frontend Refactor Verification

## Scope

Task #18 addressed the accepted review findings without expanding the product surface:

- Add selected artifact "current state -> next action" guidance.
- Show artifact-level default ingestion eligibility.
- Surface the latest audit decision/reason/before-after status near the top of the detail view.
- Keep frontend payload JSON validation and visible error feedback.
- Enforce non-empty reason on the backend for `reject`, `needs-changes`, and `comment`; `approve` may remain reasonless but still writes an audit event.

## Implementation

- `review_workbench.py`
  - Adds backend reason validation for rejected, needs_changes, and comment decisions.
  - Keeps approve path unchanged except for normal audit event recording.
- `web/review_workbench/index.html`
  - Adds a top workflow strip for current state, next action, default ingestion eligibility, and latest audit.
- `web/review_workbench/app.js`
  - Renders status guidance, artifact-level eligibility, and latest audit detail.
  - Keeps frontend reason validation and payload JSON validation.
  - Displays backend API errors through the existing toast path.
- `web/review_workbench/styles.css`
  - Adds workflow strip, eligibility, and invalid JSON field styling.

## Smoke Results

- Local URL: <http://127.0.0.1:8765>
- Static UI: `GET /` returns 200.
- Artifact API: `GET /api/artifacts/object%3Acustomer` returns the selected artifact detail.
- Backend reason enforcement:
  - `POST /api/artifacts/object%3Acustomer/reject` with empty reason returns 400.
  - `POST /api/artifacts/object%3Acustomer/needs-changes` with empty reason returns 400.
  - `POST /api/artifacts/object%3Acustomer/comment` with empty reason returns 400.
- Status preservation after failed reason checks: `object:customer` remains `approved`, version `2`.
- Screenshot: `reports/screenshots/review-workbench-task18.png`.

## Command Verification

- `.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py query_metadata.py evals tests`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `node --check web/review_workbench/app.js`
- `git diff --check`
