# Graph Extraction Optimization Review - task #294

## Verdict

PASS.

#293 moves crawled evidence extraction from loose keyword-only proposed graph items to a structured extraction contract:

- `ontology_candidates` are emitted for object/link schemas with `review_required=true`.
- Proposed graph `node` items now include typed `ontology_type`, description, properties, evidence quote, source URL, confidence, and review boundary.
- Proposed graph `edge` items now include typed `source_type`, domain relation, typed `target_type`, description, properties, metrics, evidence quote, source URL, confidence, and review boundary.
- Country/chokepoint base facts use `trade_dependency` instead of generic `depends_on`; the reified `TradeDependency:*` fact is retained as `fact_node_hint` for source row / metric / provenance traceability.
- Deep findings still require a full hazard -> chokepoint -> dependent country -> risk metric -> recommended action path.

## Review Evidence

I ran an isolated SQLite smoke, not the live maritime database. The run generated:

- 6 typed nodes
- 4 typed edges
- 3 `trade_dependency` edges
- 1 deep graph finding
- 1 skipped non-allowlist source

Sample node:

- type: `Chokepoint`
- label: `Bab el-Mandeb Strait`
- description present
- properties include `canonical_id_hint`, `domain`, `source_title`, `source_url`
- evidence quote present
- ontology candidate has `review_required=true`
- extraction boundary says `canonical_ontology_write=false`, `formal_graph_write=false`

Sample edge:

- `Country:CHN --trade_dependency--> Chokepoint:Bab el-Mandeb Strait`
- metrics: `trade_at_risk_v`, `trade_impacted`
- `fact_node_hint=TradeDependency:CHN::Bab el-Mandeb Strait`
- source URL and evidence quote present
- relation ontology candidate has `review_required=true`

Boundary check:

- ontology artifact count stayed `1 -> 1`
- canonical writes remained disabled
- formal graph writes remained disabled
- generated output stayed in proposed graph space in the temp DB

Evidence file: `/tmp/task294-structured-extraction-review.json`.

## Notes

This is still a bounded deterministic extraction pass, not a general open-domain LLM extractor. For the current maritime-risk flow it is good enough because it now outputs typed nodes/edges with properties, descriptions, provenance, confidence, and review boundaries. If we later require property-level ontology governance as standalone review artifacts, that should be a separate follow-up rather than a blocker for #293.

## Validation

- `.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py`
- `.venv/bin/python -m unittest tests/test_continuous_enrichment_frontier.py tests/test_ontology_eval.py`
- `.venv/bin/python -m py_compile agents/iterative_graph_enrichment_agent.py review_workbench.py`
- `node --check web/review_workbench/api.js`
- `git diff --check`
