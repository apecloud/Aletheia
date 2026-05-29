# Task 348 - Remove Employee/Northwind production hardcoding

## Result

`server/workbench_server.py` no longer uses `Employee:4`, `Employee -> Order`, or the Northwind fixture as a production default for Graph, Portal, instance search, graph expand, node detail, edge detail, or reasoning schema navigation.

## Changes

- Renamed the remaining fixture schema tables to explicit `FALLBACK_*` names and documented that they are demo/bootstrap fallbacks only.
- Production `types/search/detail/neighborhood/full_graph` now prefer approved `SchemaGraphModelingAgent` artifacts and return no fixture graph for non-demo tenants when reviewed projection metadata is unavailable.
- Graph defaults now resolve a tenant-local approved center from projection metadata instead of falling back to `Employee:4`.
- Node and edge detail are generic projection reads. They no longer special-case `link:employee:1:n:order`.
- Reasoning schema navigation now uses `SchemaGraphModelingAgent` metadata for production tenants. The Northwind workload task is restricted to explicit demo fallback tenants and emits `projection_source=fallback_fixture`.
- Portal quick links now use the tenant default center, not Employee.

## Smoke

- `maritime-risk / Country:CHN` scoped graph: `projection_source=SchemaGraphModelingAgent`, `25` nodes, `24` edges.
- `maritime-risk` graph context without type/id: resolves `Country:ABW`, `projection_source=SchemaGraphModelingAgent`.
- `maritime-risk` instance search without type: resolves through SchemaGraph metadata and returns `Country:CHN` for `q=CHN`.
- `maritime-risk` node detail and edge detail return `projection_source=SchemaGraphModelingAgent`.
- `default / Employee:1` still works only as explicit `projection_source=fallback_fixture`.
- `creditcardfraud / Account` fallback remains gated by approved artifacts and does not auto-create production graph semantics.

## Validation

- `.venv/bin/python -m py_compile server/workbench_server.py reasoning_engine.py review_workbench.py`
- `.venv/bin/python -m unittest tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py`
- `node --check web/app/api.js`
- `npm exec -- esbuild web/app/graph.jsx --bundle --format=iife --global-name=AletheiaGraphApp --outfile=/tmp/task348-graph.js`
- `npm exec -- esbuild web/app/workbench.jsx --bundle --format=iife --global-name=AletheiaWorkbench --outfile=/tmp/task348-workbench.js`
- `git diff --check`
