# Graph-aware Crawl Query Implementation - task 228

## Summary

Continuous graph enrichment no longer builds crawl queries from a static ontology template alone. Each frontier item now generates an explainable query plan from the current graph context:

- frontier node/edge key, name, type, ontology type
- proposed graph payload, including source/target labels and relation
- path label from deep graph profile or payload
- risk/trade metrics such as `trade_at_risk_v`
- tenant/domain terms such as maritime chokepoint and shipping disruption

## Example

For frontier edge `CHN depends on Bab el-Mandeb Strait` with path `CHN -> depends_on -> Bab el-Mandeb Strait -> trade_at_risk_v`, the generated query is:

```text
CHN China Bab el-Mandeb Strait CHN depends on Bab el-Mandeb Strait depends_on depends on trade dependency maritime chokepoint trade_at_risk_v trade at risk trade exposure trade disruption shipping disruption trade route risk discover maritime trade exposure
```

The run trace records:

- `query_terms`: grouped countries, nodes, relations, metrics, domain terms, and objective terms.
- `graph_context_used`: frontier key/name/type, neighbor nodes, relation, metrics, ontology type.
- `path_context_used`: path label, source/target label, relation, metrics.
- `excluded_terms`: low-signal terms skipped during query construction.

Blocked/private/non-allowlist sources still only enter skipped audit and now carry the query terms that caused the attempted crawl.

## UI

Workspace Agent compact timeline now shows query terms plus graph/path context for run trace rows, so users can see whether a crawl came from static text or from a graph/path frontier.

## Boundary

No safety boundary changed:

- allowlist/private URL policy still gates crawl before proposal creation
- graph enrichment outputs remain proposed/draft
- no automatic ontology approval
- no canonical ontology writes
- no formal graph writes

## Validation

Commands passed:

```bash
.venv/bin/python -m py_compile agents/iterative_graph_enrichment_agent.py review_workbench.py
.venv/bin/python -m unittest tests/test_iterative_graph_enrichment.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task228.js --format=iife --global-name=WorkbenchTask228 --log-level=warning
```

