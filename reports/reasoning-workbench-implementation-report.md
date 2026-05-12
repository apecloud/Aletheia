# Reasoning Workbench MVP Implementation Report

## Scope

- Task #46: Reasoning artifact schema for `ReasoningTask`, `ReasoningRun`, `ReasoningFinding`, and review events.
- Task #47: Tenant-scoped approved-only tool API for graph, instance, and artifact lookup.
- Task #48: Agent write boundary: the reasoning agent can only propose draft findings and draft action proposals.
- Task #49: Reasoning Workbench MVP with trace, evidence paths, and finding review gate.

## Product Boundary

The MVP is intentionally fixed to the Northwind question:

`Why did Employee #4 / Margaret Peacock handle 156 orders, and is there workload concentration or customer/order risk?`

It does not implement open-ended chat, global graph traversal, cross-tenant reasoning, automatic action execution, or canonical ontology/graph writes.

## Implementation

- Added reasoning tables:
  - `aletheia_reasoning_tasks`
  - `aletheia_reasoning_runs`
  - `aletheia_reasoning_findings`
  - `aletheia_reasoning_reviews`
- Added Reasoning APIs:
  - `GET /api/reasoning/tasks?tenant=<tenant>`
  - `GET /api/reasoning/tasks/<task_key>?tenant=<tenant>`
  - `POST /api/reasoning/tasks/<task_key>/run?tenant=<tenant>`
  - `GET /api/reasoning/findings/<finding_key>?tenant=<tenant>`
  - `POST /api/reasoning/findings/<finding_key>/<approve|reject|needs-changes|comment>?tenant=<tenant>`
- Added `reasoning.html` and `reasoning_app.js`.
- Added Reasoning navigation to the shared Portal Shell.

## Agent Tool Boundary

The deterministic MVP agent uses the same boundary expected for future LLM agents:

- `graph_query`: reads only current tenant approved graph through `InstanceRepository.neighborhood`.
- `instance_lookup`: reads current tenant source row / node detail through `InstanceRepository.detail`.
- `artifact_lookup`: reads only current tenant approved ontology artifacts.
- `propose_finding`: writes run-scoped draft `ReasoningFinding`.
- `propose_action`: writes draft action proposal inside the finding payload only.

The run cannot approve its own findings, ingest graph data, write source rows, or modify canonical ontology artifacts.

## Default Positive Path

Default tenant run returns:

- `approved=true`
- `run.status=completed`
- 2 draft findings:
  - `finding:employee-4-workload-concentration:<run>`
  - `finding:employee-4-follow-up-risk-review:<run>`
- Evidence paths per finding:
  - 4 for workload concentration
  - 3 for follow-up risk review
- Eval:
  - `approved_only=true`
  - `finding_count=2`
  - `unsupported_claims=[]`
  - `passed=true`

## Sandbox Negative Gate

`northwind-sandbox` run returns:

- `approved=false`
- `run.status=blocked`
- 0 findings
- missing approved artifacts:
  - `object:order`
  - `link:employee:1:n:order`

## URLs

- Reasoning Workbench: <http://127.0.0.1:8765/reasoning.html?tenant=default>
- Fixed default task: <http://127.0.0.1:8765/reasoning.html?tenant=default&task=reasoning%3Aemployee-4-workload-analysis>
- Sandbox negative gate: <http://127.0.0.1:8765/reasoning.html?tenant=northwind-sandbox&task=reasoning%3Aemployee-4-workload-analysis>
- Evidence path example: <http://127.0.0.1:8765/instances.html?tenant=default&type=Employee&id=4&edgeSource=Employee%3A4&edgeTarget=Order%3A10250>
- Ontology evidence example: <http://127.0.0.1:8765/?tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder>

## Validation Commands

```bash
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/reasoning_app.js
.venv/bin/python -m py_compile review_workbench.py agents/ontology_artifacts.py
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

## Local Smoke Commands

These were run against a local updated server on port `8766` to avoid replacing the already-running port `8765` process:

```bash
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8766 --ensure-schema
curl -sS -X POST 'http://127.0.0.1:8766/api/reasoning/tasks/reasoning%3Aemployee-4-workload-analysis/run?tenant=default' -H 'Content-Type: application/json' -d '{}'
curl -sS -X POST 'http://127.0.0.1:8766/api/reasoning/tasks/reasoning%3Aemployee-4-workload-analysis/run?tenant=northwind-sandbox' -H 'Content-Type: application/json' -d '{}'
```
