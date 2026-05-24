# Graph Left Tabs

Task: #186

## Change

- Converted `Approved graph / Proposed graph / Saved views` from plain left-side entries into explicit left-panel tabs.
- Counts now render as tab badges.
- Active tab has a visible accent state, matching the Ontology page's catalog-navigation style while fitting the Graph left column.

## Verification

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`
