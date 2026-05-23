# Bilingual Reasoning Result Display - task #168

## Scope

Implemented a UI language switch for the Reasoning surface so the result layer follows the selected language.

## Delivered

- Added app-level `language` state persisted in `localStorage` and URL query param `lang=en|zh`.
- Added a `Lang` selector in the top bar and language indicator in the status bar.
- Passed language into the Reasoning page.
- Added a Reasoning result display adapter for:
  - current answer / finding conclusion
  - fraud risk summary labels
  - Autopilot session objective, hypothesis titles/rationales, pruned reasons
  - candidate finding title/conclusion
  - evidence kind labels
  - evidence limits
  - recommended action text
  - Approved Finding Registry card title/conclusion
- Preserved source identifiers as-is: tenant ids, artifact keys, source table names, source refs, metrics, and evidence chain field names are not translated.

## Validation

- `node --check web/review_workbench/api.js`
- `python -m py_compile review_workbench.py`
- `python -m unittest tests/test_ontology_eval.py`
- `git diff --check`
- `npx esbuild` JSX parse smoke for `components.jsx`, `reasoning.jsx`, and `app.jsx`

## Notes

This is a display-layer change. It does not mutate stored finding payloads, evidence chains, audit records, canonical ontology, or graph state.
