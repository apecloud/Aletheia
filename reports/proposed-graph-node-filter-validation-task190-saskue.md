# Proposed Graph Node Filter Validation - Task 190

## Summary

PASS after restarting the local `8772` workbench on current commit `5e8507a`.

The original user report was that clicking `nodes` in the Graph page proposed graph scope had no response. I validated with a real browser click path on:

`http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=proposed`

## Evidence

- `nodes` was clicked with real Playwright mouse coordinates.
- The list changed to `Showing node proposals · click an item to review.`
- The review panel opened with `REVIEW SELECTED NODE`.
- The selected node was `Bab el-Mandeb Strait`.
- The selected key was `proposed-graph:maritime-risk:node:1b462fe4bce8b302`.
- The review detail showed source URL, evidence refs, confidence, boundary, review buttons, and review history.

Regression checks:

- `edges` click opened `REVIEW SELECTED EDGE`.
- `findings` click opened `REVIEW SELECTED FINDING`.
- Browser `COMMENT` on the selected node wrote a review event and kept the element `draft`.
- The UI still shows `canonical disabled · formal graph disabled`.

## Boundary

Canonical ontology fingerprint before and after:

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

Formal graph fingerprint before and after:

```json
{
  "approved": true,
  "nodes": 1,
  "edges": 0,
  "center": null
}
```

No canonical ontology or formal graph write occurred.

## Artifacts

- Browser validation JSON: `/tmp/task190-validation-after-restart.json`
- Node screenshot: `/tmp/task190-nodes-after-restart.png`
- Edge screenshot: `/tmp/task190-edges-after-restart.png`
- Finding screenshot: `/tmp/task190-findings-after-restart.png`
- Node comment screenshot: `/tmp/task190-node-comment-after-restart.png`
- DOM captures:
  - `/tmp/task190-nodes-after-restart-dom.txt`
  - `/tmp/task190-edges-after-restart-dom.txt`
  - `/tmp/task190-findings-after-restart-dom.txt`
  - `/tmp/task190-node-comment-after-restart-dom.txt`

## Verification

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/screens.jsx --bundle --format=esm --outfile=/tmp/task190-screens.js`
- Browser smoke through system Chrome
- `git diff --check`

## Note

Before restarting `8772`, the frontend had the latest proposed-graph tab UI but the Python backend process was still stale and returned `Unknown API endpoint` for the proposed-graph review route. Restarting the service against the current repo loaded the route and the browser review action passed.
