# Workspace Tab Width Polish - task 235

## Summary

Expanded the Workspace `Work Queue` and `Agent` tab layout by increasing the shared Workspace left work column from `340px` to `510px` (1.5x). Both tabs use the same `.wb` grid, so the change applies consistently to review queue and agent management screens.

## Responsive behavior

Added a Workspace media rule at `max-width: 1180px` so the three-column layout stacks into one column instead of squeezing cards and controls on narrow screens.

## Boundary

This is layout-only. No API, review action, agent run, ontology, graph, or finding write behavior changed.

## Validation

Commands passed:

```bash
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task235.js --format=iife --global-name=WorkbenchTask235 --log-level=warning
npx esbuild web/review_workbench/screens.jsx --bundle --outfile=/tmp/screens-task235.js --format=iife --global-name=ScreensTask235 --log-level=warning
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

Note: local Playwright package is not installed in this checkout, so browser width smoke is left for task #236 validation.
