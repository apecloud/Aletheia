# Graph Trail Neighborhood Expansion - task 270

## Summary

Updated Graph `Hide unrelated to trail` mode so it keeps the context around every visited trail node, not only the current active node.

## Behavior

When hide unrelated mode is enabled:

- Every visited trail node remains visible.
- Every one-hop neighbor of every visited trail node remains visible.
- Edges incident to any visited trail node remain visible.
- Trail nodes, trail-neighbor nodes, and trail-context edges show labels.
- The active node is still highlighted as the current expansion point.
- Back, Clear trail, Show all graph nodes, and Load full graph continue to reset or restore state consistently.

## Root Cause

The previous implementation computed `activeNeighborIds` only from the selected node. After a user clicked from the first node to a neighboring node, the first node stayed in the trail, but its own one-hop context was no longer part of the hidden view. That made path browsing feel like the center kept moving and older context disappeared.

## Fix

- Added `trailNeighborIds`, computed from every node in `trailNodeIds`.
- Added `trailContextEdgeKeys`, containing every edge incident to a visited trail node.
- Hidden mode now filters by `trailIds + trailNeighborIds` and renders only `trailContextEdgeKeys`.
- Hidden mode now shows node labels for trail neighbors and edge labels for all trail-context edges.

## Validation

- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task270.js --format=iife --log-level=warning`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/web_enrichment_agent.py`

## Boundary

This is frontend visualization state only. It does not change graph data, proposed graph review state, canonical ontology, formal graph writes, or reasoning/finding generation.
