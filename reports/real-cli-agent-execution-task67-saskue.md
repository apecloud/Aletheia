# Real CLI Agent Execution Validation - Task #67

## Result

PASS. Actual readiness is runtime-specific, only truly ready runtimes can execute controlled safe demos, and the #57/#65 safety boundaries still hold.

## Environment

- Server: `http://127.0.0.1:8767`
- Settings URL: <http://127.0.0.1:8767/settings.html?tenant=default>
- Validation time: `2026-05-12T12:45:49.408563+00:00`

## Runtime Results

1. **Demo-ready runtimes passed**
   - `generic_cli_builtin`: `demo_ready`, safe demo `completed`
   - `claude_code_cli_default`: `demo_ready`, safe demo `completed`
   - `codex_cli_default`: `demo_ready`, safe demo `completed`
   - `gemini_cli_default`: `demo_ready`, safe demo `completed`

Each demo-ready runtime had passing `binary`, `path_visible`, `auth`, `template`, `output_contract`, `policy`, `working_dir`, and `smoke_task` checks. Safe demo completed with 0 policy violations and only touched `reports/agent-gateway-smoke.md`.

2. **Disabled runtimes stayed gated**
   - `openclaw_cli_default`: `not_installed`, direct safe demo HTTP 400
   - `hermes_cli_default`: `not_installed`, direct safe demo HTTP 400

OpenClaw and Hermes remained `not_installed`, exposed next actions through readiness checks, and backend direct safe-demo calls returned HTTP 400 without creating accepted output.

3. **Fixed-template execution boundary passed**
   - Safe demo for Claude Code / Codex / Gemini / Generic ignored malicious `mock_cli_output` and ran the fixed allowlisted template instead.
   - Safe demo outputs were report-only (`write_report`) and draft/report scoped.
   - The lower-level `/api/agent-gateway/runs` endpoint still blocked malicious mock output with `blocked_tool_call`, `blocked_action_in_output`, `path_not_allowed`, and `non_draft_output`, proving the local policy validator remains active.

4. **Secret / tenant / canonical safety passed**
   - Readiness and safe-demo responses had no secret-like leaks (`sk-`, `api_key=`, `token=`, `password=`, `secret=`).
   - Sandbox safe demo produced a `northwind-sandbox` AgentRun, and sandbox/default run keys did not overlap.
   - Canonical artifact `link:employee:1:n:order` stayed `approved` version `6`.
   - Default graph stayed approved with `157` nodes / `156` edges.

## Verification Commands

```bash
node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Detailed JSON evidence: `reports/real-cli-agent-execution-task67-saskue.json`.
