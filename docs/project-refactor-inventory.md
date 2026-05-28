# Project Refactor Inventory

This inventory supports Project Refactor Phase 1. It records the current root
and `agents/` files, their observed references, and whether they should be kept,
merged, or reviewed for later migration.

The rule for this phase is conservative: do not delete files without import or
runtime evidence. Production schema-to-graph semantics must flow through
`SchemaGraphModelingAgent` or reviewed equivalent artifacts.

## Root Files

| File | Current role | Evidence | Decision |
| --- | --- | --- | --- |
| `README.md` | Primary install and operating guide | Human-facing entrypoint | Keep and refine in task #340 |
| `CONTRIBUTING.md` | Contribution guide | Standalone documentation | Keep |
| `LICENSE` | License | Required project metadata | Keep |
| `.gitignore` | Repository hygiene | Required project metadata | Keep |
| `.env` | Local operator secrets/config | Local-only; must not be committed | Keep local, never document values |
| `requirements.txt` | Main Python dependency set | README install path | Keep; validate in task #340 |
| `requirements_metadata.txt` | Metadata pipeline dependencies | Legacy metadata pipeline | Keep for now; candidate to fold into extras later |
| `requirements_profiler.txt` | Profiler pipeline dependencies | Legacy profiler pipeline | Keep for now; candidate to fold into extras later |
| `requirements_scraper.txt` | Scraper pipeline dependencies | Legacy scraper pipeline | Keep for now; candidate to fold into extras later |
| `requirements_hf_scraper.txt` | Hugging Face scraper dependencies | `hf_dataset_scraper.py` legacy tool | Merge/delete candidate after deciding HF scraper fate |
| `init_env.sh` | Local environment bootstrap | No current `rg` reference | Keep as optional helper; candidate to replace with README-only runbook |
| `review_workbench.py` | Legacy compatibility launcher | Imports `server.workbench_server`; README labels compatibility | Keep as compatibility shim only |
| `reasoning_engine.py` | Reasoning profile engine | Imported by `server/workbench_server.py` | Keep; review hardcoded config in task #339 |
| `query_artifacts.py` | Artifact inspection CLI | Operator CLI | Keep |
| `query_graph.py` | Graph query CLI | Imports `agents.graph_db_client.NebulaGraphClient` | Keep |
| `query_metadata.py` | Metadata inspection CLI | Operator CLI | Keep |
| `EXAMPLE_REASONING_RESULT.md` | Example output fixture/document | No runtime import | Keep as docs/example, candidate to move under `docs/` |
| `run_reasoning_result.md` | Generated/manual reasoning result sample | No runtime import | Move/delete candidate; should become report artifact or docs example |

## Agent Files

