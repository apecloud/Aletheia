# Portal Shell Polish Implementation Report

Tasks: #99, #100, #101

Baseline: `55694a7` (`Redesign portal as reasoning workbench`)

## #99 Portal Shell Layout

- Replaced the top-tab navigation presentation with a left-side Portal sidebar.
- Sidebar supports:
  - expanded mode with full labels
  - collapsed mode with compact icons
  - persisted collapsed state via `localStorage`
  - mobile drawer behavior under narrow viewports
  - automatic drawer close after navigation
- Existing navigation ids and links remain intact so page scripts can continue updating tenant-preserving URLs.
- No backend reasoning, graph, artifact, or canonical data paths were changed.
- Added a distinct Evidence route at `/evidence.html`.
  - Findings remains the conclusion / claim center.
  - Evidence is now the evidence chain browser, with its own active nav state, title, DOM structure, and evidence-centered content.

## #100 Portal Theme System

- Default visual style is now light / white-background.
- Dark mode remains available through a shell theme toggle.
- Theme choice is persisted via `localStorage` and inherited across pages.
- Colors were organized as theme variables with semantic status colors for:
  - approved / pass
  - draft / review
  - blocked / rejected
  - warning / low confidence
  - info / links
- The implementation keeps evidence and code blocks readable in both themes.
- Follow-up blocker fix: Findings, Evidence, Explore, Quality, and Ontology now use theme variables for large page regions, panels, graph canvas, sidebars, and workspace headers. Default light mode should not show dark-only page backgrounds or large black panels.
- Semantic status tokens are exposed at the root/body theme level for validation:
  - `--status-approved`
  - `--status-draft`
  - `--status-blocked`
  - `--status-warning`
  - `--status-info`

## #101 Portal i18n MVP

- Added shared `shell_app.js` i18n layer.
- Language toggle supports English and Chinese.
- Language choice is persisted via `localStorage` and inherited across pages.
- Chinese terms follow the product glossary:
  - Workbench: 工作台
  - Questions: 问题中心
  - Findings: 推理结果
  - Evidence: 证据链
  - Explore: 探索
  - Quality: 质量与异常
  - Ontology: 本体
  - Runtime: 运行环境
  - Audit: 审计
- MVP coverage includes:
  - primary navigation
  - page titles / shell titles
  - module titles
  - buttons
  - common status text
  - common empty states
  - key placeholders
- Evidence page i18n coverage includes evidence chain labels, kind filter, source/path panels, linked explanation actions, and empty states.
- Boundary: backend-generated finding/evidence/source content is not translated, preserving audit fidelity.

## Blocker Follow-up

Two #102 blockers were fixed after initial handoff:

- Default light theme still showed dark-only regions on Findings / Explore / Evidence / Quality / Ontology.
  - Root light tokens now drive these page surfaces.
  - Graph canvas and graph node fill colors have light/dark token variants.
  - Status color tokens are defined for both light and dark themes.
- Findings and Evidence were both routed to `/findings.html`.
  - Evidence now has `/evidence.html`.
  - All shell navigation entries point Evidence to `/evidence.html`.
  - Findings and Evidence no longer share active nav, title, or main DOM signature.

## Validation

Static checks:

```bash
node --check web/review_workbench/shell_app.js
node --check web/review_workbench/portal_app.js
node --check web/review_workbench/questions_app.js
node --check web/review_workbench/findings_app.js
node --check web/review_workbench/evidence_app.js
node --check web/review_workbench/quality_app.js
node --check web/review_workbench/graph_app.js
node --check web/review_workbench/reasoning_app.js
node --check web/review_workbench/instance_app.js
node --check web/review_workbench/settings_app.js
node --check web/review_workbench/ontology_app.js
git diff --check
```

Python regression:

```bash
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Browser smoke:

- Desktop Workbench renders left sidebar.
- Default theme is light.
- Findings and Evidence have distinct URLs and page signatures.
- Default light theme has no large dark-only backgrounds on Workbench, Questions, Findings, Evidence, Explore/Graph, Quality, or Ontology.
- Dark -> light theme round trip restores light page backgrounds.
- Sidebar collapse/expand persists.
- Dark theme toggle works.
- Chinese toggle changes navigation and key page text.
- Language and theme persist across Workbench, Questions, and Quality pages.
- Mobile drawer opens, navigates, and closes without covering the destination page.

Safety regression:

- Default graph remains `approved=true`, 157 nodes, 156 edges.
- Sandbox graph remains `approved=false`, 0 nodes, 0 edges, no fallback.
- Canonical `link:employee:1:n:order` remains `approved` version 6.

## Ready for #102

The implementation is ready for Saskue's browser validation task #102.
