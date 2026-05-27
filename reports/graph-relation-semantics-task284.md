# Graph Relation Semantics - Task 284

## Summary

Implemented the product/modeling split discussed in the channel:

- Base graph facts use `trade dependency`.
- Reasoning/finding explanation uses `risk propagation`.

## Changes

- `TradeDependency:* -> Country:*` plus `TradeDependency:* -> Chokepoint:*` raw fact edges are visually projected as:

  `Country:* --trade dependency--> Chokepoint:*`

- The original `TradeDependency:*` fact node is not deleted. It remains traceable in the connected-edge detail panel as `Trade dependency fact`.
- Reified `TradeDependency:*` nodes are no longer drawn as standalone canvas nodes when they can be represented by a `Country --trade dependency--> Chokepoint` projection. They remain available through the edge detail with source table, source row, ontology artifact, and status.
- `trade dependency` is localized as `贸易依赖`.
- Existing `risk propagation` / `风险传播` aggregation remains unchanged and continues to represent the reasoning layer:

  `TradeDependency + SystemicRiskResult + RiskFinding + MitigationAction`

## Boundaries

- Frontend display/projection only.
- Raw Graph API edges are unchanged.
- No formal graph write.
- No canonical ontology write.
- No proposed graph write.
- No review gate behavior change.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task284.js --format=iife --log-level=warning`
- `git diff --check`
