# Schema Graph Modeling Unification Phase 1

Task: #309
Owner: Itachi
Date: 2026-05-28

## Goal

Stop growing separate schema-to-graph decision paths. `SchemaGraphModelingAgent` is now the canonical contract for schema-derived graph ontology drafts. Legacy `ObjectModelerAgent` and `LinkWeaverAgent` remain callable, but Phase 1 routes their outputs into the unified `GraphModelDraft` shape so downstream review can validate one contract.

## Changes

- Added vocabulary-free legacy adapters in `agents/schema_graph_modeling_agent.py`:
  - `draft_from_legacy_object_model(...)`
  - `draft_from_legacy_link_model(...)`
  - `stable_graph_key(...)`
- Updated `ObjectModelerAgent` to expose `unified_modeling_agent = SchemaGraphModelingAgent` and convert LLM object drafts to `GraphModelDraft` before writing legacy rows.
- Updated `LinkWeaverAgent` to expose the same unified agent and convert link drafts to `GraphModelDraft`.
- Moved ontology artifact persistence for those legacy agents through `SchemaGraphModelingAgent.persist_draft_artifacts_in_session(...)`; legacy agents no longer delete object/link ontology artifacts or call their old direct sync helpers.
- Added tests proving legacy object/link outputs are adapted into the unified review-gated contract without maritime/demo vocabulary.

## Boundary

This is a Phase 1 compatibility layer. It does not remove legacy storage tables yet, and it does not change UI/product behavior. Legacy business object/link rows may still be written for compatibility, but ontology artifact writes now go through the unified schema graph modeling contract. It establishes the migration direction:

`MetadataScraperAgent -> DataProfilerAgent -> SchemaGraphModelingAgent -> draft ontology artifacts -> human review gate -> graph projection`

Legacy object/link agents should be treated as adapters until callers are migrated directly to `SchemaGraphModelingAgent`.

## Validation

- `.venv/bin/python -m unittest tests/test_schema_graph_modeling_agent.py`
- `.venv/bin/python -m py_compile agents/schema_graph_modeling_agent.py agents/object_modeler_agent.py agents/link_weaver_agent.py review_workbench.py`
- package import smoke for `agents.object_modeler_agent` and `agents.link_weaver_agent`
