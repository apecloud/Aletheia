# Graph Deep Reasoning Benchmark - task #179

## Scope
Implemented a reproducible benchmark that compares Aletheia graph-based deep findings against an imported mainstream-AI-style deep research baseline.

This implementation does not claim that a mainstream AI was called. The baseline is an explicit comparison artifact provided as JSON, so the benchmark is repeatable and auditable.

## Delivered
- `GraphDeepResearchBenchmark` in `agents/iterative_graph_enrichment_agent.py`.
- Persistent benchmark table: `aletheia_graph_deep_research_benchmarks`.
- CLI support: pass `--baseline-json` to run benchmark immediately after iterative enrichment.
- Unit coverage in `tests/test_iterative_graph_enrichment.py`.

## Comparison dimensions
- traceability
- multi-hop path completeness
- coverage
- hallucination risk
- updateability
- reviewer actionability

## Smoke benchmark
Input proposed graph run:
`iterative-graph:maritime-risk:20260523163823:17969`

Benchmark key:
`graph-benchmark:maritime-risk:iterative-graph-maritime-risk-20260523163823-17969:bda0ac8ea0d5f80c`

Summary:
- Aletheia complete deep graph findings: 1
- Baseline claim count: 3
- Graph finding count: 1
- Baseline is not accepted as fact source: true

Observed difference:
- Aletheia scores higher on explicit multi-hop path completeness because every kept finding must include the full graph path.
- Baseline can cover broader context, but its claims are not written into proposed/canonical graph.
- Aletheia keeps lower hallucination risk by dropping claims without evidence-chain structure.

## Write boundary
- baseline writes to proposed graph: false
- baseline writes to canonical graph: false
- benchmark persists only comparison JSON and graph finding references

## Validation
- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py`
- deterministic smoke run with `/tmp/aletheia-task178/search.json` and `/tmp/aletheia-task178/baseline.json`

## Machine-readable Evidence
Machine-readable comparison output is in `reports/graph-deep-research-benchmark-task179.json`.

Boundary assertions:
- `baseline_is_comparison_artifact_only=true`
- `baseline_writes_to_proposed_graph=false`
- `baseline_writes_to_canonical_graph=false`

Aletheia finding evidence path:
`likelihood_conflict -> Bab el-Mandeb Strait -> CHN, IND, USA -> trade_at_risk_v -> Run analyst review on exposed country/chokepoint path`
