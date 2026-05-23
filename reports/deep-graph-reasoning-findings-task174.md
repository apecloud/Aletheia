# Deep Graph Reasoning Findings - Task 174

## Summary

Task #174 optimizes the reasoning/autopilot path for graph multi-hop reasoning. The goal is to make deep findings first-class, not just a flat list of evidence rows.

The implementation adds a structured deep graph profile for candidate findings and approved findings. A finding is emphasized as a `deep_graph_finding` only when its evidence chain includes the full path:

`hazard -> chokepoint -> dependent country -> risk/trade metric -> recommended action`

Volume-only or partial chains remain ordinary candidate findings and show missing path steps.

## Backend Changes

- Added `_deep_graph_profile(evidence_chain)` to classify evidence chains.
- Maritime-risk Autopilot session scope now declares:
  - `reasoning_mode=graph_multi_hop`
  - `finding_emphasis=deep_graph_findings`
  - required path steps.
- Maritime-risk hypotheses now use `graph_path` evidence plans with required path metadata.
- Autopilot candidate API responses include:
  - `deep_graph_profile`
  - `finding_emphasis`
  - observed steps, missing steps, hop count, path labels.
- Approved findings preserve the same deep graph profile inside `recommended_action`, so Registry reuse keeps the graph reasoning semantics.

## UI Changes

Reasoning Autopilot now includes a `Deep graph findings / Multi-hop reasoning focus` panel:

- counts complete multi-hop findings
- counts incomplete graph chains
- shows max hop count
- highlights the required five-step path

Each candidate finding card now shows a graph-path card. Complete findings are labeled `deep graph finding`; incomplete chains show missing steps. This makes it clear why a finding is valuable in deep graph reasoning instead of being a simple ranking.

## Safety Boundary

This task only changes reasoning metadata and UI presentation. It does not change canonical ontology, graph writes, finding approval semantics, or Autopilot auto-promotion rules.

## Validation

Added `tests/test_reasoning_deep_graph.py`:

- complete `hazard -> chokepoint -> dependent country -> risk metric -> recommended action` chain is classified as `deep_graph_finding`
- volume-only chain is not classified as deep graph and reports missing steps

Checks run:

```bash
.venv/bin/python -m py_compile review_workbench.py
.venv/bin/python -m unittest tests/test_reasoning_deep_graph.py tests/test_ontology_eval.py
node --check web/review_workbench/api.js
npx --yes esbuild web/review_workbench/reasoning.jsx --outfile=/tmp/task174-reasoning.js --loader:.jsx=jsx --format=iife
git diff --check
```

API smoke:

```bash
curl -s -X POST \
  'http://127.0.0.1:8784/api/reasoning/autopilot/playbooks/maritime-risk/run?tenant=maritime-risk' \
  -H 'Content-Type: application/json' \
  --data '{"session_key":"autopilot:maritime-risk:task174-smoke","created_by":"task174 smoke"}'
```

The response included:

- session scope `reasoning_mode=graph_multi_hop`
- session scope `finding_emphasis=deep_graph_findings`
- `required_finding_path`
- three candidate findings with `deep_graph_profile.multi_hop=true`
- each candidate has `finding_emphasis=deep_graph_finding` and `hop_count=4`
