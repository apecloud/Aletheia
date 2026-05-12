# Portal Cross-view Navigation Implementation Report

## Scope

- Task #40: add a shared portal shell for Review Workbench and Instance Explorer.
- Task #41: add Workbench to Instance Explorer deep links for approved ontology objects and links.
- Task #42: add Instance Explorer to Workbench return links from node and edge provenance.

## Implementation

- Both `index.html` and `instances.html` now render the same portal shell: product label, Workbench / Instance Explorer navigation, current tenant, namespace, graph database, and breadcrumb.
- Cross-view navigation preserves `tenant` in all generated URLs.
- Review Workbench supports restorable artifact URLs via `?tenant=<tenant>&artifact=<canonical_key>`.
- Instance Explorer supports restorable node and edge URLs:
  - `?tenant=<tenant>&type=Employee&id=4&node=Order%3A10250`
  - `?tenant=<tenant>&type=Employee&id=4&edgeSource=Employee%3A4&edgeTarget=Order%3A10250`
- Instance node and edge detail panels keep provenance return links back to the matching Workbench artifact.

## URLs

- Workbench link artifact: <http://127.0.0.1:8765/?tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder>
- Instance edge: <http://127.0.0.1:8765/instances.html?tenant=default&type=Employee&id=4&edgeSource=Employee%3A4&edgeTarget=Order%3A10250>
- Instance node: <http://127.0.0.1:8765/instances.html?tenant=default&type=Employee&id=4&node=Order%3A10250>
- Sandbox negative gate: <http://127.0.0.1:8765/instances.html?tenant=northwind-sandbox&type=Employee&id=4>

## Validation Commands

```bash
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```

## Acceptance Notes

- This change is navigation-only.
- It does not expand to 2-hop graphs, global graph browsing, IAM, billing, or cross-tenant sharing.
- Sandbox negative gate remains visible through the same portal shell and tenant-preserving navigation path.
