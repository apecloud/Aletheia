# Reasoning Depth Scope - task #331

## Issue

Task id `61` was created with `depth: 2`, but the reasoning run behaved like depth 1.

Root cause: `ReasoningEngine.analyze(...)` always called `repo.neighborhood(... depth=1, limit=200)`. The scoped task stored `depth: 2`, but run-time analysis did not pass the task scope depth into the engine.

## Change

- `ReasoningEngine.analyze(...)` now accepts `depth` and `limit`.
- Scoped task runners now pass `scope.depth` and `scope.node_limit` into `ReasoningEngine`.
- Source-key maritime country profile now uses `depth >= 2` to compute shared-path second-hop peers:
  - center country -> shared path label/chokepoint -> peer countries.
- Evidence summary includes `depth-2 shared paths` when this second-hop context exists.

## Validation

Direct engine smoke:

- `depth=1`: no `second_hop_paths`.
- `depth=2`: `second_hop_paths` present and includes USA on shared CHN paths.

HTTP smoke after restarting 8772:

- Reran task `reasoning:graph-scope:maritime-risk-question-center-question-scope-country-chn-d2-n200-e200-q67b05e1987`.
- Output summary now includes:
  - `At depth 2, shared-path context adds peer countries...`
  - `Taiwan Strait also connects JPN, KOR, TWN, USA`
  - `Korea Strait also connects KOR, JPN, USA, RUS`
- Evidence path now includes `depth-2 shared paths`.

Commands:

- `.venv/bin/python -m py_compile reasoning_engine.py server/workbench_server.py review_workbench.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py tests/test_schema_graph_modeling_agent.py`
- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-task331.js --format=iife --global-name=AletheiaReasoning --log-level=warning`
- `git diff --check`

Boundary:

- Read-only reasoning/source aggregation.
- No canonical ontology writes.
- No formal graph writes.
- Existing source-key table filtering remains scoped to approved ontology artifact source refs/mapped tables for the current tenant.
