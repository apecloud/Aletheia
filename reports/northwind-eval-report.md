# Aletheia Ontology Eval Report

- Golden: `evals/fixtures/northwind_golden.json`
- Actual: `reports/aletheia-artifact-snapshot.json`

## Objects

- Expected: 4
- Actual: 4
- Matched: 3
- Precision: 0.75
- Recall: 0.75
- Missing: [{"name": "Category", "tables": ["categories"]}]
- Extra: [{"name": "Employee", "tables": ["employees"]}]

## Links

- Expected: 3
- Actual: 3
- Matched: 2
- Precision: 0.6667
- Recall: 0.6667
- Missing: [{"source": "Product", "target": "Category", "link_type": "N:1"}]
- Extra: [{"source": "Employee", "target": "Order", "link_type": "1:N"}]
