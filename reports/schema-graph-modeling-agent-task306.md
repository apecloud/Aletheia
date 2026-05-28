# Schema Graph Modeling Agent - task #306

## Problem

The current Workbench still contains hardcoded domain terms in `review_workbench.py` and tenant import scripts. That makes maritime concepts such as `RiskFinding`, `TradeDependency`, `SystemicRiskResult`, and `MitigationAction` look like a general graph modeling method when they are actually demo/schema-specific projections. This violates the product principle that Aletheia should infer ontology and graph structure from raw database schema and evidence, not from code-level domain vocabularies.

## Implemented

Added `agents/schema_graph_modeling_agent.py`.

The new agent:

- inspects raw database schema using SQLAlchemy inspector;
- captures table names, columns, primary keys, foreign keys, comments, and FK references;
- builds a generic LLM prompt for schema-to-graph modeling;
- explicitly forbids built-in tenant/domain vocabulary and project demo terms;
- explicitly forbids inventing review/finding/action/insight nodes unless the source schema contains those concepts;
- returns structured `GraphModelDraft` with `node_types`, `edge_types`, `rejected_candidates`, `assumptions`, and `review_boundary`;
- can persist inferred node/edge types as draft ontology artifacts with evidence, confidence, source refs, `llm_inferred=true`, prompt version, and `draft_only_until_human_review` boundary.

Added `tests/test_schema_graph_modeling_agent.py`.

The tests verify:

- source schema inspection captures PK/FK evidence from a generic database;
- the LLM prompt contains the no-hardcoding rules;
- the prompt does not include maritime/demo terms such as `RiskFinding`, `TradeDependency`, `Chokepoint`, or `maritime-risk`;
- persisted artifact specs are LLM-inferred drafts with review boundary.

## Hardcoded Hotspots Still To Migrate

This task introduces the correct generic agent path. The following legacy/demo paths remain and should be migrated next instead of extended:

- `review_workbench.py` `ENTITY_CONFIG` / `LINK_CONFIG`: hardcoded node/link catalog for default, creditcardfraud, and maritime demo graphs.
- `review_workbench.py` maritime-specific graph hydration around center nodes: currently creates `SystemicRiskResult`, `RiskFinding`, and `MitigationAction` from systemic risk rows.
- `scripts/import_maritime_risk_dataset.py` `OBJECT_SPECS` / `LINK_SPECS`: fixture-style ontology seed for maritime demo data.
- `agents/iterative_graph_enrichment_agent.py` tenant-specific parsing and `TradeDependency` hints: acceptable only as a temporary demo playbook, not as the general extraction method.

## Required Next Migration

The next implementation should route new tenants through:

`raw database schema -> SchemaGraphModelingAgent -> draft ontology proposals -> human review -> approved graph projection`

and stop adding new domain concepts directly to `review_workbench.py`.

For existing demos, hardcoded specs should be treated as fixtures or approved ontology seeds only, not as the product algorithm.

## Validation

- `.venv/bin/python -m unittest tests/test_schema_graph_modeling_agent.py`
- `.venv/bin/python -m py_compile agents/schema_graph_modeling_agent.py`
