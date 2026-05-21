# Task #129 Reasoning Boundary Cleanup Evidence

Date: 2026-05-21
Owner: @Itachi

## Scope

Keep Reasoning as the single-Case reasoning detail page:

- question, selected task, run state, evidence, trace, answer/finding, and finding review
- compact ontology basis only
- navigation to Ontology for source schema, review history, canonical state, and impact

Reasoning must not carry full ontology governance blocks.

## Positive Browser Smoke

URL:

```text
http://127.0.0.1:8771/?screen=reasoning&tenant=default
```

Rendered root contains:

- `Ontology basis`
- `Employee 1:N Order`
- `View in Ontology`
- `Compact basis only`

Rendered ontology links include:

```text
/?screen=ontology&tenant=default&artifact=link%3Aemployee%3A1%3An%3Aorder
/?screen=ontology&tenant=default&artifact=object%3Aemployee
/?screen=ontology&tenant=default&artifact=object%3Aorder
```

## Negative Browser Smoke

After stripping scripts and styles from Chrome's rendered DOM, the Reasoning root does not contain the full governance block labels:

- `Raw schema / mapping`: absent
- `Review history`: absent
- `canonical lifecycle`: absent
- `graph readiness`: absent
- `raw source schema`: absent

## Boundary Note

Reasoning still owns evidence chains and finding review for a single Case. It does not own ontology raw source, source schema, canonical schema lifecycle, graph ingestion readiness, or ontology review history. Those are linked out to the Ontology page.

## Validation

Passed:

- Chrome headless browser smoke for Reasoning compact ontology basis
- Chrome headless negative DOM check for governance block labels
- `node --check web/review_workbench/api.js`
- `.venv/bin/python -m py_compile review_workbench.py`
- `git diff --check`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
