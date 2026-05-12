# Reasoning Eval / Validation - Task #50

## Result

PASS. The Reasoning Workbench MVP satisfies the task #45 acceptance boundary for fixed Northwind Employee #4 workload analysis.

## Environment

- Server: `http://127.0.0.1:8766`
- Default URL: <http://127.0.0.1:8766/reasoning.html?tenant=default&task=reasoning%3Aemployee-4-workload-analysis>
- Sandbox URL: <http://127.0.0.1:8766/reasoning.html?tenant=northwind-sandbox&task=reasoning%3Aemployee-4-workload-analysis>
- Validation time: `2026-05-12T07:17:35.093772+00:00`

## Checks

1. **Default positive path passed**
   - Two default runs completed: `reasoning:employee-4-workload-analysis:run:1778570254960` and `reasoning:employee-4-workload-analysis:run:1778570255023`.
   - Each run returned `run.status=completed`, `approved=true`, `eval_result.passed=true`.
   - Each run produced 2 findings, both initially `draft`.
   - Employee #4 fact stayed stable: 156 orders / 830 total orders / 18.8% share.

2. **Evidence path and provenance passed**
   - Finding evidence counts stayed `[4, 3]` across repeat runs.
   - Evidence URLs preserve `tenant=default` and return HTTP 200.
   - Evidence includes Instance node, Instance edge, ontology artifact, and aggregate source-row context.
   - Direct edge API confirms `ontology_link=link:employee:1:n:order` and `source_field=orders.employeeID`.

3. **Sandbox negative gate passed**
   - Sandbox run returned `approved=false`, `run.status=blocked`, 0 findings, 0 evidence paths.
   - Missing approved artifacts include `object:order` and `link:employee:1:n:order`.
   - Reading the default finding key under `tenant=northwind-sandbox` returns HTTP 404, so default reasoning findings do not leak across tenants.

4. **Unsupported claim validation passed with MVP limitation noted**
   - The deterministic MVP does not expose an unsupported-claim injection endpoint.
   - Both default runs returned `unsupported_claims=[]`.
   - Claims emitted in findings are backed by evidence payloads: 156-order workload aggregate, customer concentration aggregate, Employee node, Employee-Order edge, and approved ontology link.
   - No unsupported claim entered a supported finding in this run.

5. **Write boundary passed**
   - Agent tool calls write only through `propose_finding` / `propose_action` with draft scopes.
   - Agent-created findings start as `draft`; the run does not approve its own findings.
   - Needs-changes without reason returned HTTP 400; manual approve is still a separate review-gate action.
   - Manual review via `POST /api/reasoning/findings/<key>/approve` with reviewer/reason approved one finding and recorded a review event.
   - Canonical ontology artifact `link:employee:1:n:order` stayed `approved` and version stayed `6` before/after reasoning run and review.

## Verification Commands

```bash
node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Detailed JSON evidence: `reports/reasoning-task50-saskue.json`.
