# Schema Graph Modeling Hardcode Inventory

Task: #311
Owner: Itachi
Date: 2026-05-28

## Summary

There are still hardcoded schema-to-graph paths. They should be treated as demo fixtures or legacy compatibility, not as the Aletheia modeling method. The correct method is: use an LLM schema graph modeling agent to infer node types, edge types, properties, descriptions, evidence, confidence, and review boundary from raw schema/profiling evidence.

## Inventory

| Area | Location | Current hardcoding | Migration |
| --- | --- | --- | --- |
| Source link schema registry | `review_workbench.py` `SOURCE_LINK_SCHEMAS` | Hardcoded Northwind, creditcardfraud, maritime, and US-Iran table/field/link mappings such as `Country -> Chokepoint`, `RiskFinding -> RiskIndicator`, `ConflictEvent -> EconomicChannel`. | Replace with approved `link` ontology artifacts produced by `SchemaGraphModelingAgent`; keep only as demo bootstrap fixtures. |
| Graph entity config | `review_workbench.py` `GraphRepository.ENTITY_CONFIG` | Hardcoded entity types, source tables, primary keys, label columns, and artifact keys, including `Chokepoint`, `TradeDependency`, `SystemicRiskResult`, `RiskFinding`, `MitigationAction`. | Generate from approved object artifacts and schema projection specs. UI/API should read artifacts, not Python constants. |
| Graph link config | `review_workbench.py` `GraphRepository.LINK_CONFIG` | Hardcoded FK join paths for Northwind and creditcardfraud; maritime links are then special-cased separately. | Generate from approved link artifacts, FK evidence, and reviewed join specs. |
| Maritime graph hydration | `review_workbench.py` maritime branches in `full_graph` | Hardcoded creation of `TradeDependency`, `SystemicRiskResult`, `RiskFinding`, `MitigationAction`, hazard/risk edges from specific maritime tables. | Move to reviewed graph projection specs. `RiskFinding` should only appear when a reviewed ontology/policy defines it, not because source column names match. |
| Tenant-specific playbooks | `review_workbench.py` `run_creditcardfraud_autopilot_playbook` and `run_maritime_risk_autopilot_playbook` | Finding candidates are created from tenant-specific hardcoded hypotheses and metrics. | Keep as named demo playbooks only. General reasoning should consume graph paths and ontology contracts. |
| Maritime import ontology seed | `scripts/import_maritime_risk_dataset.py` `OBJECT_SPECS` / `LINK_SPECS` | Seeds `Chokepoint`, `TradeDependency`, `SystemicRiskResult`, `RiskFinding`, `MitigationAction` and link specs directly. | Treat as fixture/import demo. Default path should import raw source tables, then invoke schema graph modeling agent to produce draft ontology. |
| Web/iterative extraction terms | `agents/iterative_graph_enrichment_agent.py` `COUNTRY_ALIASES`, `METRIC_TERMS`, `RELATION_TERMS`, `_extract_graph_semantics` | Hardcoded maritime extraction terms and relation construction (`trade_dependency`, `raises_risk_for`, `TradeDependency:*`). | Replace extraction heuristics with an LLM extraction contract constrained by approved ontology + source evidence. Fixed aliases can remain as optional normalizers, not ontology decisions. |

## Migration Order

1. Route new schema-to-graph jobs through `SchemaGraphModelingAgent`.
2. Add a reviewed graph projection spec generated from approved object/link artifacts.
3. Replace `GraphRepository.ENTITY_CONFIG` and `LINK_CONFIG` reads with artifact-driven specs.
4. Move import scripts to raw-table import only; seed demo ontologies only behind explicit fixture flags.
5. Keep tenant playbooks as demo-specific reasoning fixtures, clearly separated from core ontology modeling.
6. Replace iterative enrichment term heuristics with a model call that receives approved ontology constraints and source evidence, then emits typed proposed nodes/edges/finding candidates with provenance.

## Non-Negotiable Boundary

No schema-to-graph decision should be accepted without:

- source schema/profiling evidence;
- LLM structured output or reviewed human-edited equivalent;
- draft/proposed status;
- provenance and confidence;
- explicit review gate before canonical ontology or formal graph writes.

