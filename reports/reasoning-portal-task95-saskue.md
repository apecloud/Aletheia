# Reasoning Portal Validation - task #95

Result: PASS after one blocking fix.

Validation target:

- Worktree: `/Users/slc/code/Aletheia-portal-redesign`
- Service: <http://127.0.0.1:8768>
- Product baseline: task #89
- Implementation tasks: #90, #91, #92, #93, #94

## Fix Revalidation

The first #95 pass failed because Graph handoff could display stale Question Center evidence for the same `Employee:4` scoped task. After the fix, I reran the original failing path:

1. Create a Question Center scoped graph question for `Employee:4`.
2. Open Graph and hand off `Employee:4` to Reasoning.
3. Confirm the current task scope, latest run, and selected finding all use the Graph handoff evidence source.

The fix passes:

- Question Center task key: `reasoning:graph-scope:default-question-center-question-scope-employee-4-d1-n200-e200-qf22cfcfeb6`
- Question Center evidence kind: `question_scope`
- Graph node handoff task key: `reasoning:graph-scope:default-graph-explorer-graph-node-employee-4-d1-n200-e200`
- Graph node consistency:
  - `task.scope.evidence_paths[0].kind = graph_node`
  - `latest_run.evidence_paths[0].kind = graph_node`
  - `findings[0].supporting_evidence[0].kind = graph_node`
- Graph edge handoff task key: `reasoning:graph-scope:default-graph-explorer-graph-edge-employee-4-order-10640-d1-n200-e200`
- Graph edge consistency:
  - `task.scope.evidence_paths[0].kind = graph_edge`
  - `latest_run.evidence_paths[0].kind = graph_edge`
  - `findings[0].supporting_evidence[0].kind = graph_edge`

This resolves the blocker: Question Center history no longer pollutes Graph handoff current context.

## Workbench Home

Default `/` opens the Reasoning Workbench, not a full-screen graph.

Validated first-screen content:

- Knowledge status content: tenant, namespace, graph database, entity count, relation count, finding count, approved-only state, latest update.
- Key findings: rendered as finding cards with status, confidence, scope, evidence count, question, and explanation link.
- Attention items: draft findings, low-confidence findings, sandbox missing approved artifacts, and agent policy issues.
- Quick tasks: Ask a question, Explain a finding, Inspect an entity, View evidence chain, Trace graph path, Check quality issues, Run scoped reasoning.
- Recent changes: recent findings and reasoning runs.

Browser evidence:

- Workbench URL: <http://127.0.0.1:8768/?tenant=default>
- Finding cards: 8
- Attention items: 12
- Recent items: 10
- Screenshot: `/tmp/aletheia-reasoning-portal-task95.png`

Non-blocking note: the status section is labeled `Knowledge space` instead of the PRD phrase `Knowledge Status`, but all required status content is present.

## Finding / Evidence Detail

Validated from a Workbench key finding into `/findings.html`.

The detail page shows:

- Conclusion Summary
- Confidence and review status
- Original question and scope
- Supporting Evidence
- Counter Evidence / Conflicts
- Graph Path
- Rule / Ontology Basis
- Follow-up Questions

Evidence type labels were present. The sampled finding rendered `fact` and `missing` evidence type labels, source-context links, `draft_only` review gate, and ontology link `link:employee:1:n:order`.

## Question Center

Validated `/questions.html?tenant=default`.

Question Center can create a scoped draft reasoning task:

- Scope: selected graph node
- Center node: `Employee:4`
- Depth: 1
- Limit: 200
- Created task key: `reasoning:graph-scope:default-question-center-question-scope-employee-4-d1-n200-e200-qf22cfcfeb6`
- Scope source: `question_center`
- Evidence kind: `question_scope`
- `approved_only = true`
- `review_gate = draft_only`

The task appears in the history list with status/scope/source/depth-limit and links to run detail/findings.

## Quality Panel

Validated `/quality.html?tenant=default`.

The panel shows:

- Draft findings: 23
- Low confidence findings: 16
- Blocked reasoning runs: 0
- Agent policy issues: 2
- Sandbox gate: blocked
- Missing sandbox artifacts: `object:order`, `link:employee:1:n:order`

The sandbox explanation states that the approved-only graph path is blocked instead of falling back to default tenant data.

## Navigation Reframe

Validated primary navigation:

- Workbench
- Questions
- Findings
- Evidence
- Explore
- Quality
- Ontology
- Runtime
- Audit

Graph is under `Explore`, not the default first screen. Ontology review is available under `/ontology.html`; Runtime is under `/settings.html`.

## Graph Handoff Regression

Node handoff:

- Graph selection: `Employee:4`
- Handoff URL includes `source=graph`, `evidence_kind=graph_node`, `center_node=Employee:4`, `autorun=1`
- Reasoning title: `Scoped reasoning: Employee:4`
- Breadcrumb: `Graph node Employee:4`
- Evidence kind consistency: `graph_node` in task scope, latest run, and current finding
- Refresh preserved scoped node context

Edge handoff:

- Graph selection: `Employee:4->Order:10640`
- Handoff URL includes `source=graph`, `evidence_kind=graph_edge`, `center_edge_source=Employee:4`, `center_edge_target=Order:10640`, `ontology_link=link:employee:1:n:order`, `autorun=1`
- Reasoning title: `Scoped reasoning: Employee:4 -> Order:10640`
- Breadcrumb: `Graph edge Employee:4 -> Order:10640`
- Evidence includes `orders.employeeID`
- Evidence kind consistency: `graph_edge` in task scope, latest run, and current finding
- Refresh preserved scoped edge context
- Trace contains `approved_only`, `draft_only`, and `draft_reasoning_artifact`

## Safety Regression

API and UI regressions passed:

- Default graph remains `approved=true`, 157 nodes, 156 edges.
- Employee #4 order checksum remains `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph remains `approved=false`, 0 nodes, 0 edges.
- Sandbox missing approved artifacts remain `object:order` and `link:employee:1:n:order`.
- Sandbox UI renders 0 `[data-node]` and 0 `[data-edge]`.
- Canonical `link:employee:1:n:order` remains `approved` version 6.
- Question Center and Graph handoff only create/run draft-only scoped reasoning tasks; no approve, ingest, or canonical write path was exercised.

## Verification Commands

```bash
python3 /tmp/task95_validate.py
node --check web/review_workbench/portal_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/findings_app.js && node --check web/review_workbench/quality_app.js && node --check web/review_workbench/graph_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/settings_app.js && node --check web/review_workbench/ontology_app.js && git diff --check
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

All commands passed.

## Artifacts

- Detailed JSON evidence: `reports/reasoning-portal-task95-saskue.json`
- Earlier blocking report retained for audit: `reports/reasoning-portal-task95-saskue-fail.md`
- Earlier blocking JSON retained for audit: `reports/reasoning-portal-task95-saskue-fail.json`
- Workbench screenshot: `/tmp/aletheia-reasoning-portal-task95.png`

## Verdict

task #95 passes. The Portal redesign is ready for product acceptance and final commit/push, subject to Jobs/Cindy approval.

