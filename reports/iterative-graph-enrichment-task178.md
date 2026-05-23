# Iterative Graph Enrichment Implementation - task #178

## Scope
Implemented an isolated iterative graph enrichment agent that expands a proposed graph space without writing canonical ontology or the formal graph.

## Delivered
- New metadata tables via `agents/ontology_artifacts.py`:
  - `aletheia_iterative_graph_enrichment_runs`
  - `aletheia_proposed_graph_elements`
  - `aletheia_graph_deep_research_benchmarks`
- New agent: `agents/iterative_graph_enrichment_agent.py`.
- New unit coverage: `tests/test_iterative_graph_enrichment.py`.

## Runtime model
A run starts with ontology artifacts as frontier nodes, issues bounded search queries, applies source policy before extraction, and writes only draft proposed graph elements:

- `node`: proposed ontology/graph node such as Hazard, Chokepoint, Country.
- `edge`: proposed relation such as Hazard raises risk for Chokepoint, Country depends on Chokepoint.
- `finding`: proposed deep graph finding with `hazard -> chokepoint -> dependent country -> risk metric -> recommended action` evidence chain.

Every run stores:
- frontier
- query
- extracted candidate keys
- pruned/skipped source reasons
- safety profile
- budget

## Smoke result
Command used deterministic offline search fixture, tenant `maritime-risk`, frontier `object:chokepoint`, allowed domain `zenodo.org`.

Result:
- run: `iterative-graph:maritime-risk:20260523163823:17969`
- proposed graph elements: 11
- findings: 1
- pruned/skipped: 1 non-allowlist source
- skipped reason: `blocked_domain_not_allowlisted`

Sample proposed elements:
- node: `likelihood_conflict` as Hazard
- node: `Bab el-Mandeb Strait` as Chokepoint
- node: `CHN`, `IND`, `USA` as Country
- edge: `likelihood_conflict -> Bab el-Mandeb Strait`
- edge: `CHN depends on Bab el-Mandeb Strait`
- finding: `Bab el-Mandeb Strait risk propagates to CHN, IND, USA`

Finding path:
`likelihood_conflict -> Bab el-Mandeb Strait -> CHN, IND, USA -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`

## Safety boundary
- canonical ontology writes: disabled
- formal graph writes: disabled
- proposed graph writes: draft only
- private/sensitive URL policy: skip and audit
- non-allowlist public URLs: skipped, no proposed graph element
- baseline deep research: not used as graph fact

## Validation
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py`
- `.venv/bin/python -m unittest tests/test_web_enrichment.py`
- `.venv/bin/python -m unittest tests/test_reasoning_deep_graph.py`

## Provenance and Fingerprint Evidence
Machine-readable evidence is in `reports/iterative-graph-enrichment-task178.json`.

- element types present: `node`, `edge`, `finding`
- elements with `source_url`, `evidence_refs`, and `confidence`: 11 / 11
- skipped non-allowlist source: `https://example.org/untrusted-maritime-risk-task178`
- skipped reason: `blocked_domain_not_allowlisted`
- canonical ontology fingerprint before: `[["object", "draft", 1]]`
- canonical ontology fingerprint after: `[["object", "draft", 1]]`
- proposed graph count before/after: `0 -> 11`
- formal graph write: false
