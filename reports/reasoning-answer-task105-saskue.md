# Reasoning Answer Task #105 Validation

Status: PASS
Validator: Saskue
Date: 2026-05-13

## Scope

Validated the post-shell-polish Reasoning detail fix for the user URL:

`http://127.0.0.1:8767/reasoning.html?task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216&tenant=default`

The original issue was not API absence. The task had a completed run and a finding, but the page did not present a clear answer or explain the draft/review boundary.

## API Evidence

The task API returns a completed run and one finding:

- `latest_run.status`: `completed`
- `findings.length`: `1`
- finding status: `approved`
- confidence: `0.72`
- supporting evidence kind: `question_scope`
- supporting evidence source_ref: `question_center`

The persisted finding row still has the legacy template title/conclusion:

- title: `Scoped graph reasoning remains draft-only for Employee:5`
- conclusion: `This scoped graph task was created from Graph Explorer evidence...`

The Reasoning detail page now translates that weak stored artifact into a stronger user-facing answer panel for this scoped question. If product wants every downstream surface to stop showing the legacy template, the stored finding generator/backfill should be handled as a separate content-quality task.

## Browser Evidence

Real Chrome validation passed in Chinese UI mode.

User URL:

- The answer panel is visible in the first viewport: top offset `150px`.
- The panel title includes `当前结论`.
- The main answer says `Employee:5 work snapshot: Steven Buchanan has 42 approved order relationships`.
- The answer body states that Steven Buchanan is a Sales Manager and has 42 handled order relationships in the approved graph/current evidence scope.
- Status/confidence are visible: `approved · v2`, `confidence 0.72`.
- Key basis is visible: Steven Buchanan is connected to 42 approved Order relationships through the Employee-Order ontology link, with source `question_center`.
- Next-step actions are visible: open explanation, open evidence chain, open graph context, submit review, request more evidence, rerun reasoning.

Governance copy also passed:

- `draft-only` is not used as the main conclusion.
- The page explains the review/audit trail, approved finding layer, and canonical write proposal boundary.
- The page states that approving this finding does not automatically change canonical ontology or graph.
- The page does not imply that approving a finding directly writes to canonical graph/ontology.

No-finding path:

- Existing no-run task URL shows `尚未运行推理 / Not run yet`.
- It provides a `Run reasoning` entry.
- The no-finding state is explicit instead of leaving users to infer from empty panels.

Screenshots:

- User URL: `/tmp/task105-reasoning-answer.png`
- No-finding URL: `/tmp/task105-no-finding.png`

## Safety Regression

PASS.

- Default graph: `approved=true`, 157 nodes, 156 edges.
- Default order checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph: `approved=false`, 0 nodes, 0 edges, missing `object:order` and `link:employee:1:n:order`; no fallback.
- Canonical artifact `link:employee:1:n:order`: `approved`, version 6.
- Graph handoff from Employee:4 still produces a scoped Reasoning URL with `center_node=Employee%3A4`.

## Verification Commands

```bash
python3 /tmp/task105_validate.py

node --check web/review_workbench/reasoning_app.js &&
node --check web/review_workbench/shell_app.js &&
git diff --check

/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

All commands passed.

## Artifacts

- JSON evidence: `reports/reasoning-answer-task105-saskue.json`
- User URL screenshot: `/tmp/task105-reasoning-answer.png`
- No-finding screenshot: `/tmp/task105-no-finding.png`

## Conclusion

Task #105 PASS for the Reasoning detail UI behavior requested by the user. The page now surfaces a first-viewport answer, makes the review/canonical boundary explicit, and gives a clear no-finding state.

Residual observation: the stored finding row still contains the legacy template wording. The user-facing Reasoning detail page corrects the presentation, but a follow-up generator/backfill task would be appropriate if Findings/Evidence overview pages must also stop showing legacy `draft-only` titles for older rows.
