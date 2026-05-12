# Multi-tenant Implementation Report

Generated: 2026-05-12 13:33:35 CST

## Scope

Implemented tenant/namespace/graph_database as a first-class local configuration boundary for the Review Workbench, Instance Explorer, artifact review APIs, artifact CLI, and graph ingestion routing.

## Tenant model

- Default tenant: `default`
- Default namespace: `northwind`
- Default graph database: `aletheia`
- Demo isolation tenant: `northwind-sandbox`
- Demo isolation namespace: `northwind_sandbox`
- Demo isolation graph database: `aletheia_sandbox`

Tenant metadata is exposed through `GET /api/tenants` and persisted into `aletheia_tenants` when schema migration runs. Local deployments may override tenants with `ALETHEIA_TENANTS_JSON`, `ALETHEIA_TENANTS_FILE`, or `--tenants-file`.

## API and portal behavior

- `?tenant=<tenant_id>` is accepted by Review Workbench and Instance Explorer APIs.
- Artifact list/detail/review/edit operations filter by `project_id = tenant_id`.
- Instance types/search/detail/neighborhood/edge APIs check approved artifacts inside the selected tenant only.
- Portal pages show tenant display name, namespace, and graph database.
- Review Workbench and Instance Explorer links preserve the selected tenant in URL parameters.

## Graph routing

- `agents/graph_ingestion_agent.py` accepts `--tenant` and `--tenants-file`.
- Tenant graph routing resolves `graph_database` to Nebula `graph_space`.
- `scripts/run_graph_ingestion.sh` accepts `--tenant <tenant_id>` and passes it through to the agent.

## Smoke results

- `GET /api/tenants` returns `default` and `northwind-sandbox`.
- `GET /api/artifacts?tenant=default&artifact_type=object` returns 5 default tenant objects.
- `GET /api/artifacts?tenant=northwind-sandbox` returns only sandbox seed artifacts.
- `GET /api/instances/Employee/4/neighborhood?tenant=default&depth=1&limit=200` returns `approved=true`, graph database `aletheia`, 157 nodes, and 156 edges.
- `GET /api/instances/Employee/4/neighborhood?tenant=northwind-sandbox&depth=1&limit=200` returns `approved=false`, 0 nodes, 0 edges, and missing sandbox-approved `object:order` / `link:employee:1:n:order`.

## Verification commands

```bash
.venv/bin/python -m py_compile agents/graph_ingestion_agent.py review_workbench.py query_artifacts.py agents/ontology_artifacts.py agents/tenant_registry.py agents/object_modeler_agent.py agents/link_weaver_agent.py agents/action_synthesizer_agent.py
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
git diff --check
```

