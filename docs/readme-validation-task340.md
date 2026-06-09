# README Validation - Task 340

Date: 2026-05-28

## Documentation Changes

- Reworked `README.md` around a zero-to-local-workspace path:
  environment, dependencies, databases, metadata bootstrap, server startup,
  browser links, backend validation, frontend bundle checks, and
  troubleshooting.
- Added explicit `requirements*.txt` purpose table:
  `requirements.txt` is the default install path; split files are legacy
  agent-specific subsets.
- Updated test documentation to include
  `tests/test_schema_graph_modeling_agent.py`.
- Documented that Python 3.9 is not supported because the codebase uses modern
  type syntax such as `A | B`.

## Commands Run

### Fresh Python install

```bash
python3.11 -m venv /tmp/aletheia-readme-venv-340-py311
/tmp/aletheia-readme-venv-340-py311/bin/python -m pip install --upgrade pip
/tmp/aletheia-readme-venv-340-py311/bin/python -m pip install -r requirements.txt
```

Result: passed. The validated interpreter was `Python 3.11.3`.

I also tested the fallback risk with the system `python3` and found it is
`Python 3.9.6`; tests fail there on `A | B` type syntax. README now calls this
out explicitly.

### Local database bootstrap

```bash
docker compose -f docker/docker-compose.yml up -d
/tmp/aletheia-readme-venv-340-py311/bin/python scripts/bootstrap_demo_environment.py
```

Result: passed. Docker reported MySQL, PostGIS, Nebula Graph, and storage/meta
containers running. Bootstrap completed and created metadata/review tables.

### Server startup and smoke

```bash
/tmp/aletheia-readme-venv-340-py311/bin/python server/aletheia_server.py --host 127.0.0.1 --port 8772 --ensure-schema
curl -sS http://127.0.0.1:8772/
curl -sS http://127.0.0.1:8772/api/tenants
```

Result: passed. The page title was `Aletheia App`; `/api/tenants` returned the
registered tenants.

Service URL: <http://127.0.0.1:8772>

### Backend validation

```bash
/tmp/aletheia-readme-venv-340-py311/bin/python -m py_compile review_workbench.py server/aletheia_server.py agents/iterative_graph_enrichment_agent.py agents/web_enrichment_agent.py
/tmp/aletheia-readme-venv-340-py311/bin/python -m unittest tests/test_ontology_eval.py tests/test_web_enrichment.py tests/test_iterative_graph_enrichment.py tests/test_continuous_enrichment_frontier.py tests/test_reasoning_deep_graph.py tests/test_schema_graph_modeling_agent.py tests/test_us_iran_war_import.py
/tmp/aletheia-readme-venv-340-py311/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Result: passed. Full discovery ran 26 tests.

### Frontend validation

```bash
node --check web/app/api.js
npx --yes esbuild web/app/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench-readme340.js --format=iife --global-name=AletheiaWorkbench --log-level=warning
npx --yes esbuild web/app/graph.jsx --bundle --outfile=/tmp/aletheia-graph-readme340.js --format=iife --global-name=AletheiaGraph --log-level=warning
npx --yes esbuild web/app/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning-readme340.js --format=iife --global-name=AletheiaReasoning --log-level=warning
```

Result: passed.

## Known Preconditions

- Use Python 3.11+ for the README path. The macOS system `python3` in this
  environment is Python 3.9.6 and is not sufficient.
- Docker must be available for the full local workspace. Without Docker, use the
  fast unit tests only.
- LLM API keys are optional for deterministic tests and server startup, but
  required for live LLM-backed modeling/reasoning.
