# Demo Readiness Validation - Task #65

## Result

PASS. Demo readiness makes CLI agent prerequisites visible and gates safe demo execution without opening real external CLI execution.

## Environment

- Server: `http://127.0.0.1:8767`
- Settings URL: <http://127.0.0.1:8767/settings.html?tenant=default>
- Validation time: `2026-05-12T11:42:30.043500+00:00`

## Coverage

1. **Generic baseline passed**
   - `generic_cli_builtin` readiness returned `demo_ready` and `safe_demo_enabled=true`.
   - All Generic readiness checks passed: runtime, binary, path, auth, template, output contract, policy, working dir, smoke task.
   - Generic safe demo completed with no policy violations and touched only `reports/agent-gateway-smoke.md`.

2. **Placeholder CLI prerequisites passed**
   - Claude Code / Codex / Gemini / OpenClaw / Hermes were not `demo_ready` and all had `safe_demo_enabled=false`.
   - Each profile returned a clear `demo_status`, failed/unknown checklist items, and `next_action` guidance.
   - OpenClaw/Hermes showed not-installed style gating on this machine; Claude/Codex/Gemini were gated by missing executable demo template/output contract even when binary/path checks passed.

3. **Backend safe-demo gating passed**
   - Direct `POST /api/agent-gateway/safe-demo` for every non-`demo_ready` placeholder returned HTTP 400 with `Safe demo disabled: ...`.
   - These blocked direct calls did not create AgentRun records.
   - UI also disables `Run safe demo` based on `readiness.safe_demo_enabled`, but backend enforcement is the real gate.

4. **Readiness read-only passed**
   - Calling readiness APIs did not increase default AgentRun count: `20 -> 20`.
   - Sandbox readiness also did not create AgentRuns.
   - Readiness did not create output artifacts and did not change canonical ontology/graph/review state.

5. **Secret masking and tenant isolation passed**
   - Readiness responses, next actions, and safe demo run output had no secret-like leaks (`sk-`, `api_key=`, `token=`, `password=`, `secret=`).
   - Sandbox readiness and safe demo were tenant-scoped; sandbox AgentRun keys did not overlap with default AgentRun keys.

6. **Canonical safety passed**
   - Canonical artifact `link:employee:1:n:order` stayed `approved` version `6`.
   - Default graph stayed approved with `157` nodes / `156` edges.
   - Safe demo remains draft/report-only and does not commit, push, approve, ingest, or modify canonical state.

## Verification Commands

```bash
node --check web/review_workbench/app.js && node --check web/review_workbench/instance_app.js && node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/settings_app.js && git diff --check
.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Detailed JSON evidence: `reports/demo-readiness-task65-saskue.json`.
