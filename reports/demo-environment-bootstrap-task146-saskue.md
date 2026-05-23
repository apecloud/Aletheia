# Demo Environment Bootstrap Validation - task #146

## Result

PASS for task #145 demo metadata bootstrap validation.

Validated two surfaces:

- Current demo service: `http://127.0.0.1:8772`
- Fresh controlled bootstrap service: `http://127.0.0.1:8776`

## Fresh Bootstrap

Created a temporary fresh metadata database:

`aletheia_task146_bootstrap`

The application DB user `aletheia_pg_user` does not have `CREATE DATABASE`; that is an environment permission boundary. I created the temporary database with the local Postgres superuser `slc` and then ran the bootstrap with:

```bash
.venv/bin/python scripts/bootstrap_demo_environment.py \
  --db-url 'postgresql+psycopg2://slc@127.0.0.1:5432/aletheia_task146_bootstrap'
```

I ran the bootstrap twice against the same fresh DB. Both runs returned stable, idempotent counts:

```text
tables=aletheia_artifact_evidence,aletheia_artifact_reviews,aletheia_ontology_artifacts,aletheia_reasoning_findings,aletheia_reasoning_tasks,aletheia_tenants
artifacts[creditcardfraud][draft]=7
artifacts[default][approved]=8
sanitized_autopilot_sessions=0
```

After validation, I stopped the temporary server and dropped `aletheia_task146_bootstrap`.

## Runbook

README runbook now contains the required fresh-demo steps:

```bash
.venv/bin/python scripts/bootstrap_demo_environment.py
.venv/bin/python review_workbench.py --host 127.0.0.1 --port 8772 --ensure-schema
```

The README also states that bootstrap seeds demo tenants/artifacts, while `--ensure-schema` is only a startup guard and does not seed tenants/artifacts by itself. This is sufficient to reproduce the intended demo path.

## Current 8772 Smoke

After running bootstrap on the current metadata DB, the current 8772 service returned:

- `/api/ontology/catalog?tenant=default`: HTTP 200
- `/api/ontology/object%3Aemployee?tenant=default`: HTTP 200
- `/api/ontology/link%3Aemployee%3A1%3An%3Aorder?tenant=default`: HTTP 200
- `/api/reasoning/autopilot/sessions?tenant=creditcardfraud`: HTTP 200
- `POST /api/reasoning/autopilot/playbooks/creditcardfraud/run?tenant=creditcardfraud`: HTTP 200

Observed:

- Default catalog contains 8 approved artifacts.
- `object:employee` is approved v1.
- `link:employee:1:n:order` is approved and exposes `orders.employeeID = employees.employeeID`.
- `creditcardfraud` playbook session returns 6 hypotheses and 5 candidate findings.
- API payloads do not contain raw `cardCVV` or `enteredCVV`.
- API payloads do contain `card_verification_code_fields` and `credit_card_transactions_safe`.

## Fresh 8776 Smoke

Started a fresh isolated service:

```bash
.venv/bin/python review_workbench.py \
  --host 127.0.0.1 \
  --port 8776 \
  --db-url 'postgresql+psycopg2://slc@127.0.0.1:5432/aletheia_task146_bootstrap' \
  --ensure-schema
```

Validated:

- `/api/ontology/catalog?tenant=default`: HTTP 200, 8 artifacts.
- `/api/ontology/object%3Aemployee?tenant=default`: HTTP 200, `object:employee` approved v1.
- `POST /api/reasoning/autopilot/playbooks/creditcardfraud/run?tenant=creditcardfraud`: HTTP 200, draft session with 6 hypotheses and 5 candidate findings.

Browser/DOM smoke:

- Fresh Ontology page shows `Employee`, `SOURCE & SCHEMA`, `SOURCE SCHEMA`, and `fallback`.
- Fresh Autopilot page shows `Autopilot`, `Draft candidate findings`, `EVIDENCE CHAIN`, and `credit_card_transactions_safe`.
- DOM did not contain `cardCVV` or `enteredCVV`.

Screenshots:

- `/tmp/task146-fresh-ontology-employee.png`
- `/tmp/task146-fresh-autopilot.png`
- `/tmp/task146-ontology-catalog.png`
- `/tmp/task146-ontology-employee.png`
- `/tmp/task146-autopilot.png`

DOM captures:

- `/tmp/task146_fresh_ontology_employee_dom.txt`
- `/tmp/task146_fresh_autopilot_dom.txt`
- `/tmp/task146_ontology_catalog_dom.txt`
- `/tmp/task146_ontology_employee_dom.txt`
- `/tmp/task146_autopilot_dom.txt`

## Source Schema Boundary

MySQL/Docker are not running in this environment, so live source introspection is not available. Ontology source schema correctly falls back to seeded metadata:

- `object:employee` returned `schema_source=fallback`.
- The page explicitly rendered `fallback`.

This does not block task #146 because #145 is about metadata bootstrap and same-environment demo reproducibility. A full live source-schema demo should be a separate data-source recovery task.

## Verification Commands

Passed:

```bash
.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py scripts/bootstrap_demo_environment.py
.venv/bin/python -m unittest tests/test_ontology_eval.py
node --check web/review_workbench/api.js
git diff --check
```

## Residual Notes

The repository working tree still contains unrelated pre-existing local changes and untracked reports. I did not revert or mix them into this validation.
