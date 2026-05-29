# Remove Runtime Graph Fallback - task #354

## Scope

Removed the runtime graph/reasoning fallback fixture path from `server/workbench_server.py`.
Northwind remains example data only; it is no longer used as an implicit graph projection for a tenant that has not imported data and approved SchemaGraphModelingAgent artifacts.

## Changes

- Removed the `server.graph_projection_fixtures` import from `workbench_server.py`.
- Removed Workbench's Employee/Order `FALLBACK_CASES` mock data. If the cases API is unavailable or a tenant has no review objects, Workspace now shows empty/degraded state instead of injecting Northwind tasks.
- Removed runtime fallback constants and source-schema maps:
  - `EXPLICIT_DEMO_FALLBACK_TENANTS`
  - `FALLBACK_*`
  - hardcoded `Employee` / `Northwind` projection wiring.
- Simplified `InstanceRepository`:
  - `types`, `search`, `detail`, `neighborhood`, and `full_graph` now use reviewed `SchemaGraphModelingAgent` projections only.
  - A tenant without reviewed projection metadata returns an empty/degraded graph context instead of demo nodes.
  - `reasoning_entity_config` and `reasoning_link_config` now return `{}` / `[]` when no reviewed projection exists.
- Simplified `ReasoningRepository`:
  - removed the implicit Employee #4 default task.
  - all reasoning runs now use scoped graph tasks.
- Removed portal overview probing of `northwind-sandbox`.
- Updated the regression test to assert no tenant receives demo reasoning config when SchemaGraph metadata is absent.
- Updated README and `docs/schema-graph-hardcode-boundary.md` to state that Northwind is example/import/bootstrap data only, never runtime fallback.

## API Smoke

Validation server: `http://127.0.0.1:8875`.

- `creditcardfraud` full graph:
  - endpoint: `/api/graph/context?tenant=creditcardfraud&view=all&limit=80`
  - result: `approved=false`, `nodes=0`, `edges=0`, `projection_source=none`
  - no `Employee`, `Order`, `Customer`, `Northwind`, or `fallback_fixture`.
- `default` scoped Employee request:
  - endpoint: `/api/graph/context?tenant=default&type=Employee&id=1&limit=80`
  - result: `approved=false`, `nodes=0`, `edges=0`, `projection_source=none`
  - no runtime Employee graph is generated.
- `maritime-risk` scoped graph:
  - endpoint: `/api/graph/context?tenant=maritime-risk&type=Country&id=CHN&limit=80`
  - result: `approved=true`, `projection_source=SchemaGraphModelingAgent`, center `Country:CHN`, `25` nodes, `24` edges.
- empty graph expand:
  - `default`: returns `node_key is required when the tenant has no approved graph center`.
  - `maritime-risk`: uses tenant-local `Country:ABW`, `projection_source=SchemaGraphModelingAgent`.

## Validation

- `.venv/bin/python -m py_compile server/workbench_server.py tests/test_continuous_enrichment_frontier.py`
- `.venv/bin/python -m unittest tests/test_continuous_enrichment_frontier.py tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py`
- `.venv/bin/python -m unittest discover tests`
- `node --check web/app/api.js`
- `npx esbuild web/app/graph.jsx --bundle --format=iife --global-name=GraphApp --outfile=/tmp/aletheia-graph-check.js`
- `npx esbuild web/app/workbench.jsx --bundle --format=iife --global-name=WorkbenchApp --outfile=/tmp/aletheia-workbench-check.js`
- `git diff --check`

## #355返修

Saskue's first #355 pass found that server fallback was removed, but Workbench still had an Employee/Order mock fallback when API mock fallback was enabled. This返修 removes that UI runtime fallback and tightens docs so example data cannot be mistaken for production fallback.

Additional rerun checks:

- Static scan: `web/app/workbench.jsx` and `server/workbench_server.py` no longer contain `FALLBACK_CASES`, `reasoningTasks` fallback, `Employee`, `Northwind`, `object:employee`, `object:order`, or `object:customer`.
- Workbench bundle builds without Employee/Order mock data.
- API smoke on `8877`:
  - `creditcardfraud` full graph remains `approved=false`, `0/0`, `scope.projection_source=none`.
  - `default&type=Employee&id=1` remains `approved=false`, `0/0`, `scope.projection_source=none`; the requested type is echoed only in request scope, not used to synthesize data.
  - `maritime-risk Country:CHN` remains reviewed SchemaGraph projection with `25/24`.
  - default Workbench HTML does not contain `Employee`, `Northwind`, or `FALLBACK_CASES`.

## Dirty Worktree Boundary

Unrelated dirty files from @Deidara task #351 were left untouched and must not be staged with this task:

- `agents/iterative_graph_enrichment_agent.py`
- `agents/ontology_artifacts.py`
- `tests/test_iterative_graph_enrichment.py`
