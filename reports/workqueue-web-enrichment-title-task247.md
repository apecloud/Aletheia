# Work Queue Web Enrichment Title Cleanup - Task 247

## Summary

Fixed duplicate-looking ontology proposal names in Workspace / Agent output cards.

The old UI rendered both maritime-risk web enrichment proposals as
`Web enrichment for Chokepoint`, even though they came from different evidence
summaries. That made the Work Queue/Agent output list look like duplicated rows
and gave reviewers no clue which proposal they were opening.

## Changes

- `web/review_workbench/api.js`
  - Normalizes `WebEnrichment` artifact display titles from payload evidence:
    `target enrichment · proposed evidence summary`.
  - Adds a source-aware description using source title, retrieved date, and
    search query.
  - Existing rows now display distinct titles without requiring a DB migration.
- `agents/web_enrichment_agent.py`
  - Future WebEnrichment artifacts are created with evidence-specific names
    instead of the generic `Web enrichment for <artifact>`.

## Example

Before:

- `Web enrichment for Chokepoint`
- `Web enrichment for Chokepoint`

After:

- `Chokepoint enrichment · Maritime chokepoint disruption risk data released under CC-BY-4.0; incl…`
- `Chokepoint enrichment · CC-BY-4.0 maritime chokepoint risk data`

## Validation

Passed:

```bash
node --check web/review_workbench/api.js
.venv/bin/python -m py_compile agents/web_enrichment_agent.py
.venv/bin/python -m unittest tests/test_web_enrichment.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench-task247.js --format=iife --global-name=AletheiaWorkbench --log-level=warning
git diff --check
```

Live normalization smoke against 8772:

```json
{
  "count": 2,
  "duplicateTitleCount": 0
}
```

Note: the worktree already had unrelated in-progress changes in
`review_workbench.py`, `web/review_workbench/api.js`, and
`web/review_workbench/graph.jsx`; this task stages only the WebEnrichment title
cleanup hunks and leaves the unrelated changes untouched.
