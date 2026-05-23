# Web Enrichment Validation - Task 173

Status: PASS

Validated commit: `b51141f` (`Enforce web enrichment source allowlist`)

## Scope

Validate the ingestion-stage web enrichment feature from task #172:

- web search/crawl can enrich ontology candidates only as reviewable draft proposals;
- every proposal carries source and field-level provenance;
- source/domain safety policy is enforced before proposal creation;
- no canonical ontology, graph, or target artifact mutation happens automatically;
- reviewer can see provenance and risk metadata in the Ontology UI.

## Fix Regression

The earlier FAIL was fixed. A non-allowlisted public URL now goes to skipped audit and does not create a proposal.

Fixture input:

```json
{
  "allowed": "https://zenodo.org/records/13841882?task173=1779546867",
  "untrusted": "https://example.org/untrusted-maritime-risk-task173-1779546867",
  "private": "http://127.0.0.1:8772/private?token=task173secret1779546867"
}
```

Run result:

```json
{
  "proposal_count": 1,
  "result_count": 3,
  "skipped_sources": [
    {
      "reason": "blocked_domain_not_allowlisted",
      "url": "https://example.org/untrusted-maritime-risk-task173-1779546867"
    },
    {
      "reason": "blocked_non_public_or_sensitive_url",
      "url": "http://127.0.0.1:8772/private?token=task173secret1779546867"
    }
  ]
}
```

DB checks:

```json
{
  "blocked_counts": {
    "proposal_rows": 0,
    "webenrichment_artifacts": 0
  },
  "private_counts": {
    "proposal_rows": 0,
    "webenrichment_artifacts": 0
  },
  "allowed_counts": {
    "proposal_rows": 1,
    "webenrichment_artifacts": 1
  }
}
```

## Provenance

The allowed Zenodo source produced a draft proposal visible through the API:

```json
{
  "proposal_key": "webenrichment:object_chokepoint:7cc8d880e94c218c",
  "target_artifact_key": "object:chokepoint",
  "source_url": "https://zenodo.org/records/13841882?task173=1779546867",
  "status": "draft",
  "source": {
    "search_provider": "static_json",
    "search_query": "Chokepoint Maritime chokepoint or strait that can concentrate trade disruption risk. ontology definition source evidence",
    "search_rank": 1,
    "retrieved_at": "2026-05-23T14:34:27.930242Z",
    "robots_risk": "crawl skipped: crawl_budget_exhausted",
    "license_risk": "CC-BY mentioned by source/search result"
  },
  "field_provenance": [
    {
      "artifact_field": "description",
      "proposed_operation": "enrich_context",
      "review_required": true
    }
  ],
  "governance": {
    "canonical_writes": "disabled",
    "graph_writes": "disabled",
    "review_gate": "ontology_review_required",
    "target_artifact_modified": false
  }
}
```

## Write Boundary

Before/after DB fingerprint checks passed:

```json
{
  "non_web_artifact_count_unchanged": true,
  "non_web_status_version_hash_unchanged": true,
  "approved_non_web_count_unchanged": true,
  "target_payload_hash_unchanged": true,
  "target_status_version_before": {
    "status": "approved",
    "version": 2,
    "artifact_type": "object"
  },
  "target_status_version_after": {
    "status": "approved",
    "version": 2,
    "artifact_type": "object"
  }
}
```

Default graph API fingerprint remained stable:

```json
{
  "approved": true,
  "node_count": 157,
  "edge_count": 156
}
```

## UI Smoke

Used isolated current-code service:

`http://127.0.0.1:8781/?screen=ontology&tenant=maritime-risk&artifact=object%3Achokepoint`

Headless Chrome DOM smoke found reviewer-visible web enrichment metadata:

- `Web enrichment`
- `Allowed Zenodo maritime source`
- `https://zenodo.org/records/13841882`
- `Robots risk`
- `License risk`
- `field provenance`
- `disabled canonical`
- `disabled graph`

DOM capture: `/tmp/task173_ontology_dom.html`

## Commands

```bash
.venv/bin/python -m unittest tests/test_web_enrichment.py
.venv/bin/python -m py_compile agents/web_enrichment_agent.py agents/data_scraper_agent.py agents/ontology_artifacts.py review_workbench.py
.venv/bin/python /tmp/task173_validate.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
npx --yes esbuild web/review_workbench/screens.jsx --outfile=/tmp/task173-screens.js --loader:.jsx=jsx --format=iife
git diff --check
```

All passed.

Detailed JSON evidence: `reports/web-enrichment-validation-task173-saskue.json`
