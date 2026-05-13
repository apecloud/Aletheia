# Reasoning Finding Generator/Backfill Task #107

## Scope

Task #107 cleans legacy `draft-only` wording out of user-facing finding titles and conclusions without changing review, approved-only, or canonical write boundaries.

## Changes

- Updated scoped graph finding generation in `ReasoningRepository.run_scoped_graph_task` so new `question_center` and Graph Explorer handoff findings produce business-answer titles and conclusions.
- Added display normalization for historical legacy scoped graph findings when returned by:
  - `/api/portal/overview`
  - `/api/portal/findings/<finding_key>`
  - `/api/reasoning/tasks/<task_key>`
  - `/api/reasoning/findings/<finding_key>`
- Preserved provenance for normalized legacy rows by returning `raw_title`, `raw_conclusion`, and `display_normalized=true`.
- Kept `draft` language only as governance/review status. The main title and conclusion no longer use `draft-only` as the answer.
- Did not alter approved-only graph gating, reasoning draft-only write boundary, review audit trail, or canonical ontology/graph write gate.

## Validation Evidence

User legacy finding:

- Raw stored title: `Scoped graph reasoning remains draft-only for Employee:5`
- API display title: `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`
- API display conclusion: `For the question "查看一些 5 号员工的工作情况", the approved graph shows Steven Buchanan (Sales Manager) with 42 handled order relationships; 42 relationships are loaded in the current evidence scope. This is a draft answer for review and does not change canonical ontology or graph.`
- `display_normalized=true`
- `draft-only` does not appear in the main title or conclusion.

New generation smoke:

- Created and ran a new question-center Employee:5 task.
- Generated title: `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`
- Generated conclusion answers the Employee:5 work snapshot and does not contain `draft-only`.

Browser screenshots:

- Findings legacy row: `/tmp/task107-findings.png`
- Evidence linked finding: `/tmp/task107-evidence.png`
- Reasoning detail answer: `/tmp/task107-reasoning.png`

Safety regression:

- Default graph remains 157 nodes / 156 edges.
- Sandbox remains approved-only blocked: 0 nodes / 0 edges, no fallback.
- Canonical `link:employee:1:n:order` remains `approved` v6.

## Verification Commands

```bash
node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/shell_app.js && node --check web/review_workbench/findings_app.js && node --check web/review_workbench/evidence_app.js && git diff --check
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
python3 /tmp/task105_validate.py
```

## Notes

- Historical database rows are not overwritten in this change. The API compatibility layer supplies user-facing display fields while preserving original raw text for audit/provenance.
- Approving a finding still does not write canonical ontology or graph. Structural graph/ontology changes remain a separate canonical write proposal path.
