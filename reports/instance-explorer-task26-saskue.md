# Task #26 Instance Explorer Validation

## Result
PASS

## SQL Baseline
- Employee: `Employee:4` / Margaret Peacock
- Source row: `employees.employeeID = 4`
- Join: `orders.employeeID = employees.employeeID`
- Expected ontology link: `link:employee:1:n:order`
- Expected orders: 156
- Checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`
- First 10 order IDs: 10250, 10252, 10257, 10259, 10260, 10261, 10267, 10281, 10282, 10284
- Last 10 order IDs: 11018, 11024, 11026, 11029, 11040, 11044, 11061, 11062, 11072, 11076

## Approved Positive Path
- API returned nodes/edges: 157 / 156
- API returned orders: 156
- API checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`
- No Customer/Product 2-hop nodes returned.
- Employee detail source: `employees` / `employeeID=4`.

## Gate Negative Path
- Temporarily rejected `link:employee:1:n:order`.
- Neighborhood approved flag: `False`
- Nodes/edges: 0 / 0
- Missing approved artifacts: `link:employee:1:n:order`
- Edge endpoint status while rejected: 404
- Restored `link:employee:1:n:order` to approved after negative verification.

## Edge Provenance / Deep Link
- Sample edge `Employee:4->Order:10250` has `orders.employeeID`, join condition `orders.employeeID = employees.employeeID`, and ontology link `link:employee:1:n:order`.
- Edge `Employee:4->Order:11076` also resolves with the same ontology link.
- Static deep-link checks passed for Review Workbench -> Instance Explorer and Instance Explorer -> artifact links.

## Checks
- [x] positive API approved true
- [x] positive order count matches SQL baseline
- [x] positive order IDs exactly match SQL baseline
- [x] positive checksum matches baseline
- [x] positive only Employee/Order nodes
- [x] positive no Customer/Product 2-hop nodes
- [x] positive edge count matches orders
- [x] employee detail source row is employees employeeID=4
- [x] edge provenance source_ref orders.employeeID
- [x] edge provenance join condition
- [x] edge provenance ontology link
- [x] edge target row employeeID=4
- [x] edge 11076 exists and has ontology link
- [x] negative neighborhood blocked
- [x] negative missing approved link reported
- [x] negative edge endpoint blocked
- [x] restore positive still matches checksum
- [x] deep link review_object_employee_to_instances
- [x] deep link review_link_employee_order_to_employee4
- [x] deep link instance_node_to_artifact
- [x] deep link instance_edge_to_artifact
- [x] deep link instances_page_loads_app
