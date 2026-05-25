# Graph Proposed Batch Review

Task: #192
Date: 2026-05-25

## Scope

Add batch review for Graph `Proposed graph` elements without changing the meaning of approval. Batch approve is a review decision on proposed graph elements only; it does not promote anything into the formal graph and does not approve ontology artifacts.

## Implemented

- Added `POST /api/graph/proposed-elements/batch-review`.
- The API accepts `element_keys`, `action`, optional `reason`, and `reviewer`.
- Supported actions: `approve`, `needs-evidence`, `reject`, `comment`.
- API returns per-item results with `ok`, status/error, and aggregate counts.
- Partial failures are visible in the response instead of being silently swallowed.
- Approval is blocked for elements flagged as requiring ontology proposal/review.
- UI supports selecting proposed node / edge / finding rows, selecting all visible items in the current filter, clearing selection, and applying batch actions.
- UI copy says this is a graph proposal review decision and that formal graph writes remain disabled.

## Boundary

- Batch review writes review events on `aletheia_proposed_graph_elements` only.
- `canonical_write=false`.
- `formal_graph_write=false`.
- It does not write ontology artifacts.
- It does not promote proposed nodes/edges/findings into the formal graph.

## Verification

- Repository-level batch smoke:
  - two selected nodes comment successfully;
  - one valid key plus one missing key returns one success and one failure;
  - approve is blocked when a proposed graph element is flagged `requires_ontology_proposal=true`;
  - original local proposal statuses/payloads were restored after the smoke.
- `py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `tests/test_iterative_graph_enrichment.py`
- `tests/test_ontology_eval.py`
- `git diff --check`
