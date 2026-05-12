# Real CLI Agent Execution Implementation Report

## Scope

- Task #66: complete actual readiness for each CLI runtime profile and enable controlled safe demo execution for runtimes that satisfy local prerequisites.

## Runtime Status

- `generic_cli_builtin`: `demo_ready`; uses `builtin_json_report_v1`.
- `claude_code_cli_default`: `demo_ready`; local `claude` binary and auth are available; uses `claude_code_json_report_v1`.
- `codex_cli_default`: `demo_ready`; local `codex` binary and auth are available; uses `codex_cli_json_report_v1`.
- `gemini_cli_default`: `demo_ready`; local `gemini` binary is available and non-interactive JSON output is supported; uses `gemini_cli_json_report_v1`.
- `openclaw_cli_default`: `not_installed`; safe demo remains disabled with next action to install/expose `openclaw`.
- `hermes_cli_default`: `not_installed`; safe demo remains disabled with next action to install/expose `hermes`.

## Execution Boundary

Real CLI execution is still constrained to fixed safe-demo templates:

- No user-provided shell command is accepted.
- No secrets are passed to CLI runtimes.
- Runtime stdout/stderr and auth/readiness details are secret-masked before storage or UI display.
- Safe demo prompts are fixed and minimal; the gateway wraps CLI output into a draft report artifact.
- Safe demo ignores `mock_cli_output`; mock output remains available only on the lower-level validation run endpoint.
- Output is still checked by the local policy validator, including blocked tools/actions, path scope, tenant scope, and draft-only artifact status.

## API / Templates

Enabled allowlisted templates:

- `claude_code_json_report_v1`: `claude --print --output-format json --permission-mode plan`.
- `codex_cli_json_report_v1`: `codex exec --sandbox read-only --output-last-message`.
- `gemini_cli_json_report_v1`: `gemini --prompt ... --approval-mode plan --output-format json`.
- `builtin_json_report_v1`: existing Generic CLI smoke template.

Probe-only templates remain for unavailable runtimes.

## Smoke Evidence

Validated locally on `127.0.0.1:8767`:

- Readiness shows `demo_ready` for Generic, Claude Code, Codex, and Gemini.
- Readiness shows `not_installed` for OpenClaw and Hermes.
- Safe demo completed with 0 policy violations for Claude Code, Codex, Gemini, and Generic.
- Direct safe-demo POST to OpenClaw / Hermes returns HTTP 400 with `Safe demo disabled: not_installed`.
- Secret scan over readiness settings output did not expose `sk-`, `api_key=`, `token=`, `password=`, or `secret=`.
- Safe demo ignores malicious `mock_cli_output` and runs the actual fixed template.

## Validation Commands

```bash
node --check web/review_workbench/app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/reasoning_app.js
node --check web/review_workbench/settings_app.js
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
git diff --check
```
