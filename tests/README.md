# Aletheia Tests

The test suite is intentionally runnable without live Docker databases or LLM
keys. Tests focus on deterministic data contracts and safety boundaries.

## Fast suite

```bash
python -m unittest \
  tests/test_ontology_eval.py \
  tests/test_web_enrichment.py \
  tests/test_iterative_graph_enrichment.py \
  tests/test_continuous_enrichment_frontier.py \
  tests/test_reasoning_deep_graph.py \
  tests/test_schema_graph_modeling_agent.py \
  tests/test_server_api_contracts.py
```

## Full discovery

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

## Coverage by file

| File | Focus |
| --- | --- |
| `test_ontology_eval.py` | Required/optional ontology evaluation contract |
| `test_web_enrichment.py` | Allowlist/private URL safety, provenance, no canonical writes |
| `test_iterative_graph_enrichment.py` | Proposed graph expansion and multi-hop finding artifacts |
| `test_continuous_enrichment_frontier.py` | Priority frontier, cooldown, graph coverage fallback |
| `test_reasoning_deep_graph.py` | Deep graph reasoning finding/evidence shape |
| `test_schema_graph_modeling_agent.py` | LLM schema-to-graph draft contract, review boundary, table/column traceability gate, and ontology logical consistency check (subclass cycle, domain/range, disjointness) |
| `test_server_api_contracts.py` | Core HTTP API route contracts with fake repositories |
| `test_maritime_risk_benchmark.py` | Maritime-risk multi-hop question benchmark (25 questions, Hit@1/F1/chain metrics) |
| `test_hotpotqa_benchmark.py` | HotpotQA passage-only benchmark (100 questions, no KG tenant, EM/F1 over answer text) |
| `test_hotpotqa_nebula_benchmark.py` | HotpotQA graph stored/queried directly in Nebula (single TAG + single EDGE type, no per-relation tables); includes a live Nebula round-trip test, skipped (not failed) if the cluster isn't reachable |
| `test_relation_description_generator.py` | Rule-based Freebase relation name to natural language description conversion |
