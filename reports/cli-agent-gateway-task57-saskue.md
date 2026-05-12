# CLI Agent Gateway Validation - Task #57

## Result

PASS. The CLI Agent Gateway behaves as a controlled agent worker, not a shell backdoor.

## Environment

- Server: `http://127.0.0.1:8767`
- Settings URL: <http://127.0.0.1:8767/settings.html?tenant=default>
- Validation time: `2026-05-12T10:59:22.363362+00:00`

## Coverage

1. **Settings / secret masking passed**
   - Settings returned 6 runtime profiles: Generic CLI plus Codex/Gemini/Claude Code/OpenClaw/Hermes placeholders.
   - Default policy has `secret_policy=deny` and `env_allowlist=[]`.
   - Health check for `generic_cli_builtin` returned `available`, `secret_masked=true`, and masked binary display.
   - Secret-like stdout from malicious output was stored as `[masked]`.

2. **Execution-layer blocking passed**
   - UI exposes runtime/profile/template controls only; no free shell field was present.
   - `codex_cli_default` execution returned `blocked` with `runtime_probe_only`; placeholder CLI profiles are health-check only in MVP.
   - `generic_cli_builtin` smoke run completed through allowlisted `builtin_json_report_v1` and touched only `reports/agent-gateway-smoke.md`.

3. **Output-adoption blocking passed**
   - Non-JSON output failed with `non_json_output`.
   - Missing required fields failed with `missing_required_fields`.
   - Malicious output containing approve/ingest/commit/push/secret_read plus `/tmp/outside.txt` was blocked with `blocked_tool_call`, `blocked_action_in_output`, `path_not_allowed`, and `non_draft_output`.
   - A self-reported clean output with `policy_violations=[]` but `approve_finding` in `tool_calls` was blocked by local policy validator.

4. **Path / tenant isolation passed**
   - Allowed path `reports/inside.md` completed as draft report output.
   - `/tmp/outside.txt` and `../escape.txt` were blocked as path violations.
   - Sandbox run carrying `tenant_id=default` in draft artifact payload was blocked with `tenant_mismatch`.
   - Sandbox settings returned only sandbox-scoped AgentRuns and did not list default run keys.

5. **Draft-only and canonical safety passed**
   - Non-draft `status=approved` output artifact was blocked.
   - Negative runs were `failed` or `blocked` and recorded policy violations.
   - Canonical ontology/graph snapshot stayed unchanged before and after validation: artifact `link:employee:1:n:order` remained `approved` version `6`, graph stayed approved with `157` nodes / `156` edges.

## Verification Commands

```bash
node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Detailed JSON evidence: `reports/cli-agent-gateway-task57-saskue.json`.
