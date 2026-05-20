# Aletheia

> Unveiling the semantic truth hidden within legacy data.

Aletheia is a multi-agent framework that transforms relational databases into business ontologies. It extracts latent semantics from legacy schemas, reconstructs them as typed objects and links in a knowledge graph, and reasons over the result.

## Architecture

```
Source DB (MySQL)  -->  Agent Pipeline  -->  Ontology (PostGIS)  -->  Graph (Nebula)
                                                                        |
                                                                  Reasoning Workspace
```

### Agents

The pipeline is a sequence of Python agents powered by `litellm` + `instructor` (default model: `gemini/gemini-3.1-pro-preview`).

| Phase | Agent | What it does |
|-------|-------|-------------|
| Extract | `metadata_scraper_agent` | Scans DDLs, constraints, comments into PostGIS |
| Extract | `data_profiler_agent` | Profiles distributions to infer semantic types |
| Extract | `business_context_agent` | Aligns tables with business terminology from `./docs/` |
| Extract | `data_scraper_agent` | Ingests CSV/JSON/JSONL from URLs or local paths |
| Model | `object_modeler_agent` | Collapses normalized tables into business objects |
| Model | `link_weaver_agent` | Discovers explicit and implicit relationships |
| Model | `action_synthesizer_agent` | Maps stored procedures/triggers to safe actions |
| Validate | `semantic_consistency_agent` | Checks for contradictions, orphans, missing links |
| Reason | `ontology_reasoning_agent` | Multi-hop graph reasoning over live subgraphs |

Supporting modules: `graph_db_client` (Nebula client), `ontology_artifacts` (artifact schema), `tenant_registry` (multi-tenant routing), `hf_dataset_scraper` (HuggingFace dataset loader).

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for databases)
- Gemini API key

### 1. Start infrastructure

```bash
docker compose -f docker/docker-compose.yml up -d
export GEMINI_API_KEY="your-key"
```

This starts MySQL 8.0 (source data), PostGIS 15 (ontology store), and Nebula Graph 3.6 (knowledge graph).

### 2. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the pipeline

```bash
# Load test data (Northwind/TPC-H style)
./scripts/load_complex_ecommerce_dataset.sh

# Extract
./scripts/run_metadata_scraper.sh
./scripts/run_data_profiler.sh
./scripts/run_business_context.sh

# Model
./scripts/run_design_modeling.sh
./scripts/run_action_synthesizer.sh

# Ingest into graph
./scripts/run_graph_ingestion.sh all

# Validate
./scripts/run_semantic_consistency.sh

# Reason
./scripts/run_ontology_reasoning.sh
```

### 4. Inspect results

```bash
cat run_reasoning_result.md                 # reasoning output
.venv/bin/python query_metadata.py --all    # ontology in PostGIS
.venv/bin/python query_artifacts.py         # artifact status
.venv/bin/python query_graph.py             # graph queries
```

See [EXAMPLE_REASONING_RESULT.md](./EXAMPLE_REASONING_RESULT.md) for a pre-computed sample.

## Multi-Tenant Support

Aletheia isolates tenants across source databases, ontology schemas, and graph spaces. Configure tenants in `config/tenants.example.json` or override per-run with environment variables:

```bash
export ALETHEIA_MYSQL_DB="tenant_b_raw_data"
export ALETHEIA_PG_DB="tenant_b_ontology"
export ALETHEIA_GRAPH_SPACE="tenant_b_graph_space"
```

## Review Workbench

A browser-based workspace for ontology review and reasoning.

```bash
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765>. The workbench connects to PostGIS via `ALETHEIA_PG_URL` or `ALETHEIA_PG_DB` and to the source database via `ALETHEIA_MYSQL_URL` or `ALETHEIA_MYSQL_DB`.

Views:

| Path | Purpose |
|------|---------|
| `/` | Reasoning workspace -- ontology-driven question/evidence/conclusion workflow |
| `/ontology.html` | Browse and review ontology artifacts (objects, links, actions) |
| `/instances.html` | Explore live instance data through approved ontology |
| `/graph.html` | Interactive graph explorer with subgraph navigation |
| `/quality.html` | Data quality and consistency checks |
| `/reasoning.html` | Standalone reasoning session view |
| `/settings.html` | Runtime configuration and tenant management |

For a fresh database, start once with `--ensure-schema` to create the required tables.

## Project Layout

```
agents/          LLM agent implementations
config/          Tenant configuration
datasets/        Sample/test datasets
docker/          docker-compose for MySQL, PostGIS, Nebula
docs/            Business context documents for agents
evals/           Ontology evaluation suite
reports/         Generated reports
scripts/         Pipeline runner scripts
tests/           Test suite
web/             Review workbench frontend
```

## License

Apache 2.0 -- see `LICENSE`.
