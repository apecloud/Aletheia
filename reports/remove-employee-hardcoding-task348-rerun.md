# task #348 rerun: tenant-scoped fallback fixtures

## Summary

The #349 blocker was valid: the explicit demo fallback tenant set still used one global fixture graph config, so `creditcardfraud` could see Northwind/Employee artifact names when no approved SchemaGraph projection was available.

This rerun changes fallback behavior from "tenant is demo, use all fixtures" to tenant-scoped fixture sets:

- `default` and `northwind-sandbox`: Northwind fixture only (`Employee`, `Order`, `Customer`, `Product`, `Category`)
- `creditcardfraud`: fraud safe-view fixture only (`CreditCardTransaction`, `Account`, `Card`, `Merchant`)
- all other tenants: no fixture fallback; return empty/degraded when no approved `SchemaGraphModelingAgent` projection exists

Production `SchemaGraphModelingAgent` projection still has priority. The fallback path is only a visible demo/bootstrap fallback.

## Smoke Results

Server used for validation: `http://127.0.0.1:8874`

- `creditcardfraud&view=all&limit=80`: `approved=false`, `nodes=0`, `edges=0`, missing only fraud safe-view artifacts: `object:credit_card_transaction`, `object:account`, `object:card`, `object:merchant`
- `creditcardfraud` instance types with draft included: `CreditCardTransaction`, `Account`, `Card`, `Merchant`
- `creditcardfraud&type=Employee` search: returns `0` instances and reason `Type Employee is not available in tenant-scoped fallback projection for creditcardfraud`
- `default&type=Employee&id=1`: still works through `projection_source=fallback_fixture`
- `maritime-risk&type=Country&id=CHN`: still uses `projection_source=SchemaGraphModelingAgent`

Negative string checks confirmed the `creditcardfraud` graph/types responses do not contain `object:employee`, `object:order`, or `object:customer`.

## Validation

- `.venv/bin/python -m py_compile server/workbench_server.py tests/test_continuous_enrichment_frontier.py`
- `.venv/bin/python -m unittest tests/test_continuous_enrichment_frontier.py tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py`
- `node --check web/app/api.js`
- `npx esbuild web/app/graph.jsx --bundle --format=iife --global-name=GraphApp --outfile=/tmp/aletheia-graph-check.js`
- `git diff --check`

Note: `agents/iterative_graph_enrichment_agent.py` and `tests/test_iterative_graph_enrichment.py` are unrelated dirty files from task #351 and were not touched by this rerun.
