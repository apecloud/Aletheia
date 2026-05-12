# CLI Agent Gateway Implementation Report

## Scope

- Task #53: `AgentRuntimeConfig`, `AgentPolicy`, `AgentRun`, and `AgentOutputArtifact` schema.
- Task #54: `generic_cli` adapter with allowlisted command template, structured JSON output, and local policy validator.
- Task #55: CLI runtime profiles for Generic CLI, Codex CLI, Gemini CLI, Claude Code, OpenClaw, and Hermes with health-check degradation.
- Task #56: AI Runtime Settings UI for CLI runtime list, health check, smoke run, policy display, and AgentRun audit.

## Product Boundary

This implementation treats external CLI agents as controlled workers, not shell access:

- No arbitrary shell command input.
- No automatic commit, push, deploy, approve, ingest, or canonical graph / ontology writes.
- No secret passthrough in MVP; policy default is `secret_policy=deny`.
- External CLI self-reported `policy_violations=[]` is not trusted.
- CLI output must pass local policy validation before draft output artifacts are recorded.

## Runtime Profiles

Seeded runtime profiles:

- `generic_cli_builtin`: available MVP smoke runtime, implemented with a fixed Python JSON-report template.
- `codex_cli_default`: health-check/profile placeholder.
- `gemini_cli_default`: health-check/profile placeholder.
- `claude_code_cli_default`: health-check/profile placeholder.
- `openclaw_cli_default`: health-check/profile placeholder.
- `hermes_cli_default`: health-check/profile placeholder.

Only `generic_cli_builtin` executes in MVP. Other runtimes are version-probe only until explicitly implemented.

## API

- `GET /api/agent-gateway/settings?tenant=<tenant>`
- `POST /api/agent-gateway/runtimes/<runtime_id>/health?tenant=<tenant>`
- `POST /api/agent-gateway/runs?tenant=<tenant>`

`POST /api/agent-gateway/runs` accepts `runtime_id`, `policy_id`, `task_type`, and `prompt`. A test-only `mock_cli_output` field allows validation of malicious or malformed CLI outputs through the same local validator.

## Policy Validation

The local validator checks:

- Structured JSON output and required fields.
- Blocked tool calls such as `approve_finding`, `ingest_graph`, `modify_canonical_artifact`, `commit`, `push`, `deploy`, and `secret_read`.
- Blocked action text inside the output body.
- `files_touched` path scope against `allowed_paths`.
- Draft-only output artifacts.
- Tenant mismatch in output payloads.

Blocked or failed output records an `AgentRun` with violations and does not create accepted output artifacts.

## UI

Added `settings.html` and `settings_app.js`:

- Shared Portal Settings nav.
- Runtime list.
- Health check button.
- Smoke run button.
- Policy viewer.
- Recent AgentRun audit list with violations and touched files.

## Smoke Evidence

Validated on local server `127.0.0.1:8767`:

- Settings page renders `AI Runtime Settings`, `nav-settings`, and smoke controls.
- `generic_cli_builtin` health check returns `available` and masks secret handling.
- `generic_cli_builtin` smoke run returns `completed`, a draft report artifact, no violations, and allowed path `reports/agent-gateway-smoke.md`.
- Non-JSON mock output returns `failed` with `non_json_output`.
- Malicious mock output containing `approve_finding`, `commit`, `push`, and `/tmp/outside.txt` returns `blocked` with `blocked_tool_call`, `blocked_action_in_output`, and `path_not_allowed`.
- Probe-only runtimes such as `codex_cli_default` return `blocked` with top-level `AgentRun.policy_violations=[runtime_probe_only]`; the violation is not hidden inside stdout.

## Validation Commands

```bash
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/reasoning_app.js
node --check web/review_workbench/settings_app.js
.venv/bin/python -m py_compile review_workbench.py agents/ontology_artifacts.py
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
