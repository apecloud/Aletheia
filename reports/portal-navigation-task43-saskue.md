# Task #43 Portal Cross-view Navigation Validation

## Result
PASS

## Verified URLs
- workbench_link: <http://127.0.0.1:8765/?tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder>
- workbench_employee: <http://127.0.0.1:8765/?tenant=default&artifact=object%3Aemployee>
- instance_edge: <http://127.0.0.1:8765/instances.html?tenant=default&type=Employee&id=4&edgeSource=Employee%3A4&edgeTarget=Order%3A10250>
- instance_node: <http://127.0.0.1:8765/instances.html?tenant=default&type=Employee&id=4&node=Order%3A10250>
- sandbox_negative: <http://127.0.0.1:8765/instances.html?tenant=northwind-sandbox&type=Employee&id=4>

## API / Gate Evidence
- Default edge tenant/graph/link: `default` / `aletheia` / `link:employee:1:n:order`
- Default graph: approved=True, nodes=157, edges=156
- Sandbox graph: approved=False, nodes=0, edges=0, missing=object:order, link:employee:1:n:order
- Sandbox edge endpoint status: 404

## Checks
- [x] workbench_link html contains shared portal shell
- [x] instance_edge html contains shared portal shell
- [x] instance_node html contains shared portal shell
- [x] sandbox_negative html contains shared portal shell
- [x] both pages expose tenant switcher and graph database context
- [x] both pages expose Workbench and Instance Explorer nav
- [x] Workbench nav and instance links preserve tenant
- [x] Workbench artifact URL writes tenant and artifact
- [x] Workbench object employee links to Employee instances
- [x] Workbench employee-order link opens edge example
- [x] Instance nav preserves tenant
- [x] Instance node detail returns to ontology artifact with tenant
- [x] Instance edge detail returns to ontology link with tenant
- [x] Instance URL restore supports node
- [x] Instance URL restore supports edgeSource/edgeTarget
- [x] Instance URL updater records tenant, node, edge params
- [x] default edge provenance tenant/default graph
- [x] default graph URL remains positive 157/156
- [x] sandbox negative gate remains blocked through same portal URL
- [x] sandbox edge endpoint remains blocked
- [x] tenant API returns default and sandbox current contexts
