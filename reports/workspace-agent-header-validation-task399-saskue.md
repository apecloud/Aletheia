# Workspace Agent Header Validation - task #399

Result: PASS

Validated task #398 in a real Chrome browser against:

`http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=enrichment`

## Checks

- Workspace Agent header shows readable metadata:
  - Chinese: `名称 / ID / 启动时间 / 结束时间`
  - English: `Name / ID / Started / Finished`
  - Autopilot tab: `Autopilot reasoning agent` plus the same metadata fields
- `Agent settings / Agent 设置` defaults to collapsed.
- Collapsed state shows summary only, including scope, budget, cadence, safety, and next run.
- Expanded state exposes the existing settings controls:
  - scope
  - budget
  - allowlist/safety
  - cadence
  - custom minutes
  - stop condition
- Control buttons remain visible and wired:
  - `Run once / 运行一次`
  - `Save settings / 保存设置`
  - `Pause/Resume / 暂停/恢复`
  - `Open results / 打开结果`
  - `Full run log / 完整运行日志`
  - `Open reasoning / 打开推理`

## Save Settings Smoke

Browser expanded settings, changed:

- scope: `task399 smoke scope should not break save`
- budget: `7`
- cadence: `custom`
- custom minutes: `61`
- allowlist: `zenodo.org`

Then clicked `保存设置`.

API confirmed existing settings API still works:

```json
{
  "cadence": "custom",
  "custom_interval_minutes": 61,
  "max_frontier": 7,
  "allowed_domains": ["zenodo.org"]
}
```

The session config was restored immediately after the smoke check.

## Evidence

- Browser screenshot: `reports/workspace-agent-header-task399-browser.png`
- Machine-readable report: `reports/workspace-agent-header-validation-task399-saskue.json`

## Verification Commands

- `node --check web/app/api.js`
- `npx --yes esbuild web/app/workbench.jsx --bundle --format=iife --global-name=AletheiaWorkbench --outfile=/tmp/task399-workbench.js --loader:.jsx=jsx`
- `.venv/bin/python -m py_compile server/workbench_server.py`
- `.venv/bin/python -m unittest tests/test_continuous_enrichment_frontier.py tests/test_iterative_graph_enrichment.py`
- `git diff --check -- web/app/workbench.jsx reports/workspace-agent-header-validation-task399-saskue.json reports/workspace-agent-header-task399-browser.png`

## Boundary

No scheduler, enrichment, reasoning, canonical ontology, formal graph, or review gate behavior was changed by this validation.
