# Graph Explorer Left Rail Restore - Task 288

## Summary

Restored the Graph page left rail to be a Graph Explorer surface only.

## Changes

- Removed the `Automatic runs moved` / `Open Workspace agents` notice from the Graph left rail.
- Legacy links with `graph_tab=runs` still normalize back into Graph tabs, but no Agent Runs or Workspace Agent UI is rendered inside Graph.
- Existing Graph Explorer interactions remain available:
  - `Approved graph`
  - `Proposed graph`
  - `Saved views`
  - center type / center search
  - `Use typed center`
  - `Focus center in full graph`
  - `Load full graph`
  - current scope / trail controls

## Boundary

- Graph page is for graph exploration and proposed graph review.
- Automatic crawl/enrichment/reasoning agents remain managed in Workspace Agent.
- No backend API change.
- No graph data or review-gate write behavior change.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task288.js --format=iife --log-level=warning`
- `git diff --check`
- Static text check confirmed Graph no longer contains:
  - `Automatic runs moved`
  - `Open Workspace agents`
  - `Crawl, enrichment`
  - `自动运行已迁移`
  - `打开 Workspace Agent`
