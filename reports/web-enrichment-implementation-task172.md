# Web Enrichment Implementation - Task 172

## Summary

Task #172 adds an ingestion-stage web enrichment path for ontology candidates. The implementation can run from the data scraper after import or as a standalone agent. It searches or consumes provided web results, fetches bounded external pages, and creates draft-only `WebEnrichment` ontology proposals with reviewer-visible provenance.

The target ontology artifact is not overwritten. Canonical ontology, graph state, approved artifacts, and review decisions are not modified by enrichment.

## Data Model

New metadata tables:

- `aletheia_web_enrichment_runs`: one row per enrichment run, including tenant, provider, status, target artifacts, safety profile, budget, query/result/proposal counts, and timing.
- `aletheia_web_enrichment_proposals`: one row per proposed enrichment source, including target artifact key, source URL/title, summary, content hash, confidence, status, and raw proposal payload.

Each accepted web result also creates a draft ontology artifact:

- `artifact_type=WebEnrichment`
- `status=draft`
- `source_agent=WebEnrichmentAgent`
- evidence includes the target artifact and the web source

## Proposal Contract

Each proposal payload records:

- `source.url`, `source.title`, `source.search_query`, `source.search_rank`, `source.retrieved_at`
- `summary`
- `confidence`
- `robots_risk`
- `license_risk`
- `field_provenance` with proposed fields, source URL, operation, and `review_required=true`
- governance boundary: `canonical_writes=disabled`, `graph_writes=disabled`, `target_artifact_modified=false`, `review_gate=ontology_review_required`

## Safety

Default operation is deterministic and offline-testable via `--search-results-json` or `--seed-url`. Live DuckDuckGo HTML search requires explicit `--enable-live-search`.

Crawler safety controls:

- Blocks localhost, private, link-local, reserved, multicast, and `.local` targets.
- Blocks sensitive query parameters such as token, password, secret, credential, and api key.
- Requires `--allowed-domain` unless `--allow-discovered-domains` is explicitly set.
- Enforces max artifacts, max results per query, max crawl pages, timeout, and max page bytes.
- Records blocked/skipped sources in the run payload without turning them into proposals.

## API and UI

Backend:

- `GET /api/web-enrichment/proposals?tenant=<tenant>&artifact=<canonical_key>&limit=<n>`
- Artifact detail now includes `web_enrichment` for the selected artifact.

Frontend:

- Ontology detail `Source & Schema` shows a `Web enrichment` panel when draft external evidence exists.
- Reviewers can see source URL, summary, query, retrieved time, robots/license risk, field provenance, confidence, and write boundary.

## Validation

Local deterministic smoke used:

```bash
.venv/bin/python agents/web_enrichment_agent.py \
  --tenant maritime-risk \
  --artifact object:chokepoint \
  --search-results-json /tmp/task172-web/search.json \
  --allowed-domain zenodo.org \
  --max-artifacts 1 \
  --max-results-per-query 2 \
  --max-crawl-pages 0 \
  --json
```

Result:

- `proposal_count=1`
- valid source: `https://zenodo.org/records/13841882`
- blocked source: `http://127.0.0.1:8772/private?token=secret`
- created draft artifact `webenrichment:object_chokepoint:1433f0e2400a47e9`
- canonical/graph writes remained disabled

API smoke:

- `/api/web-enrichment/proposals?tenant=maritime-risk&artifact=object%3Achokepoint&limit=5` returned the proposal with safety profile, budget, field provenance, robots risk, and license risk.
- `/api/artifacts/object%3Achokepoint?tenant=maritime-risk` included `web_enrichment`.

Checks:

```bash
.venv/bin/python -m py_compile agents/web_enrichment_agent.py agents/data_scraper_agent.py agents/ontology_artifacts.py review_workbench.py
.venv/bin/python -m unittest tests/test_web_enrichment.py
node --check web/review_workbench/api.js
npx --yes esbuild web/review_workbench/screens.jsx --outfile=/tmp/task172-screens.js --loader:.jsx=jsx --format=iife
git diff --check
```

