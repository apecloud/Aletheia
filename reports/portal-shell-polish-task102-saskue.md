# Portal Shell Polish Task #102 Validation

Status: PASS
Validator: Saskue
Date: 2026-05-13
Target: `http://127.0.0.1:8767/?tenant=default`

## Scope

Validated #99, #100, and #101 after the #103/#104 blocker fixes:

- #99: left sidebar shell, collapse/expand, mobile drawer, cross-page persistence.
- #100: default light theme, dark theme toggle, semantic status colors, no light-theme black surface regressions.
- #101: Chinese/English shell and core UI text switching, with raw finding/evidence/source content left untranslated.

The validation used a real Chrome browser via Playwright, starting from cleared Portal localStorage for the default-theme assertions.

## Blocker Regression

### Light Theme Surfaces

PASS. With empty localStorage, the following pages loaded in `data-theme=light` and did not expose large dark page backgrounds, dark-only panels, or black shell/canvas surfaces:

- Workbench: `/?tenant=default`
- Questions: `/questions.html?tenant=default`
- Findings: `/findings.html?tenant=default`
- Evidence: `/evidence.html?tenant=default`
- Explore / Graph: `/graph.html?tenant=default&type=Employee&id=4&depth=1&limit=200`
- Quality: `/quality.html?tenant=default`
- Ontology: `/ontology.html?tenant=default`

After switching dark -> light, the same light-surface audit passed for Findings, Evidence, Explore, Quality, and Ontology. Transparent surfaces were treated as inherited from the light body background, not as black backgrounds.

### Findings vs Evidence IA

PASS. Findings and Evidence are now distinct routes and distinct information structures:

- Findings URL: `/findings.html?tenant=default...`
- Evidence URL: `/evidence.html?tenant=default...`
- Findings title: `Reasoning Findings`
- Evidence title: `Evidence Browser`
- Findings active nav: `nav-findings:/findings.html?tenant=default`
- Evidence active nav: `nav-evidence:/evidence.html?tenant=default`
- Findings DOM signature: `sidebar findings-sidebar#|artifact-list#finding-list|finding-detail-workspace#|...`
- Evidence DOM signature: `evidence-browser#|filters#|evidence-index#evidence-list|evidence-detail-workspace#|...`

Findings is conclusion-centered: claim/finding list, confidence, draft status, explanation detail, supporting/counter evidence, graph path, ontology/rule basis, and follow-up questions.

Evidence is evidence-chain centered: evidence item list, kind, source ref, supporting/counter role, source path, rule/ontology basis, linked finding, and return links.

## Shell / Theme / i18n Matrix

PASS.

- Default entry is `light` theme with left fixed sidebar.
- Sidebar collapse persists across page navigation and refresh.
- Theme toggle persists as `light` / `dark` across Workbench, Questions, Findings, Quality, and Graph.
- Language toggle persists as Chinese across Workbench, Questions, Findings, Quality, and Graph.
- The explicit cross-page assertion used UI state, not only localStorage: Workbench was set to Chinese + light + collapsed sidebar, then Questions / Findings / Quality / Graph were opened and refreshed; each page still showed Chinese nav, light theme, and collapsed shell.
- Mobile drawer opens on narrow viewport, navigating to Questions closes the drawer, and the question input remains visible/clickable.
- Chinese UI text covered navigation, page/module titles, buttons, status labels, empty states, and core placeholders.
- Raw backend content remained untranslated: evidence/source references such as `orders.employeeID` and `employeeID=...` stayed intact.

Semantic status tokens were present and distinct enough for the target states:

- approved: `#1b9365`
- draft: `#2563c9`
- blocked: `#c83f4d`
- warning: `#b57909`
- info: `#0887a7`

## Safety Regression

PASS.

- `/api/portal/overview?tenant=default`: 157 entities, 156 relations, `approved_only=true`, `system_state=ready`.
- Default graph context: `approved=true`, 157 nodes, 156 edges.
- Default order checksum: `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph context: `approved=false`, 0 nodes, 0 edges, missing `object:order` and `link:employee:1:n:order`; no default fallback.
- Canonical artifact `link:employee:1:n:order`: `approved`, version 6.
- Graph -> Reasoning handoff still opens scoped node reasoning for `Employee:4` with `graph_node` evidence.
- Question Center still creates approved-only, draft-only scoped tasks with `source=question_center` and `question_scope` evidence.

## Verification Commands

```bash
python3 /tmp/task102_validate.py

node --check web/review_workbench/shell_app.js &&
node --check web/review_workbench/portal_app.js &&
node --check web/review_workbench/questions_app.js &&
node --check web/review_workbench/findings_app.js &&
node --check web/review_workbench/evidence_app.js &&
node --check web/review_workbench/quality_app.js &&
node --check web/review_workbench/graph_app.js &&
node --check web/review_workbench/reasoning_app.js &&
node --check web/review_workbench/instance_app.js &&
node --check web/review_workbench/settings_app.js &&
node --check web/review_workbench/ontology_app.js &&
git diff --check

/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

All commands passed.

## Artifacts

- JSON evidence: `reports/portal-shell-polish-task102-saskue.json`
- Desktop screenshot: `/tmp/aletheia-task102-desktop.png`
- Mobile screenshot: `/tmp/aletheia-task102-mobile.png`

## Conclusion

Task #102 PASS. The #103 light-theme black-background blocker and #104 Findings/Evidence IA blocker are both resolved in the current 8767 build. The original #102 sidebar/theme/i18n matrix and safety regression also pass.
