# Employee-Employee Evidence Review

## Decision

Add `Employee -> Employee` as an optional Northwind v2 link.

## Evidence

- Artifact snapshot: `reports/aletheia-artifact-snapshot-task6.json`
- Artifact key: `link:employee:1:n:employee`
- Link description: the `Employee` object contains reporting structures, indicating a manager/subordinate relationship.
- Object evidence source: `table:employees`
- Extracted employee columns include `employeeID` and `reportsTo`.
- Loaded source data has 9 employees; 8 rows have non-null `reportsTo`, and all 8 point to an existing `employeeID`.

## Product Handling

- Keep v1 required golden unchanged.
- Include `Employee -> Employee` only in optional/v2 golden.
- Treat future self-links without explicit source evidence as `unexpected_extra` until reviewed.
