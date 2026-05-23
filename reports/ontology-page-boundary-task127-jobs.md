# Task #127: Ontology Page PRD / Data Contract

## Product Decision

`Workspace` stays lightweight for now. It should behave as a Case Inbox: what work exists, what is blocked, who owns it, and what the next action is.

`Reasoning` is the deep single-Case surface: question, run, conclusion, evidence chain, trace, review action, follow-up, and rerun.

`Ontology` is the canonical knowledge governance surface. All ontology-related information should live here: raw source origin, source schema, object/link/property definitions, evidence, review history, canonical status, version, coverage, and downstream usage.

## Page Boundary

### Workspace

Owns:
- Case / work item list.
- Priority, status, owner, blocker, next action.
- Short conclusion or evidence-gap summary.
- Entry links: `Open reasoning`, `Open ontology basis`, `Open review`.

Does not own:
- ObjectType / LinkType / Property definition detail.
- Raw data source or schema inspection.
- Full evidence chain or reasoning trace.
- Canonical schema review workflow.

### Reasoning

Owns:
- One Case's question and scoped reasoning context.
- Reasoning run, current conclusion, confidence, trace, evidence chain.
- Finding-level review actions.
- Follow-up question and rerun.

Does not own:
- Full ontology governance detail.
- Raw source schema exploration.
- Ontology artifact edit / canonical schema lifecycle.

Reasoning may show ontology only as summary basis, for example:
`Employee 1:N Order · approved v6 · source orders.employeeID -> employees.employeeID · View in Ontology`.

### Ontology

Owns:
- ObjectType, LinkType, Property, Rule/Basis definitions.
- Original source systems, source tables, columns, PK/FK, profiling and semantic hypotheses.
- Artifact evidence and source references.
- Review process and audit history for ontology artifacts.
- Canonical status, version, approval gate, graph ingestion eligibility.
- Schema diagram, mapping coverage, conflict/missing/low-confidence issues.
- Used-by impact: reasoning tasks, findings, graph paths, instance explorer paths, downstream templates.

Ontology is not a task inbox and not a single-Case reasoning workspace.

## Ontology Page Information Architecture

### 1. Catalog / Schema Map

Purpose: show the current tenant's ontology structure.

Required controls:
- Tenant awareness: tenant id, namespace, graph database.
- Tabs or filters: ObjectType / LinkType / Property / Rule.
- Status filter: approved / proposed / needs_changes / rejected / draft.
- Search by name, canonical_key, source table, source column.

Required display:
- Canonical key.
- Type.
- Name and description.
- Status, version, confidence.
- Source agent.
- Updated time.
- Canonical eligibility: whether it is consumed by default graph ingestion.

Default view:
- Show approved canonical first.
- Allow proposed/draft as overlay, clearly marked as non-canonical.

### 2. Definition Detail

Purpose: explain what this ontology element means.

For ObjectType:
- `canonical_key`
- `name`
- `description`
- `graph_label`
- primary/source keys
- properties
- mapped source tables
- extraction SQL or mapping summary
- generated graph schema if available

For LinkType:
- `canonical_key`
- `name`
- `source_object`
- `target_object`
- `cardinality`
- join condition / source relation
- source fields
- graph edge name
- extraction SQL or mapping summary

For Property:
- owning object or link
- source column(s)
- data type
- nullable/required
- semantic type/hypothesis
- derivation rule if derived

For Rule/Basis:
- rule name
- applies-to ObjectType/LinkType/Property
- rule expression or plain-language statement
- approved status/version
- downstream reasoning usage

### 3. Raw Source / Schema

Purpose: make the origin of ontology explicit.

Required display:
- Source system or database.
- Schema/table name.
- Column list with data type, nullable, primary key, comments.
- FK/join relation when used by a LinkType.
- Row count or profiling summary when available.
- Extraction/profiling timestamp.

Current available source tables from code:
- `aletheia_extracted_tables`
- `aletheia_extracted_columns`
- `aletheia_column_profiles`
- `aletheia_object_mappings`
- `aletheia_business_objects`
- `aletheia_business_links`

### 4. Evidence

Purpose: show why the ontology element exists.

Required fields:
- `evidence_type`
- `source_ref`
- `summary`
- `confidence`
- `content_hash`
- `raw_payload`
- created time

Evidence must be grouped by kind:
- schema evidence
- data profile evidence
- source row/sample evidence
- LLM or agent proposal evidence
- human review evidence
- conflict/missing evidence

### 5. Review / Governance

Purpose: make canonical lifecycle auditable.

Required display:
- Current status.
- Current version.
- Latest review event.
- Full review history.
- Reviewer, decision, reason.
- Before/after status and version.
- Before/after payload diff or link to diff.
- Approval effect: whether the artifact is eligible for default graph ingestion.

Current available table:
- `aletheia_artifact_reviews`

Review actions may stay in a review drawer/workflow, but the history and current governance state must be visible in Ontology.

### 6. Canonical / Graph Readiness

Purpose: make the approved-only gate concrete.

Required display:
- `approved` means eligible for default graph ingestion.
- `draft/proposed/needs_changes/rejected` means blocked from default graph ingestion.
- Tenant graph database / graph space.
- Generated graph label/edge/schema when present.
- Last ingestion status if available.

