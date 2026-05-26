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
  tests/test_us_iran_war_import.py
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
| `test_us_iran_war_import.py` | US-Iran impact dataset fixtures |
