# Continuous Agent Framework: US-Iran War Impact Analysis

## Goal

Build a continuously running enrichment and reasoning agent that keeps expanding the current graph network and periodically produces reviewed, evidence-backed findings about how a US-Iran war or escalation scenario propagates across global systems.

The product goal is not “crawl the web forever.” The goal is:

- keep a bounded, auditable frontier of entities/events/claims;
- enrich facts and relationships under approved ontology;
- propose new ontology only through review;
- run graph multi-hop reasoning over the evolving network;
- surface candidate findings with paths, confidence, and review actions.

## Core Objects

### 1. Continuous Enrichment Session

Represents a long-running investigation.

Example:

```json
{
  "session_key": "continuous:us-iran-impact:20260525",
  "tenant": "us-iran-impact",
  "objective": "Analyze global political, military, energy, shipping, financial, supply-chain, country, company, and market impacts of a US-Iran war scenario",
  "status": "running",
  "mode": "bounded_autonomous",
  "canonical_writes": "disabled",
  "formal_graph_writes": "disabled"
}
```

Required controls:

- `max_iterations_per_cycle`
- `max_sources_per_cycle`
- `max_new_frontier_per_cycle`
- `max_depth`
- `allowed_domains`
- `blocked_domains`
- `rate_limit`
- `dedupe_policy`
- `confidence_thresholds`
- `pause/stop` API
- `human_review_required_for_ontology=true`

### 2. Frontier Item

A unit of work for the agent.

Types:

- `event`: US strike, Iranian retaliation, sanction package, Hormuz closure threat
- `entity`: country, ministry, military unit, company, vessel, port, commodity, market index
- `relationship`: country imports oil from region, company exposed to shipping route, bank exposed to sanctions
- `question`: “Which countries are most exposed to Hormuz closure?”
- `finding`: already discovered candidate finding that deserves expansion

Each frontier item has:

- key
- type
- priority
- depth
- source trace
- last expanded time
- status
- dedupe key
- stop reason

### 3. Evidence Source

Every crawled/search result is treated as evidence, not truth.

Each source record stores:

- URL
- title/snippet/raw extraction summary
- crawl time
- retrieval method
- source type: official, dataset, news, market data, analysis, social, unknown
- source reliability score
- robots/license risk
- geopolitical bias/uncertainty note when relevant
- extracted claims
- skipped/block reason if rejected

### 4. Graph Fact

A fact node or edge under existing approved ontology.

Examples:

- `Country:CHN -> imports_oil_from -> Region:Persian Gulf`
- `Chokepoint:Hormuz Strait -> disruption_affects -> Commodity:Crude Oil`
- `Company:Maersk -> operates_route_through -> Chokepoint:Hormuz Strait`
- `Market:Brent Crude -> reacts_to -> Event:Hormuz Closure Threat`

Fact graph writes are allowed only to working/proposed graph with:

- approved ontology basis
- source URL
- evidence refs
- confidence
- extraction agent/run id
- time validity
- TTL/decay policy
- conflict status
- rollback support

### 5. Ontology Candidate

Any new type/relation/property proposal.

Examples:

- new type: `SanctionPackage`, `MilitaryEscalation`, `InsurancePremiumShock`
- new relation type: `exposes_to_secondary_sanctions`, `raises_war_risk_for`, `reroutes_shipping_via`
- new property: `war_risk_score`, `shipping_delay_days`, `oil_import_dependency_pct`

These must go to Ontology review. They do not become canonical automatically.

### 6. Candidate Finding

A reasoning result generated from the enriched graph.

Must include:

- claim
- multi-hop path
- evidence refs for each hop
- confidence
- assumptions
- counter-evidence
- missing data
- recommended action
- review status

## Domain Ontology for US-Iran Impact

### Object Types

Core geopolitical/military:

- `Event`
- `ConflictActor`
- `Country`
- `MilitaryAsset`
- `MilitaryBase`
- `SanctionPackage`
- `DiplomaticAction`

Energy and shipping:

- `Chokepoint`
- `Port`
- `ShippingRoute`
- `Vessel`
- `Commodity`
- `EnergyFlow`
- `InsuranceMarket`

Economy and finance:

- `MarketIndex`
- `Currency`
- `BondMarket`
- `Bank`
- `Company`
- `SupplyChainSegment`
- `TradeFlow`

Public/social layer:

- `NewsClaim`
- `OfficialStatement`
- `SocialSignal`
- `PopulationImpact`

### Relation Types

- `actor_participates_in_event`
- `event_raises_risk_for_region`
- `country_imports_commodity_from_region`
- `commodity_flows_through_chokepoint`
- `company_operates_route_through_chokepoint`
- `market_reacts_to_event`
- `sanction_targets_actor`
- `sanction_affects_company`
- `conflict_disrupts_shipping_route`
- `shipping_disruption_impacts_supply_chain`
- `supply_chain_impacts_company`
- `company_impacts_market_index`
- `official_statement_updates_risk_assessment`

## Continuous Loop

```text
1. Load active session
2. Pull top-priority frontier items
3. Generate search queries from frontier + objective
4. Search/crawl within source policy
5. Extract claims, entities, and relationships
6. Classify extraction:
   a. existing ontology fact -> proposed/working graph
   b. possible ontology change -> ontology proposal review
   c. weak/blocked source -> skipped audit
7. Dedupe and conflict-check facts
8. Update frontier from new nodes/edges/findings
9. Run graph reasoning playbooks by domain layer
10. Create candidate findings
11. Update session status and dashboard
12. Sleep/backoff, then repeat
```

