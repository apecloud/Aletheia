# Task #131 Workspace Boundary Cleanup Evidence

Date: 2026-05-21
Owner: @Itachi

## Scope

Workbench / Workspace is now a lightweight Case Inbox instead of an ontology artifact review workspace.

Workspace owns:

- Case title, status, owner, blocker, summary, next action
- cross-Case routing
- links to Reasoning and Ontology

Workspace does not own:

- ontology source schema
- evidence chain
- audit trail
- source refs
- canonical graph ingestion readiness
- ontology approve/reject/needs-changes controls

## Browser Smoke

Service:

```bash
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8772
```

URL:

```text
http://127.0.0.1:8772/?screen=workbench&tenant=default
```

Rendered root contains:

- `Case Inbox`
- `Case routing`
- `What needs attention`
- `Open reasoning`
- `Open ontology basis`
- `Employee 1:N Order`

Negative DOM check on rendered root:

- `Evidence chain`: absent
- `Audit Trail`: absent
- `Source refs`: absent
- `Ingestion eligible`: absent
- `Approve`: absent
- `Reject`: absent
- `Needs changes`: absent
- `Payload (JSON)`: absent
- `Decision history`: absent

## Validation

Passed:

- Chrome rendered DOM smoke for Workspace Case Inbox
- Chrome negative DOM check for ontology governance/review blocks
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py`
- `git diff --check`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
