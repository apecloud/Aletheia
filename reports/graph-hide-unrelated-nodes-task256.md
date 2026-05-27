# Graph Hide Unrelated Nodes - task #256

## Result
- Graph page already exposes an explicit visibility toggle when a node is selected.
- Left scope panel shows `Hide unrelated nodes` / `Show all graph nodes`.
- Canvas toolbar also exposes the same toggle via the circle visibility icon.
- Default remains full approved tenant graph; hiding is opt-in and clears on tenant switch / clear focus.

## Boundary
- No API or formal graph write behavior changed.
- Toggle only changes client-side visibility and focus rendering.

## Validation
- Graph esbuild passed.
- `node --check web/review_workbench/api.js` passed.
- `.venv/bin/python -m py_compile review_workbench.py` passed.
- Maritime CHN graph API smoke returned `Country:CHN` with `has_CHN=true`.
- Screenshot: `/tmp/task259-graph-center-search-zh.png` shows the visibility control in the Graph scope panel.