## Reasoning Playbooks

### 1. Energy Shock Propagation

Path shape:

```text
Conflict event -> Hormuz risk -> crude/LNG flow -> importing country -> market/industry impact -> action
```

Example finding:

> Hormuz escalation can propagate to JPN/KOR energy security through crude/LNG import dependency, raising shipping insurance and strategic reserve review priority.

### 2. Shipping and Insurance Shock

Path shape:

```text
Military escalation -> chokepoint/route -> vessel rerouting/insurance premium -> port delay -> company/supply chain exposure
```

### 3. Sanctions and Secondary Exposure

Path shape:

```text
Sanction package -> targeted Iranian entity -> bank/company/country exposure -> transaction risk -> compliance action
```

### 4. Market Contagion

Path shape:

```text
Event -> commodity price -> currency/index/sector -> country/company impact -> watchlist action
```

### 5. Diplomatic Escalation/De-escalation

Path shape:

```text
Official statement -> actor intent -> risk score change -> affected routes/markets -> finding update
```

### 6. Humanitarian/Public Impact

Path shape:

```text
Conflict event -> infrastructure/population impact -> migration/public health/economic pressure -> international response
```

## Agent Roles

### Source Discovery Agent

Finds candidate sources and obeys source policy.

Outputs:

- source candidates
- skipped sources
- crawl tasks

### Extraction Agent

Extracts entities, relationships, metrics, and claims.

Outputs:

- fact nodes/edges
- ontology candidates
- confidence and evidence spans

### Ontology Gate Agent

Classifies whether extraction fits approved ontology.

Outputs:

- `graph_fact_ok`
- `ontology_review_required`
- `reject_low_confidence`

### Graph Updater Agent

Writes only to working/proposed graph.

Outputs:

- proposed graph nodes/edges
- conflict records
- rollback metadata

### Frontier Manager

Decides what to crawl next.

Inputs:

- new nodes/edges
- high-impact findings
- stale findings
- missing evidence

### Deep Reasoning Agent

Runs graph multi-hop reasoning.

Outputs:

- candidate findings
- missing evidence tasks
- review actions

### Reviewer/Policy Agent

Checks safety and prevents boundary violations.

Outputs:

- blocked writes
- policy violations
- audit events

## Status API

`GET /api/enrichment/sessions/{session_key}`

Returns:

- status: running / paused / stopped / failed
- current cycle
- active frontier count
- sources crawled
- skipped sources
- new graph facts
- ontology proposals waiting review
- candidate findings
- last cycle summary
- next scheduled cycle

`POST /api/enrichment/sessions/{session_key}/pause`

`POST /api/enrichment/sessions/{session_key}/resume`

`POST /api/enrichment/sessions/{session_key}/stop`

`POST /api/enrichment/sessions/{session_key}/run-cycle`

## UI

### Workspace / Case Inbox

Shows the continuous investigation as a case:

- `US-Iran War Impact Monitor`
- running / paused
- latest findings
- blocked ontology proposals
- missing evidence
- next actions

### Graph Page

Shows:

- working graph facts
- proposed graph facts
- source/evidence path
- conflict/duplicate status
- batch review
- promote/publish gate when ready

### Ontology Page

Shows:

- ontology candidates from extraction
- source evidence
- reason why it is considered ontology-changing
- approve/reject/needs evidence

### Reasoning Page

Shows:

- candidate findings by layer
- multi-hop path
- evidence chain
- assumptions/counter-evidence
- dependency on proposed graph vs approved graph
- review gate

## Safety and Governance

Hard rules:

1. No automatic canonical ontology writes.
2. No silent formal graph writes from crawler.
3. No private/local/sensitive URL crawling.
4. No low-confidence source promoted into high-confidence finding.
5. Every fact and finding must carry provenance.
6. Every cycle must be auditable and reproducible by run key.
7. The worker must be pausable/stoppable.
8. Deduplication and conflict detection are required before graph write.
9. Findings depending on unreviewed ontology are blocked or clearly marked.
10. Source policy failures go to skipped audit, not graph/finding output.

## Minimal MVP

Phase 1 can be small:

- Tenant: `us-iran-impact`
- Seed frontier:
  - `Event:US-Iran escalation`
  - `Chokepoint:Hormuz Strait`
  - `Commodity:Crude Oil`
  - `Country:JPN/KOR/CHN/USA`
- Source policy:
  - official sources and curated fixtures first
- One cycle at a time, manually triggered
- Proposed graph facts only
- Candidate findings only

Phase 2:

- Background worker with scheduled cycles
- Pause/resume/stop/status APIs
- UI session dashboard

Phase 3:

- Multi-source live web search
- market/news updates
- stale/reaffirm finding lifecycle
- alerting when findings change materially

## Acceptance Criteria

1. Agent status page shows whether the crawler is running and what it is doing.
2. Each cycle produces a run record with frontier, sources, skipped audit, extracted facts, and findings.
3. Existing ontology facts can enter proposed/working graph with provenance.
4. New ontology candidates go to review and never canonicalize automatically.
5. Findings include complete paths and distinguish approved vs proposed facts.
6. Stopping the session prevents new crawling and graph writes.
7. Re-running the same frontier dedupes existing facts instead of duplicating graph noise.
