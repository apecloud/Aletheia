# Project README / Requirements / Test Cleanup - Task 245

## Summary

Updated the project onboarding surface so a new developer can install, test, and
start the local demo from the README.

## Changes

- Rewrote `README.md` around the current Aletheia product loop:
  - tenants: `default`, `creditcardfraud`, `maritime-risk`
  - Workspace / Work Queue / Agent / Ontology / Graph / Reasoning concepts
  - install smoke test that does not require Docker or an LLM key
  - full local demo startup on port `8772`
  - current direct links for Workspace, Work Queue, Agent, Ontology, Graph, Proposed graph review, Reasoning, and Settings
  - data import, web enrichment, test case map, CLI inspection, troubleshooting
- Added top-level `requirements.txt`, replacing the missing README target.
- Added `tests/README.md` to document the current deterministic test suite.
- Added `datasets/maritime_web_enrichment_fixture.json` so the README offline web enrichment example points at a real fixture file.

## README Install Self-Test

Executed README install steps in a clean temporary venv:

```bash
rm -rf /tmp/aletheia-readme-venv
python3.11 -m venv /tmp/aletheia-readme-venv
/tmp/aletheia-readme-venv/bin/python -m pip install --upgrade pip
/tmp/aletheia-readme-venv/bin/pip install -r requirements.txt
```

Result: pass.

## Validation

Commands passed:

```bash
docker compose -f docker/docker-compose.yml config
/tmp/aletheia-readme-venv/bin/python -m py_compile review_workbench.py agents/iterative_graph_enrichment_agent.py agents/web_enrichment_agent.py
/tmp/aletheia-readme-venv/bin/python -m unittest discover -s tests -p 'test_*.py'
node --check web/review_workbench/api.js
npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench.js --format=iife --global-name=AletheiaWorkbench --log-level=warning
npx esbuild web/review_workbench/graph.jsx --bundle --outfile=/tmp/aletheia-graph.js --format=iife --global-name=AletheiaGraph --log-level=warning
npx esbuild web/review_workbench/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning.js --format=iife --global-name=AletheiaReasoning --log-level=warning
/tmp/aletheia-readme-venv/bin/python scripts/bootstrap_demo_environment.py
/tmp/aletheia-readme-venv/bin/python review_workbench.py --host 127.0.0.1 --port 8783 --ensure-schema
curl -sS 'http://127.0.0.1:8783/api/tenants'
curl -sS 'http://127.0.0.1:8783/?screen=workbench&tenant=maritime-risk'
git diff --check
```

The temporary 8783 server was stopped after smoke validation.
