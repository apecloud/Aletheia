# Auto Enrichment Dedup Evidence UI/API - task #360

## Scope

Expose the deterministic identity-dedup audit trail created by the auto enrichment pipeline in the review surfaces without changing merge, approval, or graph write behavior.

## Changes

- Added `dedup_audit` to proposed graph element API responses.
- Preserved the hard boundary `llm_merge_decision_allowed=false` when present in payload audit data.
- Added Graph Proposed review display for:
  - `candidate_id`, `task_id`, `run_id`, `frontier_id`
  - `dedup_decision`
  - matched node/edge/element key
  - `match_score`
  - `match_evidence`
  - `conflict_fields`
  - `decision_reason`
  - `source_fingerprint`, `evidence_fingerprint`
  - `llm_merge_decision_allowed`
- Added the same dedup audit display to Workspace Work Queue inline review details.
- Added a regression test for audit extraction and false merge-boundary preservation.

## Boundaries

- Display-only change.
- No automatic approval or merge.
- No canonical ontology write.
- No formal graph write.
- No changes to the enrichment candidate identity algorithm from task #351.

## Validation

- `.venv/bin/python -m py_compile server/workbench_server.py tests/test_continuous_enrichment_frontier.py`
- `.venv/bin/python -m unittest tests/test_continuous_enrichment_frontier.py tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py`
- `.venv/bin/python -m unittest discover tests`
- `node --check web/app/api.js`
- `npx esbuild web/app/graph.jsx --bundle --format=iife --global-name=GraphApp --outfile=/tmp/aletheia-graph-task360.js`
- `npx esbuild web/app/workbench.jsx --bundle --format=iife --global-name=WorkbenchApp --outfile=/tmp/aletheia-workbench-task360.js`
- `git diff --check`
- API smoke on port `8876`: `/api/graph/proposed-elements?tenant=maritime-risk&status=all&limit=5` returns `dedup_audit` on each proposed element payload row.
