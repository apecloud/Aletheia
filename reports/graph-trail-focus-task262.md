# Graph Trail Focus - Task 262

## Summary

Implemented `Trail focus` for the Graph page so `Hide unrelated` supports path browsing instead of replacing context with a single latest center.

## Behavior

- Clicking a graph node appends it to a visited trail.
- When `Hide unrelated to trail` is enabled, the canvas keeps:
  - every visited trail node,
  - the path edges between visited nodes,
  - the current active node's one-hop neighbors for continued browsing.
- Trail nodes and path edges keep visible labels/highlight, so earlier nodes in the chain remain readable.
- Added `Back` / `Clear trail` controls in the left panel and canvas toolbar.
- `Load full graph` clears hidden mode and resets the trail to the loaded center, restoring full-graph visibility.

## Related regressions fixed

- `maritime-risk / Country` typed center resolution now matches `CHN`, `China`, `中国`, and displayed labels such as `China (CHN)` inside the current tenant/type candidate set.
- Reasoning Chinese mode now renders the top-level `Autopilot` tab as `自动推理`; machine keys such as `CANDIDATE:AUTOPILOT:*` are unchanged.

## Evidence

- Screenshot: `/tmp/task261-graph-china-center-v3.png`
- Graph center smoke: `maritime-risk + Country + China (CHN)` resolves to `Country:China (CHN)` on the page.

## Validation

```bash
npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task261-v4.js --format=iife --log-level=warning
npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task251-tab-v2.js --format=iife --log-level=warning
npx esbuild web/review_workbench/app.jsx web/review_workbench/components.jsx web/review_workbench/workbench.jsx web/review_workbench/graph.jsx web/review_workbench/screens.jsx web/review_workbench/reasoning.jsx --bundle --outdir=/tmp/aletheia-task261-esbuild --format=iife --log-level=warning
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py agents/web_enrichment_agent.py
.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py
git diff --check
```

