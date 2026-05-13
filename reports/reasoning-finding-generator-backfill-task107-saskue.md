# Reasoning Finding Generator/Backfill Task #107 Validation

Status: PASS
Validator: Saskue
Date: 2026-05-13

## Scope

Validated task #107 after the finding generator/display-normalization change. The goal was to remove legacy `draft-only` template wording from user-facing finding titles and conclusions while preserving raw provenance and keeping reasoning/canonical safety gates unchanged.

## API Validation

Legacy finding validated:

`finding:graph-scope:reasoning:graph-scope:default-question-center-question-scope-employee-5-d1-n200-e200-q0957854216:run-1778640570198`

The following endpoints all return normalized display fields for the legacy row:

- `/api/portal/overview?tenant=default`
- `/api/portal/findings/<finding_key>?tenant=default`
- `/api/reasoning/tasks/<task_key>?tenant=default`
- `/api/reasoning/findings/<finding_key>?tenant=default`

Assertions passed:

- Display title: `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`
- Display conclusion answers the Employee:5 work snapshot and does not change canonical ontology or graph.
- `display_normalized=true`
- `raw_title=Scoped graph reasoning remains draft-only for Employee:5`
- `raw_conclusion` preserves the original legacy scoped graph template.
- Main `title` / `conclusion` do not contain `draft-only`.
- Supporting evidence and provenance remain present, including `question_center` and task/run/evidence chain.

New generation smoke also passed. A newly generated question-center Employee:5 finding has the same business-answer title/conclusion shape, does not contain `draft-only`, and does not carry `display_normalized/raw_title/raw_conclusion` because it is not a legacy row.

## Browser Validation

Real Chrome validation passed for the legacy row in Chinese UI mode:

- Findings page main detail shows the normalized Employee:5 title/conclusion, not the legacy template.
- Evidence page linked finding shows the normalized Employee:5 title, while retaining evidence source/path/provenance.
- Reasoning detail shows the normalized current answer and governance/canonical boundary copy.

Screenshots:

- Findings: `/tmp/task107-saskue-findings.png`
- Evidence: `/tmp/task107-saskue-evidence.png`
- Reasoning: `/tmp/task107-saskue-reasoning.png`

## Safety Regression

PASS.

- Default graph: `approved=true`, 157 nodes, 156 edges.
- Default graph checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph: `approved=false`, 0 nodes, 0 edges, missing `object:order` and `link:employee:1:n:order`; no fallback.
- Canonical artifact `link:employee:1:n:order`: `approved`, version 6.
- `draft_only` remains a review/write-boundary status, not a main title or conclusion.
- Approving a finding is still not represented as an automatic canonical graph/ontology write.

## Verification Commands

```bash
python3 /tmp/task107_validate.py

node --check web/review_workbench/reasoning_app.js &&
node --check web/review_workbench/shell_app.js &&
node --check web/review_workbench/findings_app.js &&
node --check web/review_workbench/evidence_app.js &&
git diff --check

/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

All commands passed.

## Artifacts

- JSON evidence: `reports/reasoning-finding-generator-backfill-task107-saskue.json`
- Implementation report: `reports/reasoning-finding-generator-backfill-task107-itachi.md`
- Implementation JSON: `reports/reasoning-finding-generator-backfill-task107-itachi.json`

## Conclusion

Task #107 PASS. Legacy `draft-only` template wording is no longer used as user-facing main title/conclusion in the validated API and browser surfaces, while raw legacy values and provenance remain available for audit. New generated question-center scoped findings now use business-answer titles/conclusions. Safety boundaries remain unchanged.
