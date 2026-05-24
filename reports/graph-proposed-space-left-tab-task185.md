# Graph Proposed Space Left Tab

Task: #185

## Change

- Moved `Proposed graph space` out of the right Inspector.
- Added left-side Graph catalog tabs:
  - `Approved graph`
  - `Proposed graph`
  - `Saved views`
- `Proposed graph` now behaves like Ontology page catalog navigation: it is a left-side graph-space category, not a selected-node detail panel.
- Right side is reserved for Inspector, connected edges, and scoped reasoning.

## User-Facing Result

Open:

`http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=proposed`

The left panel shows the 11 draft proposed graph elements, run key, 6 node / 4 edge / 1 finding counts, deep finding path, source/provenance/confidence, and canonical-write-disabled boundary.

## Boundary

- Proposed graph elements are still displayed separately from the approved graph canvas.
- Proposed elements remain draft/proposed and do not become canonical graph nodes.
- Tenant-scoped center selection from task #181 remains unchanged.

## Verification

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`