| File | Current role | Evidence | Decision |
| --- | --- | --- | --- |
| `agents/schema_graph_modeling_agent.py` | Unified schema-to-graph modeling contract and draft artifact producer | Imported by `scripts/rebuild_maritime_schema_graph.py`, `ObjectModelerAgent`, `LinkWeaverAgent`, and tests | Keep as production owner |
| `agents/object_modeler_agent.py` | Legacy object grouping adapter | Shell runner `scripts/run_design_modeling.sh`; delegates to `SchemaGraphModelingAgent` | Keep as adapter, migrate callers to unified agent |
| `agents/link_weaver_agent.py` | Legacy link discovery adapter | Shell runner `scripts/run_design_modeling.sh`; delegates to `SchemaGraphModelingAgent` | Keep as adapter, migrate callers to unified agent |
| `agents/ontology_artifacts.py` | SQLAlchemy metadata models and artifact persistence helpers | Imported by modeling/enrichment/tests | Keep |
| `agents/tenant_registry.py` | Tenant config loader | Used by server and agents | Keep |
| `agents/web_enrichment_agent.py` | Web/search enrichment with allowlist and draft proposal boundaries | README, tests, Workspace run APIs | Keep |
| `agents/iterative_graph_enrichment_agent.py` | Proposed graph expansion and benchmark comparison | README, tests, Workspace run APIs | Keep; hardcoded term heuristics reviewed in task #339 |
| `agents/graph_ingestion_agent.py` | Legacy graph ingestion into graph DB | `scripts/run_graph_ingestion.sh` | Keep until graph projection path fully replaces it |
| `agents/graph_db_client.py` | Nebula Graph client abstraction | `query_graph.py` and graph ingestion | Keep |
| `agents/data_scraper_agent.py` | Generic table loader/scraper | Multiple dataset scripts | Keep |
| `agents/metadata_scraper_agent.py` | Legacy metadata extraction | `scripts/run_metadata_scraper.sh` | Keep for old pipeline, candidate to merge into source profiling service |
| `agents/data_profiler_agent.py` | Legacy semantic column profiling | `scripts/run_data_profiler.sh` | Keep for old pipeline, candidate to merge into schema profile stage |
| `agents/business_context_agent.py` | Legacy business context alignment | `scripts/run_business_context.sh` | Keep for old pipeline, candidate to fold into unified modeling prompt context |
| `agents/action_synthesizer_agent.py` | Legacy action catalog generation | `scripts/run_action_synthesizer.sh` | Keep for old pipeline, candidate to replace with finding/action review workflow |
| `agents/semantic_consistency_agent.py` | Legacy semantic validation | `scripts/run_semantic_consistency.sh` | Keep for old pipeline, candidate to convert into eval/test suite |
| `agents/ontology_reasoning_agent.py` | Legacy deep ontology reasoning | `scripts/run_ontology_reasoning.sh` | Keep for old pipeline, candidate to merge with reasoning/workbench path |
| `agents/hf_dataset_scraper.py` | Hugging Face dataset search helper | No active README/test reference found | Delete/move candidate after confirming no operator dependency |

## Keep

- Production server and app: `server/workbench_server.py`, `web/app/`,
  `reasoning_engine.py`.
- Production modeling/enrichment: `SchemaGraphModelingAgent`,
  `ontology_artifacts.py`, `tenant_registry.py`, `web_enrichment_agent.py`,
  `iterative_graph_enrichment_agent.py`.
- Operator CLIs: `query_artifacts.py`, `query_graph.py`, `query_metadata.py`.
- Compatibility shim: `review_workbench.py`, explicitly marked legacy.

## Merge Candidates

- `ObjectModelerAgent` and `LinkWeaverAgent`: keep only as compatibility
  adapters until direct callers use `SchemaGraphModelingAgent`.
- `metadata_scraper_agent.py`, `data_profiler_agent.py`, and
  `business_context_agent.py`: candidates for one source schema/profile context
  package that feeds `SchemaGraphModelingAgent`.
- `semantic_consistency_agent.py`: candidate to become tests/evals rather than a
  separate production agent.
- `ontology_reasoning_agent.py`: candidate to merge into the workbench reasoning
  path after confirming no standalone workflow remains.

## Delete Or Move Candidates

- `agents/hf_dataset_scraper.py`: no active code/test/README reference found.
  Move to `scripts/experiments/` or delete after human confirmation.
- `run_reasoning_result.md`: looks generated; move under `reports/` or delete if
  obsolete.
- `EXAMPLE_REASONING_RESULT.md`: keep if useful, otherwise move under `docs/`.
- `requirements_hf_scraper.txt`: tied to HF scraper; remove with that tool if it
  is retired.

## Risk Notes

- Several legacy agents are still reachable through shell scripts and README
  pipeline commands. They should not be deleted in Phase 1.
- Import-script seed specs and demo/bootstrap fixtures must stay explicitly
  marked as fixture/demo paths; they must not become production schema-to-graph
  decision paths.
- Any future deletion should include `rg` evidence, test impact, and a fallback
  route before removal.
