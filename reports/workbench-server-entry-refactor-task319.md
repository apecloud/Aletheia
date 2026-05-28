# Workbench Server Entry Refactor - task #319

## Scope

- Renamed the large local workbench server implementation from `review_workbench.py` to `server/workbench_server.py`.
- Kept `review_workbench.py` as a compatibility launcher so existing scripts and operator habits still work.
- Isolated legacy demo graph projection constants into `server/graph_projection_fixtures.py` instead of keeping tenant/domain fixtures in the server entry file.
- Updated README and frontend comments to point to the renamed workbench server entry.

## Boundary

- No API route was intentionally renamed.
- The backend `/api/*` surface remains served by the same handler logic.
- Legacy projection fixtures remain available only as compatibility/demo fixtures; production schema-to-graph modeling should continue through `SchemaGraphModelingAgent`, draft artifacts, provenance, and review gates.

## Verification

- `.venv/bin/python -m py_compile review_workbench.py server/workbench_server.py server/graph_projection_fixtures.py agents/ontology_artifacts.py agents/iterative_graph_enrichment_agent.py agents/web_enrichment_agent.py agents/schema_graph_modeling_agent.py`
- `.venv/bin/python -m unittest tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py`
- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench-task319.js --format=iife --global-name=AletheiaWorkbench --log-level=warning`
- `npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph-task319.js --format=iife --global-name=AletheiaGraph --log-level=warning`
- `npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task319.js --format=iife --global-name=AletheiaReasoning --log-level=warning`
- `git diff --check`
- `python server/workbench_server.py --help`
- `python review_workbench.py --help`
- Restarted 8772 using `server/workbench_server.py`.
- Smoke checked `/api/tenants`, `/api/graph/context`, and the workbench HTML route.
