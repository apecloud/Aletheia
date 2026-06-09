# Aletheia

> A review-gated ontology, graph, and reasoning workspace for legacy data.

Aletheia turns relational datasets into tenant-scoped ontology artifacts, proposed
graph facts, and reviewable reasoning findings. The product loop is:

1. import or connect data
2. extract ontology candidates and graph facts
3. enrich with controlled web/search evidence
4. reason over approved/proposed graph context
5. route ontology, graph, and finding proposals through human review gates

The current demo has three main tenants:

- `default`: Northwind example data
- `creditcardfraud`: fraud discovery and finding approval workflow
- `maritime-risk`: chokepoint, country dependency, enrichment, and multi-hop graph reasoning

Northwind is example/import/bootstrap data only. It is not a runtime fallback:
if a tenant has no imported data and no reviewed `SchemaGraphModelingAgent`
projection, the server and UI should show an empty/degraded state instead of
injecting Employee/Order demo objects.

## What Is In This Repo

| Path | Purpose |
| --- | --- |
| `agents/` | Import, ontology modeling, graph ingestion, web enrichment, and reasoning agents |
| `config/` | Tenant configuration examples |
| `datasets/` | Local sample datasets used by demos and smoke tests |
| `docker/` | Local MySQL, PostGIS, and Nebula Graph compose stack |
| `docs/` | Business context documents consumed by agents |
| `evals/` | Ontology evaluation helpers |
| `reports/` | Generated implementation and validation reports |
| `scripts/` | Dataset import, bootstrap, and pipeline runner scripts |
| `tests/` | Unit and integration-style regression tests |
| `web/app/` | Frontend app for Workspace, Ontology, Graph, Reasoning, and Settings |
| `server/aletheia_server.py` | Local API server and metadata/review backend |
| `review_workbench.py` | Compatibility launcher for legacy server commands; new entrypoint is `server/aletheia_server.py` |
| `query_artifacts.py`, `query_graph.py`, `query_metadata.py` | CLI inspection tools |

## Core Concepts

| Concept | Meaning |
| --- | --- |
| Tenant | Isolated namespace, metadata DB routing, source DB routing, and graph space |
| Ontology artifact | Object/link/action/property candidate or approved ontology element |
| Proposed graph element | Draft graph node, edge, or graph-level finding with provenance |
| Finding | Reviewable reasoning conclusion with evidence and action context |
| Work Queue | Workspace tab for human review of ontology, graph, and finding proposals |
| Agent | Workspace tab for automatic enrichment and reasoning agent settings/history |
| Review gate | The owning approval API for ontology, graph proposals, or findings |

Important boundary: automatic agents may create draft/proposed objects, but they
do not bypass review gates. Canonical ontology writes, formal graph writes, and
finding approvals remain separately controlled.

## Zero-To-Local Workspace

Use this path first on a new machine. It starts with a clean Python
environment, installs dependencies, starts local databases, bootstraps metadata,
runs the server, opens the browser UI, and then runs the deterministic test
suite.

### 1. Create a clean Python environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

If `python3.11` is not available, use any Python 3.11+ interpreter:

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Python 3.9 is not supported because the codebase uses modern type syntax such as
`A | B`.

### 2. Install Python dependencies

```bash
python -m pip install -r requirements.txt
```

`requirements.txt` is the default install set. The legacy split files remain
only for old agent-specific runners:

| File | Current use |
| --- | --- |
| `requirements.txt` | Default local workspace, server, agents, tests, and demos |
| `requirements_metadata.txt` | Legacy metadata scraper subset |
| `requirements_profiler.txt` | Legacy data profiler/object modeling subset |
| `requirements_scraper.txt` | Legacy source scraper subset |
| `requirements_hf_scraper.txt` | Hugging Face dataset scraper experiment |

Prefer `requirements.txt` unless you are deliberately running one of the old
standalone scripts.

### 3. Start local databases

```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts:

- MySQL 8.0 on `127.0.0.1:3306`
- PostGIS 15 on `127.0.0.1:5432`
- Nebula Graph 3.6 on `127.0.0.1:9669`

If Docker is not available, you can still run the fast unit tests below, but the
browser workspace and bootstrap scripts need a reachable metadata/source
database.

### 4. Configure optional LLM access

LLM-backed modeling and reasoning scripts use LiteLLM. Set one API key before
running those scripts:

```bash
export GEMINI_API_KEY="your-key"
# or
export OPENAI_API_KEY="your-key"
```

The deterministic tests, server startup, and most review UI smoke tests do not
require an API key.

### 5. Bootstrap demo metadata

```bash
python scripts/bootstrap_demo_environment.py
```

The bootstrap creates metadata/review tables, registers demo tenants, and seeds
repeatable ontology artifacts for the local review server.

### 6. Start the server

Run this in a separate terminal and keep it running while you use the browser UI:

```bash
python server/aletheia_server.py --host 127.0.0.1 --port 8772 --ensure-schema
```

Open <http://127.0.0.1:8772>.

The legacy launcher still works for old scripts:

```bash
python review_workbench.py --host 127.0.0.1 --port 8772 --ensure-schema
```

Useful direct links:

- Workspace: <http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk>
- Workspace Work Queue: <http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=workqueue>
- Workspace Agent settings/history: <http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents>
- Ontology: <http://127.0.0.1:8772/?screen=ontology&tenant=maritime-risk>
- Graph: <http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk>
- Proposed graph review: <http://127.0.0.1:8772/?screen=graph&tenant=maritime-risk&graph_tab=proposed>
- Reasoning: <http://127.0.0.1:8772/?screen=reasoning&tenant=maritime-risk>
- Settings: <http://127.0.0.1:8772/?screen=settings&tenant=maritime-risk>

### 7. Run backend validation

```bash
python -m py_compile review_workbench.py server/aletheia_server.py agents/iterative_graph_enrichment_agent.py agents/web_enrichment_agent.py
python -m unittest \
  tests/test_ontology_eval.py \
  tests/test_web_enrichment.py \
  tests/test_iterative_graph_enrichment.py \
  tests/test_continuous_enrichment_frontier.py \
  tests/test_reasoning_deep_graph.py \
  tests/test_schema_graph_modeling_agent.py \
  tests/test_us_iran_war_import.py
