# Graph Reasoning Dataset Selection - task #164

## Short answer

The best next dataset for Aletheia is **Maritime Chokepoint Disruption Risk**.

It is more suitable than `creditcardfraud` for graph reasoning because the value is not in one row classification. The value comes from multi-hop impact paths:

`hazard / conflict -> chokepoint -> country dependency -> trade exposure -> economic risk -> mitigation action`

This lets the demo show propagation, dependency, evidence composition, and "why this downstream country is exposed" explanations.

## What World Monitor actually uses

World Monitor is not one dataset. Its documentation describes a real-time OSINT dashboard built from many public data sources. The relevant sources for our purpose are:

- **ACLED**: armed conflict and protest event data.
- **GDELT**: global event, language, tone, and geolocated news-derived events.
- **IMF PortWatch / Portstraitwatch**: chokepoint and maritime transit intelligence.
- **UN Comtrade**: global merchandise trade flows.
- **USGS / NASA EONET / GDACS**: earthquake and natural hazard event layers.
- **Cloudflare Radar / cable maps / AbuseIPDB**: internet and cyber/infrastructure layers.

For Aletheia, we should not copy the whole World Monitor dashboard. We should pick one graph-shaped scenario that proves reasoning quality.

## Candidate comparison

### Candidate A: Maritime Chokepoint Disruption Risk

Primary source:

- Zenodo: "Maritime chokepoint dependencies and systemic risks"
- Files:
  - `chokepoint_country_dependencies.csv` ~651 KB
  - `chokepoint_risk_indicators.csv` ~3 KB
  - `chokepoint_systemic_risk_results.csv` ~3.1 MB

Access / license:

- Download method: direct file download from Zenodo record, no application API key needed for the core MVP files.
- License/access caveat: use the license attached to the Zenodo record as the import license of record; preserve citation metadata in the source registry before using it in a public demo.

Optional enrichment:

- IMF PortWatch / Portstraitwatch throughput
- ACLED conflict events near chokepoints
- GDELT news/tone around chokepoint names
- UN Comtrade commodity flows

Entity types:

- `Chokepoint`
- `Country`
- `TradeDependency`
- `Hazard`
- `RiskIndicator`
- `SystemicRiskResult`
- `ConflictEvent` / `NewsEvent` after enrichment
- `MitigationAction`

Relationship types:

- `Country -> depends_on -> Chokepoint`
- `Chokepoint -> exposed_to -> Hazard`
- `Country -> has_trade_exposure -> TradeDependency`
- `TradeDependency -> measured_by -> SystemicRiskResult`
- `ConflictEvent -> occurs_near -> Chokepoint`
- `RiskFinding -> supported_by -> Dependency / Hazard / Event`
- `MitigationAction -> mitigates -> RiskFinding`

Why it is strong:

- Small enough to import quickly.
- Naturally graph-shaped.
- Supports explainable multi-hop reasoning.
- Good fit for world-monitor-style geopolitical/supply-chain intelligence.
- Demo question has obvious business value.

Risk / caveat:

- Phase 1 is structural risk, not live real-time monitoring.
- Live ACLED/GDELT enrichment may need API/rate-limit handling.

Verdict:

- **Recommended first choice.**

### Candidate B: ACLED + GDELT Conflict / Protest Event Graph

Primary source:

- ACLED API / exports for event-level political violence and protest data.
- GDELT event files or APIs for geolocated event and media-tone data.

Access / license:

- Download method: ACLED via registered API/export access; GDELT via public event files/API.
- License/access caveat: ACLED access terms are more restrictive and may require an account/token; GDELT is easier for public smoke tests but noisier and less curated.

Scale:

- Can be small if scoped to one country / region / 6-12 months.
- Can become very large globally.

Entity types:

- `Event`
- `Actor`
- `Organization`
- `Location`
- `Country`
- `Source`
- `EventType`
- `TimeWindow`

Relationship types:

- `Actor -> participates_in -> Event`
- `Event -> occurs_at -> Location`
- `Event -> reported_by -> Source`
- `Event -> has_type -> EventType`
- `Actor -> co_occurs_with -> Actor`
- `Location -> belongs_to -> AdminRegion`

Reasoning value:

- Actor networks, escalation patterns, event clusters, source confidence.
- Can answer "which actor-location clusters are escalating?" or "which local events are under-covered by global media?"

Risk / caveat:

- ACLED API access requires authentication, and free/research tiers have restrictions.
- Actor/entity normalization is harder.
- More likely to become a noisy event browser unless we build careful aggregation.

Verdict:

- Good second phase after chokepoint model.

### Candidate C: Infrastructure / Disaster Impact Graph

Primary source:

- USGS earthquakes
- NASA EONET natural events
- GDACS disaster alerts
- Optional: airport delays, port calls, undersea cable map, Cloudflare Radar

Access / license:

