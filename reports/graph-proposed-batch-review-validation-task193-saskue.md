# Graph Proposed Batch Review Validation - Task 193

## Summary

PASS for task #193 on commit `35024af`.

Validated the new Graph proposed batch review flow on:

`http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=proposed`

Scope covered:

- Batch approve for all 11 proposed graph elements.
- Batch comment for only visible nodes.
- Mixed node / edge / finding batch action.
- Partial failure reporting.
- Ontology-review-required blocking.
- Reject / needs-evidence reason guard.
- Canonical ontology and formal graph fingerprints unchanged.

## API Evidence

`POST /api/graph/proposed-elements/batch-review?tenant=maritime-risk`

Validated cases:

- `approve_all_11`: `ok_count=11`, `failed_count=0`
- `mixed_comment`: node + edge + finding, `ok_count=3`, `failed_count=0`
- `partial_failure`: one valid key + one missing key, `ok_count=1`, `failed_count=1`
- `reject_without_reason`: `ok_count=0`, `failed_count=1`
- `ontology_required_block`: one element flagged `requires_ontology_proposal=true` plus one valid element, `ok_count=1`, `failed_count=1`

All successful per-item results returned review events with:

```json
{
  "canonical_write": false,
  "formal_graph_write": false
}
```

Batch response boundary:

```json
{
  "canonical_write": false,
  "formal_graph_write": false,
  "target": "proposed_graph_space",
  "scope": "selected_proposed_graph_elements"
}
```

API evidence file: `/tmp/task193-api-validation.json`

## Browser Evidence

Real browser validation through system Chrome:

- `Select visible` on all 11 elements followed by `Approve selected` shows `11 graph proposal review decisions recorded · formal graph unchanged`.
- `nodes` filter followed by `Select visible` selects 6 node elements and `Comment` records 6 decisions.
- Mixed manual selection of one edge, one finding, and one node records 3 decisions.
- `Needs evidence` without a reason shows `Review reason is required for batch reject / needs evidence.`
- Temporary ontology-review-required flag causes UI partial failure display: `1 recorded, 1 failed · <element_key>`.

Browser artifacts:

- `/tmp/task193-browser-validation.json`
- `/tmp/task193-browser-followup.json`
- `/tmp/task193-ui-approve-all.png`
- `/tmp/task193-ui-nodes-comment.png`
- `/tmp/task193-ui-mixed-comment-followup.png`
- `/tmp/task193-ui-partial-failure-followup.png`
- DOM captures:
  - `/tmp/task193-ui-approve-all-dom.txt`
  - `/tmp/task193-ui-nodes-comment-dom.txt`
  - `/tmp/task193-ui-mixed-comment-followup-dom.txt`
  - `/tmp/task193-ui-partial-failure-followup-dom.txt`

## Boundary Fingerprints

Canonical ontology before and after:

```json
{
  "approved": 15,
  "all": 20,
  "status_counts": {
    "draft": 5,
    "approved": 15
  }
}
```

Formal graph before and after:

```json
{
  "approved": true,
  "nodes": 1,
  "edges": 0,
  "center": null
}
```

No canonical ontology or formal graph write occurred.

## Verification Commands

- `.venv/bin/python /tmp/task193_validate.py`
- `python3 /tmp/task193_browser.py`
- `python3 /tmp/task193_browser_followup.py`
- `.venv/bin/python -m py_compile review_workbench.py`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py tests/test_ontology_eval.py`
- `git diff --check`

## Notes

The existing proposed graph elements were already in `approved` proposal-review status when validation began, due to task #192 smoke. The batch approve flow was still validated by re-approving all 11 elements; review events were recorded and formal graph/canonical boundaries remained unchanged.

Minor polish observation: the panel counter still renders `11 draft` as a proposed-space label even when row-level statuses are `approved`. Row-level status is correct; if product wants the header to reflect review-status counts, it belongs with the follow-up layout/polish work.
