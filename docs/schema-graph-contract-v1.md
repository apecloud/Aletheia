# Schema Graph Contract v1

Aletheia's schema graph contract is the boundary between raw source database metadata, LLM-inferred draft ontology artifacts, and human review.

The contract is implemented in `agents/schema_graph_modeling_agent.py`:

- `GraphNodeTypeDraft` defines draft ontology object/entity types.
- `GraphEdgeTypeDraft` defines draft relation/link types.
- `GraphModelDraft` carries `schema_version`, `min_compatible_version`, review boundary, constraints, and compatibility policy.
- `artifact_specs_for_draft()` projects drafts into review-gated `object` and `link` ontology artifact payloads.

## Versioning

The current contract version is `schema_graph_contract_v1`.

Every `GraphModelDraft` defaults to:

- `schema_version = schema_graph_contract_v1`
- `min_compatible_version = schema_graph_contract_v1`
- `review_boundary = draft_only_until_human_review`

Artifact specs carry the same version fields in their payloads so persisted draft artifacts remain traceable to the contract that produced them.

## Required Constraints

Entity/object draft constraints:

- Evidence must include at least one source-backed entry.
- Keys are stable graph keys derived from schema evidence.
- Primary keys, when present, identify the best source primary key.
- Confidence must be between 0 and 1.

Relation/link draft constraints:

- Evidence must include at least one source-backed entry.
- If `node_types` are present, relation endpoints must reference declared node keys.
- Metrics and derived values stay on edge properties, not separate ontology object types.
- Confidence must be between 0 and 1.

Rejected candidates must include `name`, `reason`, and `suggested_graph_treatment` so dropped event/fact/metric tables remain observable.

## Compatibility

Breaking changes require a major version when they remove or rename fields consumed by `artifact_specs_for_draft()`, change review-boundary semantics, tighten constraints for persisted drafts, or change object/link payload meanings.

Minor-compatible changes can add optional fields, add artifact metadata while preserving existing keys, or loosen validation bounds.

Migration is required for natural-key reinterpretation, endpoint semantic changes, or review-gate behavior changes.

## Validation

Run the focused contract tests:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_schema_graph_modeling_agent.py'
```
