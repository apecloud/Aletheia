# Work Queue Inline Review - Task 241

## Summary

Workspace `Work Queue` now supports inline review for proposed draft objects. Selecting an item shows an `Inline review` panel with the owning gate, status, confidence, source/run, evidence refs, path/relation when present, and explicit write boundaries.

## Covered Review Objects

- Ontology proposals: calls the existing ontology artifact review API.
- Proposed graph nodes / edges / findings: calls the existing proposed graph review API.
- Autopilot candidate findings: calls the existing Autopilot candidate finding review API.

Deep links are still available as secondary actions:

- `Open in Ontology`
- `Open in Graph`
- `Open in Reasoning`

## Boundary

- No second review state machine was added.
- Inline actions call the original owning review gate.
- Graph inline review remains scoped to `proposed_graph_space` and returns `canonical_write=false / formal_graph_write=false`.
- Ontology proposals still require ontology review before canonical ontology use.
- Candidate finding approval still goes through the evidence-chain review gate and cannot bypass Autopilot finding rules.

## Validation

Commands passed:

```bash
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task241.js --format=iife --global-name=WorkbenchTask241 --log-level=warning
npx esbuild web/review_workbench/screens.jsx --bundle --outfile=/tmp/screens-task241.js --format=iife --global-name=ScreensTask241 --log-level=warning
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile review_workbench.py
.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py
git diff --check
```

API smoke:

- Graph proposed edge comment via proposed graph review API returned status `draft` with `canonical_write=false`, `formal_graph_write=false`, `target=proposed_graph_space`.
- Ontology WebEnrichment draft comment via artifact review API preserved `status=draft` and canonical graph ingestion ineligible.
- Autopilot candidate finding comment via candidate review API preserved the candidate review gate and evidence chain.

