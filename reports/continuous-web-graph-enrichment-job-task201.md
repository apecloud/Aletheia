# Continuous Enrichment Job Run - Task #201

## Run

- Tenant: `maritime-risk`
- Run key: `iterative-graph:maritime-risk:20260525064811:73095`
- Status: `completed`
- Objective: `continuous crawl frontier discover hazard chokepoint country trade action paths`
- Source policy: `zenodo.org` allowlist; non-allowlist sources skipped.

## Counts

- Candidate write attempts: 93
- Unique proposed graph elements returned by run API: 56
- Unique finding elements returned by run API: 3
- Pruned/skipped sources: 3

Type split among returned elements:

- Nodes: 42
- Edges: 11
- Findings: 3

## New Candidate Findings

1. `Bab el-Mandeb Strait risk propagates to CHN, IND, USA`
   - Path: `likelihood_conflict -> Bab el-Mandeb Strait -> CHN, IND, USA -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`
   - Source: `https://zenodo.org/records/13841882/task201-bab-el-mandeb`
   - Confidence: 0.73

2. `Hormuz Strait risk propagates to JPN, KOR`
   - Path: `shipping disruption -> Hormuz Strait -> JPN, KOR -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`
   - Source: `https://zenodo.org/records/13841882/task201-hormuz`
   - Confidence: 0.73

3. `Malacca Strait risk propagates to CHN, JPN, KOR`
   - Path: `likelihood_geopolitical -> Malacca Strait -> CHN, JPN, KOR -> trade_impacted -> Run analyst review on exposed country/chokepoint path`
   - Source: `https://zenodo.org/records/13841882/task201-malacca`
   - Confidence: 0.73

## Skipped Sources

The untrusted `example.org` maritime-risk result was skipped three times, once for each active frontier item, with reason `blocked_domain_not_allowlisted`.

## Boundary

This job used the existing iterative proposed-graph enrichment path. It wrote proposed graph elements only. It did not canonicalize ontology artifacts and did not publish to the formal graph.

## Verification

- CLI job completed successfully.
- API smoke confirmed the run is visible at `/api/graph/proposed-elements?tenant=maritime-risk&run_key=iterative-graph:maritime-risk:20260525064811:73095&limit=120`.
- Returned findings all include explicit multi-hop paths.