```

### 8. Check frontend bundles

The repo does not require a checked-in `node_modules` directory. Use `npx` for
one-off bundle checks:

```bash
node --check web/app/api.js
npx --yes esbuild web/app/workbench.jsx --bundle --outfile=/tmp/aletheia-workbench.js --format=iife --global-name=AletheiaWorkbench --log-level=warning
npx --yes esbuild web/app/graph.jsx --bundle --outfile=/tmp/aletheia-graph.js --format=iife --global-name=AletheiaGraph --log-level=warning
npx --yes esbuild web/app/reasoning.jsx --bundle --outfile=/tmp/aletheia-reasoning.js --format=iife --global-name=AletheiaReasoning --log-level=warning
```

## Data Import And Enrichment

### Relational pipeline

```bash
./scripts/load_complex_ecommerce_dataset.sh
./scripts/run_metadata_scraper.sh
./scripts/run_data_profiler.sh
./scripts/run_business_context.sh
./scripts/run_design_modeling.sh
./scripts/run_action_synthesizer.sh
./scripts/run_graph_ingestion.sh all
./scripts/run_semantic_consistency.sh
./scripts/run_ontology_reasoning.sh
```

### Maritime-risk dataset

```bash
python scripts/import_maritime_risk_dataset.py --tenant maritime-risk
```

### Web enrichment

Web enrichment collects external evidence for ontology candidates. It is
draft-only and review-gated:

- writes `WebEnrichment` draft artifacts and `web_source` evidence
- records query, URL, retrieval time, summary, confidence, robots/license risk,
  and field-level provenance
- blocks localhost/private-network URLs, secret-bearing URLs, and domains outside
  the allowlist
- never auto-approves ontology artifacts and never writes the formal graph

Offline fixture mode is the recommended CI path:

```bash
python agents/web_enrichment_agent.py \
  --tenant maritime-risk \
  --artifact object:chokepoint \
  --search-results-json datasets/maritime_web_enrichment_fixture.json \
  --allowed-domain zenodo.org \
  --json
```

Live search must be explicitly enabled and bounded:

```bash
python agents/web_enrichment_agent.py \
  --tenant maritime-risk \
  --artifact object:chokepoint \
  --enable-live-search \
  --allowed-domain zenodo.org \
  --max-artifacts 1 \
  --max-results-per-query 2 \
  --max-crawl-pages 1
```

## Test Case Map

| Test file | What it protects |
| --- | --- |
| `tests/test_ontology_eval.py` | Required/optional ontology evaluation behavior |
| `tests/test_web_enrichment.py` | Web enrichment safety, provenance, and draft-only boundaries |
| `tests/test_iterative_graph_enrichment.py` | Proposed graph expansion, evidence refs, and no canonical writes |
| `tests/test_continuous_enrichment_frontier.py` | Enrich agent frontier priority, cooldown, and graph coverage fallback |
| `tests/test_reasoning_deep_graph.py` | Multi-hop reasoning finding shape and evidence path requirements |
| `tests/test_us_iran_war_import.py` | US-Iran impact dataset import fixtures and graph-reasoning inputs |

Run all current tests:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

## CLI Inspection

```bash
python query_metadata.py --all
python query_artifacts.py
python query_graph.py
```

## Multi-Tenant Configuration

Tenants are loaded from metadata and can be initialized from
`config/tenants.example.json`. The local demo primarily uses:

- `default`
- `creditcardfraud`
- `maritime-risk`

Common override environment variables:

```bash
export ALETHEIA_MYSQL_DB="tenant_raw_data"
export ALETHEIA_PG_DB="aletheia_ontology"
export ALETHEIA_GRAPH_SPACE="tenant_graph_space"
```

## Troubleshooting

- Missing `requirements.txt`: use the top-level `requirements.txt`, not the old
  split files. The split `requirements_*.txt` files are retained only for legacy
  agent-specific installs.
- `ERR_CONNECTION_REFUSED`: ensure `server/aletheia_server.py` or the
  compatibility launcher `review_workbench.py` is running on the port you
  opened, usually `8772`.
- Empty demo pages on a fresh DB: run `python scripts/bootstrap_demo_environment.py`
  and restart the server with `--ensure-schema`.
- Docker ports already in use: either stop the conflicting local service or set
  explicit `ALETHEIA_MYSQL_URL` / `ALETHEIA_PG_URL` values.

## License

Apache 2.0. See `LICENSE`.
