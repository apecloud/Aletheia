# Full Chinese Localization - task #250

## Scope
- Chinese mode now localizes main navigation, status bar, Workspace Work Queue/Agent, Graph Catalog/Proposed review, Ontology catalog/detail/review, and Reasoning/Finding result surfaces.
- Finding titles, summaries, recommendations, counter-evidence, action labels, status labels, and common graph/country labels render in Chinese where `lang=zh` is active.
- `enrich / enrichment` user-facing terminology is standardized as `信息增益`; `web enrichment` is shown as `网页信息增益`; machine keys such as `web_enrichment_crawl`, API fields, source refs, table names, metrics, and canonical keys remain unchanged.
- Country codes are expanded in Chinese display as readable labels like `China (CHN)` while preserving the original code.
- Follow-up fix after #251 FAIL: Reasoning page chrome now localizes the visible tab labels, question/evidence/review controls, scoped-question form, Autopilot panel labels, Finding Registry filters/actions, and suggested question text in Chinese mode.

## Files changed
- `web/review_workbench/app.jsx`
- `web/review_workbench/components.jsx`
- `web/review_workbench/workbench.jsx`
- `web/review_workbench/graph.jsx`
- `web/review_workbench/screens.jsx`
- `web/review_workbench/reasoning.jsx`

## Validation
- `npx esbuild web/review_workbench/app.jsx web/review_workbench/components.jsx web/review_workbench/workbench.jsx web/review_workbench/graph.jsx web/review_workbench/screens.jsx web/review_workbench/reasoning.jsx --bundle --outdir=/tmp/aletheia-task250-esbuild --format=iife --log-level=warning`
- `npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench-task250.js --format=iife --log-level=warning`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task250.js --format=iife --log-level=warning`
- `npx esbuild web/review_workbench/screens.jsx --bundle --outfile=/tmp/aletheia-screens-task250.js --format=iife --log-level=warning`
- `npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task250.js --format=iife --log-level=warning`
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py agents/web_enrichment_agent.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py`
- `curl -fsS 'http://127.0.0.1:8772/api/graph/context?tenant=maritime-risk&type=Country&id=CHN&view=all&limit=80'`
- `playwright screenshot --channel chrome` for Workspace, Graph, and Reasoning Chinese-mode pages.
- Follow-up: `npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task250-fix2.js --format=iife --log-level=warning`
- Follow-up: `npx esbuild web/review_workbench/app.jsx web/review_workbench/components.jsx web/review_workbench/workbench.jsx web/review_workbench/graph.jsx web/review_workbench/screens.jsx web/review_workbench/reasoning.jsx --bundle --outdir=/tmp/aletheia-task250-260-esbuild --format=iife --log-level=warning`
- Follow-up: Chrome screenshot `/tmp/task250-reasoning-zh-fix2.png` confirms visible Reasoning controls and suggested questions are in Chinese while source identifiers remain unchanged.
- `git diff --check`

## Evidence files
- `/tmp/task250-workspace-zh.png`
- `/tmp/task250-graph-country-zh.png`
- `/tmp/task250-reasoning-zh.png`
- `/tmp/task250-reasoning-zh-fix2.png`
