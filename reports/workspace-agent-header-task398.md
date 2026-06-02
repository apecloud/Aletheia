# Workspace Agent Header & Collapsed Settings - task #398

## Scope

Updated the Workspace Agent page only. No scheduler, enrichment, reasoning, review gate, or API behavior was changed.

## Changes

- Added a compact Agent header metadata row above the existing summary:
  - Name
  - ID
  - Started
  - Finished
- Metadata source:
  - Enrichment: session key plus selected/latest run timestamps, falling back to session created/updated timestamps.
  - Autopilot: selected/latest run key and timestamps.
- Changed `Agent settings` to default collapsed.
- Collapsed settings still show an at-a-glance summary: scope, budget, cadence, safety boundary, and next run.
- Added `Expand / Collapse` control. Existing `Run once`, `Save settings`, `Pause/Resume`, `Open results`, `Full run log`, and `Open reasoning` controls are unchanged.

## Validation

Passed:

```bash
node --check web/app/api.js
npx --yes esbuild web/app/workbench.jsx --bundle --format=iife --global-name=AletheiaWorkbench --outfile=/tmp/task398-workbench.js
git diff --check -- web/app/workbench.jsx
curl -sS -o /tmp/task398-workbench.html -w '%{http_code}\n' 'http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=agents&agent_tab=enrichment&lang=zh'
curl -sS 'http://127.0.0.1:8772/workbench.jsx' | rg -n 'Settings collapsed|设置已折叠|Started|启动时间|Finished|结束时间|agentDisplayName|setSettingsOpen'
```

The 8772 served source includes the new header fields and collapsed settings state. Browser-level interaction validation is delegated to task #399.
