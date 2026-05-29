# Schema-To-Graph Hardcode Boundary

This note records the Phase 1 boundary after consolidating schema-to-graph
modeling around `SchemaGraphModelingAgent`.

## Production Path

The production path for deciding graph semantics is:

1. Load raw source tables and collect source schema/profile evidence.
2. Run `SchemaGraphModelingAgent` to produce a structured `GraphModelDraft`.
3. Persist draft ontology artifacts with evidence, confidence, prompt version,
   source refs, and `draft_only_until_human_review` write boundary.
4. Promote only through the existing human review gate.
5. Project approved artifacts into the graph view.

No production path should decide node types, edge types, link types, properties,
or descriptions from hardcoded domain terms alone.

## Legacy And Fixture Paths

| Location | Current status | Allowed use |
| --- | --- | --- |
| `server/graph_projection_fixtures.py` | Historical fixture module | Example/import/bootstrap data source only; not a runtime graph or reasoning fallback |
| `scripts/import_maritime_risk_dataset.py` `OBJECT_SPECS` / `LINK_SPECS` | Curated maritime demo fixture | Repeatable demo seed only; production rebuild uses `scripts/rebuild_maritime_schema_graph.py` |
| `scripts/import_us_iran_war_dataset.py` `OBJECT_SPECS` / `LINK_SPECS` | Curated web-research snapshot fixture | Demo snapshot seed only |
| `scripts/bootstrap_demo_environment.py` seed specs | Demo/test environment fixture | Local bootstrap and tests only |
| `ObjectModelerAgent` / `LinkWeaverAgent` | Legacy adapters | May populate legacy compatibility rows, but artifact writes must flow through `SchemaGraphModelingAgent` |
| `agents/iterative_graph_enrichment_agent.py` term hints | Extraction hints for proposed graph enrichment | May suggest draft/proposed facts from evidence; cannot approve ontology or formal graph data |
| `reasoning_engine.py` `ENTITY_CONFIG` / `LINK_CONFIG` usage | Read-time legacy graph navigation | Must be replaced over time by approved schema-graph projection metadata |

## Migration Rules

- Keep demo fixtures explicit and named as fixtures; do not hide them behind
  production-sounding APIs.
- Do not use demo fixtures as runtime fallback. A tenant without imported data
  and reviewed `SchemaGraphModelingAgent` projection must return empty/degraded
  state and ask for import/model/review, not Employee/Northwind data.
- Prefer reviewed SchemaGraphModelingAgent artifacts over static configs when
  both are available.
- Any new import script must default to raw-table import. Ontology seeding is
  allowed only behind an explicit demo/fixture mode.
- Reasoning can read approved graph projection metadata, but it should not infer
  new schema semantics from `ENTITY_CONFIG` / `LINK_CONFIG`.
- Enrichment can create proposed graph elements with provenance/confidence, but
  it cannot write canonical ontology or formal graph data.

## Deferred Cleanup

- Rename legacy `BusinessObject` / `BusinessLink` compatibility tables and API
  labels in a separate migration so existing rows, tests, and adapters do not
  break silently.
- Move source profiling, metadata scraping, and business-context gathering into
  one schema/profile evidence package that feeds `SchemaGraphModelingAgent`.
- Replace `reasoning_engine.py` static config navigation with approved
  SchemaGraphModelingAgent projection metadata.
- Retire or move old demo-only scripts after their bootstrap/test coverage is
  replaced.
