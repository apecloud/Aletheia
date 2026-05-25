# Continuous Web Graph Enrichment - Task #201

## Product Goal

Turn web enrichment from a one-shot ontology proposal helper into a bounded autonomous enrichment loop:

1. Crawl/search from current graph and ontology frontier.
2. Extract candidate ontology changes and factual graph facts.
3. Route ontology changes through human review.
4. Allow non-ontology facts under existing approved ontology to enter a working/proposed graph with provenance.
5. Continue crawling from newly discovered nodes and evidence.
6. Run deep graph reasoning over the enriched graph and produce candidate findings.

## Hard Boundary

This must not become an unlimited crawler that silently rewrites the knowledge system.

| Output type | Example | Write behavior |
| --- | --- | --- |
| New ontology type | `PortFacility`, `InsuranceExposure` | Draft ontology proposal only, human review required |
| New relation type | `country depends on chokepoint through commodity` | Draft ontology proposal only, human review required |
| New property/schema | `hazard_likelihood_score` | Draft ontology proposal only, human review required |
| Fact node under approved type | `Chokepoint:Hormuz Strait`, `Country:JPN` | Can enter working/proposed graph with provenance |
| Fact edge under approved relation | `Country:JPN -> depends_on -> Chokepoint:Hormuz Strait` | Can enter working/proposed graph with provenance |
| Finding | `Hormuz disruption propagates to JPN energy exposure` | Candidate finding only, human review required |
| Canonical ontology | approved Object/Link/Property definitions | Never auto-write |
| Formal graph | approved production graph | Never silently write from crawler; promote via explicit review/publish gate |

## Proposed Loop

```text
Seed frontier
  -> web search / crawl
  -> source policy check
  -> extraction
  -> classify output
      -> ontology candidate -> Ontology review queue
      -> fact node/edge -> Working graph / Proposed graph
      -> weak/blocked source -> skipped audit
  -> dedupe / conflict check
  -> update frontier
  -> multi-hop reasoning
  -> candidate findings
  -> human review
```

## Data Model Additions

### Enrichment Run

`aletheia_continuous_enrichment_runs`

- `run_key`
- `tenant_id`
- `objective`
- `status`
- `seed_frontier`
- `budget_profile`
- `source_policy`
- `created_at`, `updated_at`
- `stopped_reason`

### Frontier Item

`aletheia_enrichment_frontier`

- `frontier_key`
- `run_key`
- `tenant_id`
- `kind`: `ontology_artifact | graph_node | graph_edge | finding | query`
- `target_key`
- `depth`
- `priority`
- `status`: `queued | processing | expanded | skipped | exhausted`
- `source_trace`
- `dedupe_key`

### Proposed Graph Fact

Reuse/extend `aletheia_proposed_graph_elements`:

- `element_type`: `node | edge | finding`
- `status`: `draft | accepted_fact | rejected | needs_evidence | superseded`
- `ontology_basis`: approved object/link artifact keys
- `source_url`
- `evidence_refs`
- `confidence`
- `crawl_run_key`
- `frontier_key`
- `dedupe_key`
- `expires_at` / `ttl_policy`
- `review_boundary`: canonical/formal graph write disabled by default

### Ontology Proposal

Reuse ontology draft artifacts and web enrichment proposals. New ontology candidates must be tagged:

- `proposal_origin=continuous_web_enrichment`
- `requires_human_review=true`
- `source_url`, `evidence_refs`, `confidence`
- `proposed_change_type`: `new_type | new_relation | new_property | schema_change`

## Agent Behavior

The crawler agent can run continuously, but only under a bounded session contract:

- `max_iterations`
- `max_sources_per_iteration`
- `max_new_frontier_per_iteration`
- `allowed_domains`
- `rate_limit`
- `private_url_block=true`
- `dedupe=true`
- `conflict_detection=true`
- `low_confidence_to_skipped_or_needs_evidence=true`
- `canonical_writes=disabled`
- `formal_graph_writes=disabled`

## Reasoning Behavior

Deep reasoning should run after each meaningful enrichment batch:

- Input graph = approved ontology + approved graph + accepted/proposed graph facts in current enrichment scope.
- Findings must carry full path evidence.
- Candidate findings remain draft until human approval.
- Findings must distinguish approved facts from proposed facts.
- If a finding depends on unreviewed ontology proposals, it should be marked `ontology_review_blocked`.

## UI Changes

### Ontology Page

Show `New ontology candidates from web enrichment`:

- candidate type/relation/property
- source URL and snippets
- field-level provenance
- confidence
- approve/reject/needs evidence

### Graph Page

Show `Working graph / Proposed graph` facts:

- auto-inserted fact nodes/edges under approved ontology
- provenance and crawl run
- status, confidence, TTL
- accept/reject/needs evidence/batch review

### Reasoning Page

Show `Findings from continuous enrichment`:

- finding summary
- multi-hop path
- which steps are approved vs proposed
- blocked-by-ontology-review warnings
- approve finding as reviewed inference

## Acceptance Criteria

1. New ontology candidates never auto-canonicalize.
2. Fact nodes/edges under existing approved ontology can be inserted into proposed/working graph with full provenance.
3. The crawler can continue from newly discovered nodes/edges but obeys budget, allowlist, dedupe, rate limits, and skipped audit.
4. Low-confidence or blocked sources do not become graph facts.
5. Deep graph findings are generated from enriched graph paths and remain candidate findings.
6. UI clearly separates ontology proposals, proposed graph facts, and findings.
7. Canonical ontology and formal graph fingerprints do not change unless an explicit review/publish path is used.

## Suggested Implementation Split

1. Continuous enrichment session/frontier model and API.
2. Extractor classifier: ontology proposal vs graph fact vs skipped source.
3. Working/proposed graph write path for fact nodes/edges under approved ontology.
4. Frontier expansion loop with budget/dedupe/allowlist controls.
5. Deep reasoning trigger over enriched graph and candidate finding generation.
6. UI: Ontology candidates, Graph proposed facts, Reasoning findings.
7. Validation: canonical safety, graph provenance, infinite-loop guard, source policy, and finding traceability.
