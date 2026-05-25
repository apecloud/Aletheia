# Proposed Graph Review Validation - task #188

## Scope

User issue: Graph page `nodes / edges / findings` in Proposed graph space were visible but not clickable, so reviewer could not inspect or review proposed graph elements.

## Change

- Added proposed graph element review API:
  - `POST /api/graph/proposed-elements/{element_key}/approve`
  - `POST /api/graph/proposed-elements/{element_key}/reject`
  - `POST /api/graph/proposed-elements/{element_key}/needs-evidence`
  - `POST /api/graph/proposed-elements/{element_key}/comment`
- Added clickable Proposed graph rows/cards in Graph page left tab.
- Added selected element detail panel with:
  - element key/type/status/run
  - source URL, evidence refs, confidence
  - deep graph path/conclusion when present
  - explicit `canonical disabled / formal graph disabled` boundary
  - Approve / Needs evidence / Reject / Comment controls
  - review history

## Smoke Evidence

Isolated service: `<http://127.0.0.1:8784/?screen=graph&tenant=maritime-risk&graph_tab=proposed>`

API smoke on `proposed-graph:maritime-risk:edge:071ed2b5fe353297`:

- `comment` returned HTTP 200.
- Status remained `draft`.
- Review event was appended to payload.
- Response write boundary: `canonical_write=false`, `formal_graph_write=false`, target `proposed_graph_space`.
- Empty `reject` returned HTTP 400 with `Review reason is required for reject or needs evidence`.

Browser smoke:

- Proposed graph tab is visible.
- Clicking `CHN depends on Bab el-Mandeb Strait` opens `Review selected edge`.
- Detail shows Zenodo source URL, evidence, confidence, and canonical/formal graph disabled boundary.
- Approve / Needs evidence / Reject / Comment buttons are present.
- Review history is visible after comment smoke.

Tenant/canonical counts after smoke:

```json
{"approved_artifacts": 15, "all_artifacts": 20, "proposed_elements": 11}
```

## Verification

- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/screens.jsx --bundle --format=esm --outfile=/tmp/task188-screens.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py`
- `.venv/bin/python -m unittest tests/test_web_enrichment.py tests/test_reasoning_deep_graph.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`


## Additional Closure Evidence

Cindy requested three explicit closure artifacts; they are now captured.

Browser node / edge / finding click evidence:

- Node: clicked `Bab el-Mandeb Strait`, opened `Review selected node`; screenshot `/tmp/task188-node-detail.png`, DOM `/tmp/task188-node-detail-dom.txt`.
- Edge: clicked `CHN depends on Bab el-Mandeb Strait`, opened `Review selected edge`; screenshot `/tmp/task188-edge-detail.png`, DOM `/tmp/task188-edge-detail-dom.txt`.
- Finding: clicked `Bab el-Mandeb Strait risk propagates to CHN, IND, USA`, opened `Review selected finding`; screenshot `/tmp/task188-finding-detail.png`, DOM `/tmp/task188-finding-detail-dom.txt`.

Review event write evidence:

```json
{
  "node": {"http_status": 200, "status_after": "draft", "canonical_write": false, "formal_graph_write": false},
  "edge": {"http_status": 200, "status_after": "draft", "canonical_write": false, "formal_graph_write": false},
  "finding": {"http_status": 200, "status_after": "draft", "canonical_write": false, "formal_graph_write": false}
}
```

Canonical/formal graph fingerprint before and after the review-event smoke:

```json
{
  "before": {
    "canonical_ontology": {"approved_artifacts": 15, "all_artifacts": 20, "artifact_status_counts": {"draft": 5, "approved": 15}},
    "formal_graph": {"approved": true, "nodes": 1, "edges": 0, "center": "Chokepoint:Bab el-Mandeb Strait"}
  },
  "after": {
    "canonical_ontology": {"approved_artifacts": 15, "all_artifacts": 20, "artifact_status_counts": {"draft": 5, "approved": 15}},
    "formal_graph": {"approved": true, "nodes": 1, "edges": 0, "center": "Chokepoint:Bab el-Mandeb Strait"}
  },
  "unchanged": true
}
```

Full machine-readable evidence: `/tmp/task188-extra-evidence.json`.