### 7. Used By / Impact

Purpose: show why changing this ontology element matters.

Required display:
- Reasoning tasks using this basis.
- Findings that cite it.
- Instance Explorer paths depending on it.
- Graph paths or edges using it.
- Downstream rules/templates impacted.

Minimum viable implementation can calculate this from:
- `reasoning_runs.evidence_paths_json`
- `reasoning_findings.supporting_evidence_json`
- known artifact references in instance and graph APIs.

## API Contract

Reuse existing live APIs first:

### Existing

`GET /api/artifacts?tenant=<tenant_id>&artifact_type=&status=&source_agent=`

Returns ontology artifact list and stats. This should feed the Ontology catalog.

`GET /api/artifacts/{canonical_key}?tenant=<tenant_id>`

Returns artifact detail with:
- artifact fields
- `evidence[]`
- `reviews[]`
- tenant info

### Required Additions

`GET /api/ontology/catalog?tenant=<tenant_id>&kind=&status=&q=`

Returns normalized ontology catalog rows, one row per ObjectType / LinkType / Property / Rule.

Minimum row shape:

```json
{
  "tenant": {"tenant_id": "default", "namespace": "northwind", "graph_database": "aletheia_default"},
  "items": [
    {
      "canonical_key": "link:employee:1:n:order",
      "kind": "LinkType",
      "name": "Employee handles Order",
      "description": "...",
      "status": "approved",
      "version": 6,
      "confidence": 0.88,
      "source_agent": "LinkWeaver",
      "canonical_eligible": true,
      "source_summary": "orders.employeeID -> employees.employeeID",
      "updated_at": "..."
    }
  ],
  "stats": []
}
```

`GET /api/ontology/{canonical_key}?tenant=<tenant_id>`

Returns full governance detail:

```json
{
  "artifact": {},
  "definition": {},
  "source_schema": {
    "tables": [
      {
        "schema_name": null,
        "table_name": "orders",
        "columns": [
          {"column_name": "employeeID", "data_type": "INTEGER", "is_primary_key": false, "is_nullable": true, "semantic_type": "employee_id"}
        ],
        "profile": {}
      }
    ],
    "join_conditions": ["orders.employeeID = employees.employeeID"]
  },
  "evidence": [],
  "reviews": [],
  "canonical": {
    "status": "approved",
    "version": 6,
    "canonical_eligible": true,
    "graph_database": "aletheia_default",
    "graph_label_or_edge": "employee_order",
    "ingestion_gate": "approved_only"
  },
  "used_by": {
    "reasoning_tasks": [],
    "findings": [],
    "graph_paths": [],
    "instance_paths": []
  },
  "issues": []
}
```

`GET /api/ontology/{canonical_key}/diff?tenant=<tenant_id>&from_version=&to_version=`

Returns before/after payload and status diff. This can be second-phase if current review rows already show enough before/after.

## Navigation Contract

Required deep links:
- Reasoning -> Ontology: ontology basis summary links to `/ontology.html?tenant=<tenant_id>&key=<canonical_key>`.
- Ontology -> Reasoning: Used-by list links to `/reasoning.html?tenant=<tenant_id>&task=<task_key>`.
- Workspace -> Reasoning: Case row links to `/reasoning.html?...`.
- Workspace -> Ontology: only when showing a compact basis/reference, links to `/ontology.html?...`.
- Ontology -> Review action: if an ontology artifact needs approval, action should open the same artifact's review drawer/state, not duplicate a full Workspace artifact table.

## Migration From Current Implementation

Current state observed:
- `Workbench` is currently a live ontology artifact review table powered by `/api/artifacts`.
- `Ontology` is a static mock catalog using `data.ARTIFACTS`.
- `Reasoning` is already the correct deep single-Case page.

Required shift:
- Move ontology artifact list/detail/evidence/review/canonical status from Workbench into Ontology.
- Ontology must stop relying on mock `data.ARTIFACTS` for real use.
- Workspace should be reduced to light Case Inbox or kept minimal until multi-Case requirements are explicit.
- Reasoning should keep only ontology basis summary and deep links, not full governance details.

## Acceptance Criteria

Pass if:
- Ontology page shows real tenant-scoped ObjectType / LinkType / Property artifacts from live APIs.
- Selecting `link:employee:1:n:order` shows definition, raw source relation, evidence, review history, approved version/status, graph ingestion eligibility, and used-by links.
- Reasoning page no longer expands full ontology review/source schema/history; it shows a compact basis summary plus `View in Ontology`.
- Workspace does not duplicate ontology artifact governance or single-Case reasoning detail; it remains a light Case Inbox / entry surface.
- Draft/proposed/rejected ontology elements are visually distinct from approved canonical elements and cannot be mistaken as default graph input.
- Tenant context is visible and all calls are tenant-scoped.

Fail if:
- Ontology still uses mock data while Workbench remains the real ontology artifact page.
- Workspace and Ontology both allow editing/reviewing the same ontology artifact as primary workflows.
- Reasoning contains full ontology source schema/review history rather than a compact basis reference.
- Approved-only/canonical boundaries are hidden or ambiguous.
