# Reasoning Portal Redesign Implementation Report

## Scope

Tasks: #90, #91, #92, #93, #94

This change reframes Aletheia Portal from a graph-first browser into a reasoning workbench. The default entry now surfaces conclusions, explanation paths, attention items, questions, and quality status before users drill down into graph, ontology, runtime, or audit details.

## Implemented

### #90 Reasoning Workbench Home

- Replaced the default `/` page with a reasoning situation panel.
- Added `GET /api/portal/overview`.
- The first screen shows:
  - knowledge status: tenant, namespace, graph database, approved-only state, entity/relation/finding/task counts, latest update
  - key findings as conclusion cards with confidence, status, evidence count, scope, and explanation link
  - attention items for draft findings, low-confidence conclusions, blocked/failed runs, sandbox approved-only gaps, and agent policy issues
  - quick tasks organized by user goal
  - recent reasoning tasks, runs, and findings

### #91 Finding / Evidence Detail

- Added `/findings.html` and `GET /api/portal/findings/<canonical_key>`.
- Finding detail is organized as explanation structure, not raw JSON:
  - conclusion summary
  - confidence and review status
  - question and scope
  - reasoning run trace summary
  - supporting evidence
  - counter evidence / conflicts empty state
  - graph path / source context link
  - ontology/rule/review-gate basis
  - follow-up questions
- Evidence cards label evidence type as `fact`, `hypothesis`, `conflict`, or `missing` from recorded path kind.

### #92 Question Center MVP

- Added `/questions.html`.
- Added `POST /api/reasoning/questions`.
- Users can create scoped question tasks with:
  - question input
  - scope selector
  - center node
  - depth / limit
  - graph context link
- History lists reasoning tasks with status, latest run status, scope, source, depth/limit, and links to run detail and findings.

### #93 Quality & Attention Panel

- Added `/quality.html`.
- Quality panel summarizes:
  - draft findings awaiting review
  - low-confidence findings
  - blocked/failed reasoning runs
  - blocked/failed agent runs and policy violations
  - sandbox missing approved artifacts
- Sandbox gate is explicitly shown as blocked when approved-only graph context cannot be built.

### #94 Portal Navigation Reframe

- Reframed top navigation to:
  - Workbench
  - Questions
  - Findings
  - Evidence
  - Explore
  - Quality
  - Ontology
  - Runtime
  - Audit
- Moved previous ontology review workbench to `/ontology.html`.
- Existing Graph, Instance, Reasoning, and Settings views remain available, but Graph is now under Explore and is no longer the default entry.
- Graph -> scoped reasoning remains available from graph context actions without hijacking the Questions nav item.

## Safety Boundaries

- Tenant remains URL-scoped across all new pages and APIs.
- Portal overview and detail APIs read tenant-scoped data only.
- Graph context continues to use approved-only artifacts.
- Question Center creates draft-only scoped reasoning tasks; it does not approve, ingest, or write canonical artifacts.
- Sandbox remains blocked instead of falling back to default tenant graph data.
- Canonical `link:employee:1:n:order` remains approved v6.

## Validation

Static checks:

```bash
node --check web/review_workbench/portal_app.js
node --check web/review_workbench/questions_app.js
node --check web/review_workbench/findings_app.js
node --check web/review_workbench/quality_app.js
node --check web/review_workbench/graph_app.js
node --check web/review_workbench/reasoning_app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/settings_app.js
node --check web/review_workbench/ontology_app.js
git diff --check
```

Python checks:

```bash
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

API checks:

- `GET /api/portal/overview?tenant=default` returns ready status with 157 entities, 156 relations, key findings, attention items, quick tasks, and recent changes.
- `GET /api/graph/context?tenant=northwind-sandbox&type=Employee&id=4&depth=1&limit=200` returns `approved=false`, 0 nodes, 0 edges, and missing approved artifacts.
- `GET /api/artifacts/link%3Aemployee%3A1%3An%3Aorder?tenant=default` still returns approved v6.

Browser smoke:

- Default `/` opens Workbench, not full-screen graph.
- Workbench shows key findings and attention items.
- Ask bar routes to Question Center with question preserved.
- Finding detail shows supporting evidence, evidence type label, draft-only basis, graph/source context, and follow-up questions.
- Quality panel shows sandbox blocked and missing approved artifacts.
- Question Center creates a scoped draft question task.
- Graph nav keeps Questions pointed at Question Center while scoped reasoning remains a contextual graph action.

Result: local implementation is ready for task #95 validation.

## #95 FAIL Fix

Saskue found a blocking stale-context issue:

- Reproduction before fix: create a Question Center scoped question for `Employee:4`, then open Graph handoff for the same `Employee:4`.
- Actual before fix: Reasoning title/question changed to Graph handoff, but current run/finding evidence still displayed the older Question Center `question_scope` evidence.
- Risk: current reasoning context, latest run, and selected finding could disagree, breaking explanation trust.

Fix:

- `create_scoped_task_from_graph` now includes `source`, `evidence_kind`, center, depth, node limit, and edge limit in scoped task identity.
- Question Center tasks now set `source=question_center` and include a question hash in task identity.
- Graph handoff tasks use `source=graph_explorer` and `graph_node` / `graph_edge` evidence identity.
- Reasoning UI now compares graph handoff evidence signature against latest run/finding evidence.
- If graph handoff has `autorun=1` and latest run evidence does not match the current handoff evidence, the UI reruns the task instead of showing stale evidence. Stale findings are not selected as the current finding.

After-fix evidence:

- Question Center task key:
  - `reasoning:graph-scope:default-question-center-question-scope-employee-4-d1-n200-e200-qf22cfcfeb6`
  - task evidence kind: `question_scope`
- Graph node handoff task key:
  - `reasoning:graph-scope:default-graph-explorer-graph-node-employee-4-d1-n200-e200`
  - task evidence kind: `graph_node`
  - latest run evidence kind: `graph_node`
  - selected finding evidence kind: `graph_node`
- Graph edge handoff continues to produce `graph_edge` evidence and draft-only trace.

Additional fix validation:

```bash
node --check web/review_workbench/reasoning_app.js
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py
```

Real Chrome after-fix smoke:

- Create Question Center scoped question for `Employee:4`.
- Open Graph node handoff URL with `source=graph&evidence_kind=graph_node&autorun=1`.
- Verify `#task-title` is `Scoped reasoning: Employee:4`.
- Verify evidence panel contains `graph_node`.
- Verify evidence panel does not contain `question_scope`.
- Reload URL and verify `graph_node` remains.
- Open Graph edge handoff and verify `graph_edge`, `orders.employeeID`, and `draft_only`.
- Recheck Workbench and Quality smoke.

Result: the #95 blocking stale-context repro is fixed locally and ready for Saskue revalidation.
