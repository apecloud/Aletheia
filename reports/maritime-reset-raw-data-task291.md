# Maritime Reset To Raw Data - Task 291

## Summary

Cleaned generated `maritime-risk` artifacts from automatic crawl, graph enrichment, and Autopilot reasoning so the tenant is back to original imported data plus approved base ontology/graph.

## Deleted generated data

- Proposed graph elements: 330 -> 0
- Iterative graph enrichment runs: 13 -> 0
- Web enrichment proposals: 2 -> 0
- Web enrichment runs: 7 -> 0
- WebEnrichment ontology artifacts: 2 -> 0
- WebEnrichment artifact reviews/evidence: 2 reviews and 4 evidence rows deleted
- Autopilot candidate findings: 27 -> 0
- Autopilot hypotheses: 36 -> 0
- Autopilot sessions: 9 -> 0
- Continuous enrichment sessions: 2 -> 0
- Deep research benchmark runs: 1 -> 0

## Preserved original data

- Approved maritime object ontology artifacts: 8 -> 8
- Approved maritime link ontology artifacts: 7 -> 7
- Source data tables were not modified.
- Approved graph API still returns original imported graph context.

## Smoke validation

- `GET /api/graph/context?tenant=maritime-risk&type=Country&id=CHN&depth=1&limit=100&view=all`
  - approved: true
  - nodes: 232
  - edges: 300
  - center: `Country:CHN`
  - includes `Country:CHN`
  - includes original `TradeDependency:CHN::Bohai Strait`
- `GET /api/graph/proposed?tenant=maritime-risk&limit=10`
  - elements: 0
  - runs: 0
- `GET /api/agent-runs/console?tenant=maritime-risk&limit=20`
  - runs: 0
- `GET /api/artifacts?tenant=maritime-risk&status=draft`
  - draft artifacts: 0

## Boundary

This was a data reset only. No source tables, approved ontology object/link artifacts, formal graph API logic, or review-gate code were changed.
