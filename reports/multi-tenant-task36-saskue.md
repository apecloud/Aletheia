# Task #36 Multi-tenant Validation

## Result
PASS

## Tenant Metadata
- `default`: namespace `northwind`, graph database `aletheia`
- `northwind-sandbox`: namespace `northwind_sandbox`, graph database `aletheia_sandbox`

## Artifact / Review Isolation
- Default artifacts: 10 scoped to `default`
- Sandbox artifacts: 3 scoped to `northwind-sandbox`
- Sandbox review comment was visible only on sandbox artifact, not on default artifact.

## Tenant-scoped Instance Gate
- Default graph: approved=True, graph_database=`aletheia`, nodes=157, edges=156, checksum `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`
- Sandbox graph: approved=False, nodes=0, edges=0, missing=object:order, link:employee:1:n:order
- Sandbox edge endpoint status: 404

## Graph Routing
- `default` routes to graph database `aletheia`; agent graph space `aletheia`; client space `aletheia`
- `northwind-sandbox` routes to graph database `aletheia_sandbox`; agent graph space `aletheia_sandbox`; client space `aletheia_sandbox`

## Checks
- [x] tenants endpoint lists default and sandbox
- [x] default current tenant metadata correct
- [x] sandbox current tenant metadata correct
- [x] default artifacts all scoped to default
- [x] sandbox artifacts all scoped to northwind-sandbox
- [x] artifact result sets differ between tenants
- [x] default has approved employee/order/link
- [x] sandbox employee exists but order/link not approved or absent
- [x] review comment writes only sandbox tenant
- [x] default instance types include Employee and Order
- [x] sandbox instance types do not expose Order when sandbox order not approved
- [x] default search finds Employee 4 Margaret
- [x] sandbox search uses sandbox tenant and does not leak default orders
- [x] default graph positive path 157 nodes / 156 edges
- [x] default graph IDs checksum matches baseline
- [x] sandbox graph negative gate 0 nodes / 0 edges
- [x] sandbox graph reports missing tenant-approved order/link
- [x] default edge resolves with default graph database
- [x] sandbox edge blocked
- [x] routing registry maps distinct graph databases
- [x] GraphIngestionAgent receives tenant graph space
