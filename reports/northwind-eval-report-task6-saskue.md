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
