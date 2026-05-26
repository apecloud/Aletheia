# Enrich Agent Priority Frontier - task 238

## Summary

Continuous enrichment now builds each run cycle from a prioritized frontier queue instead of only replaying stored seeds or falling back to ontology artifacts.

Priority order:

1. `new_graph_node` / `new_graph_edge`: newly proposed graph nodes and paths that have not been enriched yet.
2. `user_question_scope`: active scoped reasoning questions and their center/path context.
3. `reasoning_finding_seed`: reasoning findings and Autopilot candidate findings that imply a path needing more evidence or expansion.
4. `graph_coverage`: cooldown-based graph-wide rotation after higher-priority seeds have been covered.

Every selected frontier item now carries `source_kind`, `priority`, `reason`, and, when applicable, `related_question_key`, `related_finding_key`, or `related_run`.

## Coverage State

Continuous session config now tracks:

- `frontier_state.last_enriched_at`: last enrichment timestamp per frontier key.
- `frontier_state.selected_count`: how many times a frontier key was selected.
- `frontier_state.coverage_cursor`: cycle-level coverage progress.
- `frontier_cooldown_minutes`: default 360 minutes.

This prevents each cycle from starting at the same graph item. If all high-priority items are inside cooldown, the selector falls back to `graph_coverage` items instead of restarting a static ontology template.

## Query Behavior

The actual crawl query generation remains the #228 graph/path-aware planner. Frontier items keep payload/path/relation/metrics, so traces still expose:

- `query_terms`
- `graph_context_used`
- `path_context_used`
- `excluded_terms`

## UI

Workspace Agent now shows a compact `Frontier priority` panel listing queued seeds with source kind, priority, and reason. This is intentionally summary-level; detailed review still happens in Graph/Ontology/Reasoning surfaces.

## Smoke Evidence

Current `maritime-risk` session can select priority frontier items from proposed graph data:

```json
{
  "selected_example": {
    "source_kind": "new_graph_node",
    "priority": 100.0,
    "reason": "new proposed graph node has not been enriched yet"
  },
  "coverage_fallback": "when recent new graph items are in cooldown, graph_coverage items are selected"
}
```

## Boundary

No write boundary changed:

- web/source policy still gates crawl before proposal creation
- suspected ontology remains ontology proposal/review-gated
- graph facts remain proposed graph elements
- reasoning output remains candidate/draft finding
- no canonical ontology writes
- no formal graph writes
- no auto-approve

## Validation

Commands passed:

```bash
.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py tests/test_ontology_eval.py
.venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task238.js --format=iife --global-name=WorkbenchTask238 --log-level=warning
git diff --check
```
