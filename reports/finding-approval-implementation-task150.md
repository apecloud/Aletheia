# Finding Approval Implementation - task #150

## Scope

Implemented the Finding approval loop from task #149:

- Formal Finding review lifecycle: `approved`, `needs_more_evidence`, `rejected`, `stale`, `superseded`, `reaffirmed`, `comment`.
- Approved Finding Registry API for active reviewed insights.
- Active reasoning context injection as `prior_finding / reviewed_inference`.
- Autopilot candidate human review gate: candidate approval creates a formal approved Finding, while Autopilot auto-promote remains blocked.
- Workspace next action and change proposal bridge APIs, both explicitly non-canonical.

## Product Boundary

Finding approval is a reviewed insight / decision memory approval. It writes only:

- `aletheia_reasoning_findings`
- `aletheia_reasoning_reviews`

It does not write canonical ontology, graph state, business actions, or ontology artifact approvals.

Approved findings can be reused as:

- `prior_finding` context for later reasoning
- `reviewed_inference` in the Finding Registry
- Workspace next-action input
- Draft change proposal bridge input

Inactive states (`rejected`, `needs_more_evidence`, `stale`, `superseded`) are excluded from active reasoning context by default and remain visible through explicit status/audit queries.

## API Changes

- `GET /api/reasoning/findings?tenant=<id>&status=approved&context=active`
- `POST /api/reasoning/autopilot/candidate-findings/<candidate_key>/approve`
- `POST /api/reasoning/autopilot/candidate-findings/<candidate_key>/needs-evidence`
- `POST /api/reasoning/autopilot/candidate-findings/<candidate_key>/reject`
- `POST /api/reasoning/findings/<finding_key>/actions`
- `POST /api/reasoning/findings/<finding_key>/change-proposals`
- Extended `POST /api/reasoning/findings/<finding_key>/<action>` with `needs-evidence`, `mark-stale`, `supersede`, and `reaffirm`.

## UI Changes

Reasoning page now shows:

- Approved Finding Registry in the right rail.
- Autopilot candidate `Approve as finding` action through the separate human review gate.
- Finding lifecycle controls: approve, needs evidence, reject, reaffirm, mark stale, supersede, comment.
- Copy clarifying that approved findings can create prior context / next actions / proposals but cannot write canonical ontology or graph.

## Validation

Static checks:

- `python3 -m py_compile review_workbench.py`
- `.venv/bin/python -m py_compile review_workbench.py agents/tenant_registry.py`
- `node --check web/review_workbench/api.js`
- `PYTHONPATH=. .venv/bin/python tests/test_ontology_eval.py`
- `git diff --check`

Smoke checks on `http://127.0.0.1:8773`, then service restarted on `http://127.0.0.1:8772`:

- Ran creditcardfraud playbook and approved one Autopilot candidate through the human review endpoint.
- Approved formal finding appeared in `GET /api/reasoning/findings?tenant=creditcardfraud&context=active`.
- Registry entry carried `prior_finding / reviewed_inference` labels.
- `POST /actions` returned a Workspace next action with `canonical_write=false`.
- `POST /change-proposals` returned `proposal_draft` with `writes_canonical=false`.
- Marked one approved finding `stale`; active context excluded it, while `status=stale` audit query still returned it.
- Created and approved a default tenant finding, then created a later scoped question; new task scope included one `prior_finding` labeled `reviewed_inference`.
- Compared creditcardfraud artifact fingerprint before and after candidate approval: unchanged, 7 artifacts.
- Direct Autopilot candidate `status=promoted` remains blocked with HTTP 400.
- creditcardfraud Registry/API smoke payload did not contain raw `cardCVV` or `enteredCVV`.

## Demo URL

- Reasoning Autopilot: <http://127.0.0.1:8772/?screen=reasoning&tenant=creditcardfraud>
- Active approved findings API: <http://127.0.0.1:8772/api/reasoning/findings?tenant=creditcardfraud&context=active>
