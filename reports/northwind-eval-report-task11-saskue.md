# Aletheia Ontology Eval Report

- Golden: `/Users/slc/code/Aletheia/evals/fixtures/northwind_golden.json`
- Actual: `reports/aletheia-artifact-snapshot-task6.json`

## Objects

- Expected: 4
- Actual: 5
- Matched: 4
- Precision: 0.8
- Recall: 1.0
- Missing: []
- Extra: [{"name": "Employee", "tables": ["employees"]}]

## Links

- Expected: 3
- Actual: 5
- Matched: 3
- Precision: 0.6
- Recall: 1.0
- Missing: []
- Extra: [{"source": "Employee", "target": "Employee", "link_type": "1:N"}, {"source": "Employee", "target": "Order", "link_type": "1:N"}]

## Required / Optional

- Optional Golden: `evals/fixtures/northwind_optional.json`

### Objects

- Required: 4 / 4
- Required Recall: 1.0
- Required Missing: []
- Optional: 1 / 1
- Optional Recall: 1.0
- Optional Missing: []
- Optional Hit: [{"name": "Employee", "tables": ["employees"]}]
- Unexpected Extra: []

### Links

- Required: 3 / 3
- Required Recall: 1.0
- Required Missing: []
- Optional: 2 / 2
- Optional Recall: 1.0
- Optional Missing: []
- Optional Hit: [{"source": "Employee", "target": "Employee", "link_type": "1:N"}, {"source": "Employee", "target": "Order", "link_type": "1:N"}]
- Unexpected Extra: []
