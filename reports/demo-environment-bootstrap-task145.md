# Demo Environment Bootstrap - task #145

## Root Cause

The local 8772 demo process was running against a fresh metadata Postgres
database. It had tenant metadata that was created manually during Autopilot
testing, but the server was started without `--ensure-schema`, so
`aletheia_ontology_artifacts` and the related Ontology tables were absent.

Result: Autopilot metadata could work, while Ontology catalog/detail endpoints
failed with `relation "aletheia_ontology_artifacts" does not exist`.

## Fix

Added a repeatable metadata bootstrap:

```bash
.venv/bin/python scripts/bootstrap_demo_environment.py
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8772 --ensure-schema
```

The bootstrap:

- calls `ensure_artifact_schema()` for Ontology/Reasoning metadata tables
- ensures `aletheia_tenants`
- registers `creditcardfraud`
- seeds default Northwind demo ontology artifacts:
  - 5 approved object artifacts
  - 3 approved link artifacts
- seeds creditcardfraud ontology artifacts:
  - 4 draft object artifacts
  - 3 draft link artifacts
- sanitizes old local Autopilot smoke sessions so historical `blocked_fields`
  use `card_verification_code_fields`, not raw verification field names

This keeps the current 8772 demo environment reproducible without relying on
one-off manual table creation.

## Current Bootstrap Output

```text
Aletheia demo metadata bootstrap complete
metadata_db_url=postgresql+psycopg2://aletheia_pg_user:aletheia_pg_password@127.0.0.1:5432/aletheia_ontology
tables=aletheia_artifact_evidence,aletheia_artifact_reviews,aletheia_ontology_artifacts,aletheia_reasoning_findings,aletheia_reasoning_tasks,aletheia_tenants
artifacts[creditcardfraud][draft]=7
artifacts[default][approved]=8
sanitized_autopilot_sessions=3
```

## Fresh 8772 Smoke

Restarted 8772 with:

```bash
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8772 --ensure-schema
```

Validated URLs:

- `http://127.0.0.1:8772/?screen=ontology&tenant=default`
- `http://127.0.0.1:8772/?screen=ontology&tenant=default&artifact=object%3Aemployee`
- `http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud`

Smoke summary:

```json
{
  "creditcardfraud_artifact_count": 7,
  "default_catalog_count": 8,
  "employee_status": "approved",
  "has_pruned_reason": true,
  "playbook_candidates": 5,
  "playbook_hypotheses": 6,
  "raw_sensitive_leak": false,
  "tenants": ["default", "northwind-sandbox", "creditcardfraud"]
}
```

Notes:

- Docker is not running on this machine, so the current 8772 process is using
  local Homebrew Postgres for metadata.
- MySQL source DB is not currently listening on 3306, so Ontology source-schema
  detail falls back to seeded metadata. Autopilot playbook still returns the
  deterministic reported profile used by task #142.

## Verification

Passed:

```bash
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py scripts/bootstrap_demo_environment.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
git diff --check
```