- Download method: public APIs/feeds for USGS, NASA EONET, and GDACS; infrastructure enrichment depends on the selected asset dataset.
- License/access caveat: hazard feeds are generally easy to test with, but asset layers have mixed licensing and may require separate validation before public demos.

Entity types:

- `NaturalEvent`
- `InfrastructureAsset`
- `Country`
- `Region`
- `Alert`
- `ImpactArea`
- `TransportNode`

Relationship types:

- `NaturalEvent -> affects -> Region`
- `InfrastructureAsset -> located_in -> Region`
- `Alert -> describes -> NaturalEvent`
- `TransportNode -> serves -> Country`
- `RiskFinding -> supported_by -> Event / Alert / Asset`

Reasoning value:

- Good for geospatial and temporal risk aggregation.
- Can show "earthquake near critical infrastructure" reasoning.

Risk / caveat:

- Need a good infrastructure asset dataset to avoid being just points on a map.
- Harder to create business-action closure unless paired with ports/airports/cables.

Verdict:

- Useful demo later, especially for geospatial graph reasoning.

## Recommended MVP: Maritime Chokepoint Dataset

### Tenant

Use a new tenant:

`maritime-risk`

Display name:

`Maritime Chokepoint Risk`

### Source schema

Initial source tables:

- `chokepoint_country_dependencies`
- `chokepoint_risk_indicators`
- `chokepoint_systemic_risk_results`

Optional enrichment tables later:

- `acled_events_near_chokepoints`
- `gdelt_chokepoint_mentions`
- `portwatch_chokepoint_throughput`
- `commodity_trade_flows`

### ObjectType draft artifacts

- `object:chokepoint`
- `object:country`
- `object:trade_dependency`
- `object:hazard`
- `object:risk_indicator`
- `object:systemic_risk_result`
- `object:risk_finding`
- `object:mitigation_action`

### LinkType draft artifacts

- `link:country:n:m:chokepoint_dependency`
- `link:chokepoint:1:n:risk_indicator`
- `link:country:1:n:systemic_risk_result`
- `link:trade_dependency:n:1:country`
- `link:trade_dependency:n:1:chokepoint`
- `link:risk_finding:n:m:evidence`
- `link:mitigation_action:n:1:risk_finding`

## Sample findings to validate graph reasoning

### Finding 1: Concentrated chokepoint dependency

Question:

`Which countries are highly exposed to one maritime chokepoint?`

Evidence:

- Country dependency share on chokepoint.
- Systemic risk metric.
- Hazard likelihood/severity for that chokepoint.

Expected output:

- A ranked list of exposed countries.
- Why each country is exposed.
- Evidence boundary: structural dependency only unless enriched with live events.

### Finding 2: Hazard-adjusted trade disruption risk

Question:

`Which chokepoints combine high hazard severity with high dependent trade value?`

Evidence:

- Hazard severity/duration/likelihood.
- Expected trade disrupted.
- Number and diversity of dependent countries.

Expected output:

- Chokepoints with high systemic risk.
- Explanation path from hazard to country impact.

### Finding 3: Event-enriched disruption risk

Question:

`If conflict events increase near the Red Sea / Bab el-Mandeb, which countries should be prioritized for review?`

Evidence:

- ACLED/GDELT events near chokepoint.
- Country dependency on chokepoint.
- Systemic risk results.

Expected output:

- Countries and sectors most exposed.
- Confidence split between structural dependency and live event evidence.

## Why this is better than creditcardfraud for graph reasoning

`creditcardfraud` is useful for finding approval, evidence chain, and sensitive-field handling. But most reasoning is row/aggregate based:

`transaction -> account/card/merchant -> risk flag`

The maritime dataset is graph-native:

`event/hazard -> chokepoint -> trade route/dependency -> country -> risk/action`

It naturally demonstrates:

- multi-hop path explanation
- upstream/downstream impact propagation
- dependency concentration
- geographic and temporal enrichment
- action prioritization

## Concrete next tasks

1. **Dataset import**: download Zenodo files into `datasets/maritime_chokepoints`, create source tables, register `maritime-risk` tenant.
2. **Ontology draft**: create ObjectType / LinkType artifacts listed above.
3. **Graph explorer support**: allow Country -> Chokepoint -> Hazard / RiskResult neighborhoods.
4. **Reasoning playbook**: add a maritime risk playbook with the three sample questions.
5. **Validation**: verify source schema in Ontology, graph paths in Graph page, and approved finding reuse in Reasoning.

## Sources

- World Monitor data sources: https://www.worldmonitor.app/docs/data-sources
- World Monitor getting started / repo structure: https://www.worldmonitor.app/docs/getting-started
- ACLED API docs: https://acleddata.com/api-documentation/getting-started
- ACLED codebook: https://acleddata.com/knowledge-base/codebook/
- GDELT event codebook: https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
- Portstraitwatch data access: https://www.portstraitwatch.com/access-data
- Zenodo maritime chokepoint dataset: https://zenodo.org/records/13841882
