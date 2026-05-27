# Graph Inspector Connected Edges Scroll - Task 280

## Summary

Implemented the Graph inspector request for the right-side `Connected edges` panel.

## Changes

- The selected node's connected-edge list is now capped at 1000 rendered rows.
- The list has its own vertical scroll region and no longer expands the whole inspector/page.
- The header count shows either the full count or `shown/total` when more than 1000 edges exist.
- A localized note appears when the list is truncated:
  - English: `Showing first 1000 connected edges.`
  - Chinese: `当前展示前 1000 条相连边。`
- Existing edge detail behavior is preserved, including `risk propagation` / `风险传播` aggregate layer detail.

## Boundaries

- Frontend display only.
- No backend graph API change.
- No formal graph write.
- No canonical ontology write.
- No proposed graph / review gate behavior change.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task280.js --format=iife --log-level=warning`
- `git diff --check`
