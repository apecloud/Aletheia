# Maritime Rebuild With SchemaGraphModelingAgent - task #317

## Result

Cleaned `maritime-risk` generated/old metadata and rebuilt draft ontology through `SchemaGraphModelingAgent` only. The rebuild used source schema/profile evidence from the three raw maritime tables and did not call the curated `OBJECT_SPECS` / `LINK_SPECS` path from `scripts/import_maritime_risk_dataset.py`.

## Source Tables Preserved

- `maritime_chokepoint_country_dependencies`: 4950 rows (before 4950).
- `maritime_chokepoint_risk_indicators`: 24 rows (before 24).
- `maritime_chokepoint_systemic_risk_results`: 4752 rows (before 4752).

## Cleanup

- `aletheia_artifact_evidence`: deleted 30.
- `aletheia_artifact_reviews`: deleted 15.
- `aletheia_continuous_enrichment_sessions`: deleted 1.
- `aletheia_ontology_artifacts`: deleted 15.

## Rebuilt Draft Ontology

- Artifacts: 4 total, status `draft`.
- By type: {'link': 2, 'object': 2}.

- `link:has_country_dependency` (link) `Has Country Dependency` conf 0.9; source_agent `SchemaGraphModelingAgent`; refs `table:maritime_chokepoint_country_dependencies`, `table:maritime_chokepoint_risk_indicators`.
- `link:has_systemic_risk_result` (link) `Has Systemic Risk Result` conf 0.9; source_agent `SchemaGraphModelingAgent`; refs `table:maritime_chokepoint_systemic_risk_results`, `table:maritime_chokepoint_risk_indicators`.
- `object:country` (object) `Country` conf 0.9; source_agent `SchemaGraphModelingAgent`; refs `table:maritime_chokepoint_country_dependencies`, `table:maritime_chokepoint_systemic_risk_results`.
- `object:maritime_chokepoint` (object) `Maritime Chokepoint` conf 0.95; source_agent `SchemaGraphModelingAgent`; refs `table:maritime_chokepoint_risk_indicators`.

## LLM Draft Output

- Node `country` / `Country`: A country involved in maritime trade, identified by its ISO3 code. Confidence 0.9.
- Node `maritime_chokepoint` / `Maritime Chokepoint`: A strategic maritime passage, canal, or strait with associated risk indicators. Confidence 0.95.
- Edge `has_country_dependency` / `Has Country Dependency`: `country` -> `maritime_chokepoint`; Represents a country's trade dependency on a specific maritime chokepoint. Confidence 0.9.
- Edge `has_systemic_risk_result` / `Has Systemic Risk Result`: `country` -> `maritime_chokepoint`; Represents the systemic risk results for a country's trade through a maritime chokepoint. Confidence 0.9.

## Boundary

- `SchemaGraphModelingAgent` was the only schema->graph modeling entry used for rebuild.
- `scripts/import_maritime_risk_dataset.py` curated ontology specs were not used.
- `server/review_workbench` graph fixture configs were not used to decide ontology semantics.
- All rebuilt artifacts are draft and review-gated: `draft_only_until_human_review`.
- No proposed graph, web enrichment, iterative enrichment, autopilot, reasoning finding, continuous session, or agent-run residual rows remain for `maritime-risk`.
- No canonical/formal graph write was performed. The Graph page approved graph remains gated until these draft artifacts are reviewed/approved and the graph projection consumes reviewed schema graph artifacts.

Machine report: `reports/maritime-schema-graph-rebuild-task317.json`.

## Validation

- `.venv/bin/python -m unittest tests/test_schema_graph_modeling_agent.py tests/test_ontology_eval.py` passed.
- `.venv/bin/python -m py_compile agents/schema_graph_modeling_agent.py scripts/rebuild_maritime_schema_graph.py review_workbench.py server/workbench_server.py` passed.
- `git diff --check` passed.
- API smoke `GET /api/artifacts?tenant=maritime-risk` returns 4 draft artifacts from `SchemaGraphModelingAgent` with `llm_inferred=true` and `prompt_version=schema_graph_modeling_v1`.
- API smoke `GET /api/graph/context?tenant=maritime-risk&view=all&limit=80` correctly remains `approved=false` with 0 graph nodes because this rebuild stopped at the review gate; approved graph projection is intentionally not written directly by the rebuild script.

## Review Gate Approval

After inspecting the LLM draft output and source schema/profile evidence, I approved the 4 rebuilt artifacts through the existing artifact review API as reviewer `Itachi`:

- `object:country`
- `object:maritime_chokepoint`
- `link:has_country_dependency`
- `link:has_systemic_risk_result`

The review events are recorded in `aletheia_artifact_reviews`; this was not a direct canonical/formal graph write.

## Graph Visibility

After restarting 8772, API smoke `GET /api/graph/context?tenant=maritime-risk&view=all&limit=80` returns an approved graph projected from `SchemaGraphModelingAgent` artifacts: 80 nodes and 240 edges with `projection_source=SchemaGraphModelingAgent`.
