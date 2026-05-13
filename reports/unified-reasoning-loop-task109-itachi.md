# Unified Reasoning Loop Task #109

## Scope

Task #109 turns `/reasoning.html` into the unified loop workspace for Question -> Answer -> Evidence -> Follow-up / Review. It keeps `/questions.html` and `/evidence.html` as history/browser entry points, but the single-task workflow now stays on the reasoning page.

## Changes

- Promoted `/reasoning.html` to the primary unified workspace:
  - current task question and status remain in the sticky header
  - current answer stays first in the main content and remains visible in the first viewport
  - evidence chain, graph path, ontology/rule basis, raw evidence payload, and question context are available as collapsible sections on the same page
  - follow-up question, rerun, submit review, request more evidence, approve / needs changes / reject / comment actions remain scoped to the current task/finding
- Added in-page question creation:
  - create a scoped question from the unified page
  - create follow-up questions using the current task scope
  - URL is updated to the new `task` and remains refresh-safe
- Updated `/questions.html`:
  - creating a scoped question now redirects to `/reasoning.html?tenant=...&task=...`
  - history links say `Open reasoning loop`
- Updated `/evidence.html` linked actions:
  - evidence items now link back to the unified reasoning loop for the task
  - evidence browser remains a global browser/history view
- Added English/Chinese shell copy for the new loop labels and actions.

## Validation Evidence

Existing user URL:

- URL: `http://127.0.0.1:8767/reasoning.html?task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216&tenant=default`
- Current answer appears at top `178.78px` in the viewport.
- Answer: `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`
- Same page includes:
  - question form / context
  - answer
  - evidence chain with 2 items
  - follow-up form
  - review actions

Question creation flow:

- Created a new Employee:5 scoped question from `/questions.html`.
- Browser landed on `/reasoning.html?tenant=default&task=...`.
- No-finding state showed `尚未运行推理 / Not run yet` with a visible run action.
- Running from the same page produced an Employee:5 answer and evidence count without `draft-only` as the main answer.

Mobile smoke:

- Mobile viewport renders the same answer and evidence section.

Screenshots:

- `/tmp/task109-user-url.png`
- `/tmp/task109-created-question.png`
- `/tmp/task109-created-run.png`
- `/tmp/task109-mobile.png`

Safety regression:

- Default graph remains 157 nodes / 156 edges.
- Sandbox remains approved-only blocked: 0 nodes / 0 edges, no fallback.
- Canonical `link:employee:1:n:order` remains `approved` v6.
- `python3 /tmp/task105_validate.py` still passes, confirming the prior Reasoning Answer first-screen behavior did not regress.

## Verification Commands

```bash
node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/evidence_app.js && node --check web/review_workbench/shell_app.js && git diff --check
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
python3 /tmp/task105_validate.py
```

## Non-goals Preserved

- No free SQL/Cypher/SPARQL was added.
- Reasoning output remains draft-bound unless reviewed.
- Approving a finding still does not write canonical graph/ontology.
- Existing Findings and Evidence pages were not deleted; they remain list/browser/deep-link support surfaces.
