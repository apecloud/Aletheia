# Reasoning Portal Validation - task #95

Result: FAIL, one blocking integration issue.

Validation target:

- Worktree: `/Users/slc/code/Aletheia-portal-redesign`
- Service: <http://127.0.0.1:8768>
- Baseline PRD: task #89
- Implementation under review: task #90-#94

## Blocking Issue

Graph -> Reasoning handoff can show a stale Question Center evidence run after Question Center creates a scoped question for the same graph center.

This breaks the #89/#95 expectation that Graph selected node/edge -> Reasoning shows the current selected graph scope and evidence context.

### Repro

1. Open Question Center:
   <http://127.0.0.1:8768/questions.html?tenant=default>
2. Create a scoped graph question:
   - Question: `Which evidence supports Employee #4 workload risk?`
   - Scope: `Selected graph node`
   - Center node: `Employee:4`
   - Depth: `1`
   - Limit: `200`
3. Open Graph:
   <http://127.0.0.1:8768/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200>
4. Select `Employee:4`.
5. Open the Inspector `Open scoped reasoning` link, or open the equivalent handoff URL:
   <http://127.0.0.1:8768/reasoning.html?tenant=default&source=graph&question=Explain+this+node%27s+role+in+the+graph&depth=1&limit=200&graph_url=%2Fgraph.html%3Ftenant%3Ddefault%26type%3DEmployee%26id%3D4%26depth%3D1%26limit%3D200%26node%3DEmployee%253A4&evidence_kind=graph_node&evidence_label=Employee%3A4&center_node=Employee%3A4&evidence_summary=Margaret+Peacock&evidence_source_ref=employeeID%3D4&ontology_artifact=object%3Aemployee&autorun=1>

### Actual

The Reasoning page title and task scope are updated to Graph context:

- Title: `Scoped reasoning: Employee:4`
- Question: `Explain this node's role in the graph`
- Task scope API has `evidence_paths[0].kind = graph_node`

But the displayed latest evidence/finding still comes from the previous Question Center run:

```text
Employee:4
question_scope

Question Center scoped task for: Which evidence supports Employee #4 workload risk?

question_center
```

API state confirms the mismatch:

- `task.scope.evidence_paths[0].kind` is `graph_node`
- `latest_run.evidence_paths[0].kind` is `question_scope`
- first displayed finding supporting evidence is also `question_scope`

Screenshot: `/tmp/aletheia-task95-fail-graph-node-stale-evidence.png`

### Expected

After Graph handoff with `source=graph` and `evidence_kind=graph_node`, the Reasoning page should not show a stale Question Center evidence context as the active evidence/finding.

Acceptable fixes:

- On `source=graph&autorun=1`, rerun when the latest run evidence does not match the incoming graph handoff evidence; or
- Display the current task scope evidence as the active context and mark older findings as historical; or
- Use distinct task keys for Question Center scoped questions vs Graph handoff tasks when the question/evidence source differs.

## Passed Before Blocker

The validation reached and passed these checks before stopping at the blocker:

- Default Workbench opens at `/`, not a full-screen graph.
- First screen contains knowledge space/status metrics, key findings, attention items, quick tasks, and recent changes.
- Navigation is reframed as Workbench / Questions / Findings / Evidence / Explore / Quality / Ontology / Runtime / Audit.
- Graph is under Explore and not the default first screen.
- Finding/Evidence detail opens from a key finding and shows conclusion summary, supporting evidence, counter evidence/conflicts, graph path, rule/ontology basis, source links, and follow-up questions.
- Evidence items are typed as `fact`, `hypothesis`, `conflict`, or `missing`.
- Question Center can create a scoped draft task for `Employee:4`.
- Quality panel shows draft findings, low confidence, blocked reasoning, agent policy, and sandbox missing approved artifacts.
- API regression baseline still passes:
  - default graph `approved=true`, 157 nodes / 156 edges
  - Employee #4 order checksum `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`
  - sandbox `approved=false`, 0 nodes / 0 edges, no fallback
  - canonical `link:employee:1:n:order` remains `approved` version 6

## Non-Blocking Observation

The Workbench status module is labeled `Knowledge space` instead of the PRD phrase `Knowledge Status`. The required status content is present, so I do not treat this as blocking.

## Validation Commands Run

```bash
python3 /tmp/task95_validate.py
```

The script is currently intentionally failing at the Graph handoff stale evidence assertion.

