# Graph Center Manual Search - task #259

## Result
- Added a manual center input action to Graph center selection: `Use typed center` / `使用输入的中心节点`.
- The input resolves values inside the current tenant instead of depending on the short visible dropdown list.
- Country aliases now resolve common input forms such as `CHN`, `China`, and `中国` to `Country:CHN`.
- Existing dropdown selection still works; typed input does not fall back across tenants.
- Follow-up fix after #260 FAIL: unknown inputs now show an explicit no-match message and clear the typed center instead of displaying a success state.
- Follow-up fix after #260 FAIL: typed resolution must match the current tenant and current type; `default + Employee + CHN` no longer becomes `Employee:China (CHN)`.

## Boundary
- Manual resolution only sets the Graph center type/id for the current tenant and then reuses the existing `Load full graph` path.
- No ontology, proposed graph, or formal graph writes are introduced.

## Validation
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task256-259.js --format=iife --log-level=warning`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py`
- Graph API smoke: `maritime-risk&type=Country&id=CHN&view=all&limit=80` returned center `Country:CHN`, `has_CHN=true`, 81 nodes, 94 edges.
- Chrome screenshot: `/tmp/task259-graph-center-search-zh.png`.
- Follow-up Chrome screenshots: `/tmp/task260-maritime-country-fix.png`, `/tmp/task260-default-chn-fix.png`.
- `git diff --check`
