# Workspace Work Queue Copy - Task 209

## Scope

Clarified Workspace navigation copy so users can distinguish human-attention work items from automatic agent runs.

## Changes

- Renamed `Case Inbox` tab to `Work Queue`.
- Kept `Agent Runs` as a separate tab for automatic crawl / graph enrichment / reasoning runs.
- Used status labels:
  - `Active`
  - `Blocked`
  - `Done`
  - `All`
- Added explanatory copy:
  - `Cases are business questions, findings, or review follow-ups that need human attention.`
- Renamed surrounding labels:
  - `CASE` -> `WORK ITEM`
  - `Case routing` -> `Work routing`
  - `Inbox summary` -> `Queue summary`
  - `Selected Case` -> `Selected work item`

## Validation

- `node --check web/review_workbench/api.js`
- `npx esbuild web/review_workbench/workbench.jsx --bundle --outfile=/tmp/workbench-task209.js --format=iife --global-name=WorkbenchTask209 --log-level=warning`
- `npx esbuild web/review_workbench/screens.jsx --bundle --outfile=/tmp/screens-task209.js --format=iife --global-name=ScreensTask209 --log-level=warning`
- `.venv/bin/python -m py_compile review_workbench.py`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
- `git diff --check`
- Chrome DOM smoke at `http://127.0.0.1:8772/?screen=workbench&tenant=maritime-risk&workspace_tab=cases`
