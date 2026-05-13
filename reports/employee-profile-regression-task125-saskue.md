# Employee Profile Regression Validation - task #125

Result: PASS

Validated the real user screenshot regression path and additional Employee:5 entry points after task #124.

Covered paths:
- Historical user screenshot task `q815ceadee5`: API and browser Current Answer.
- Left history-card click into the same historical Employee:5 task.
- Fresh Questions Employee:5 task, generated and run during validation.
- Graph handoff Employee:5 task, generated and run during validation.

PASS evidence:
- Current Answer first screen shows `画像判断 / 关键事实 / 业务含义 / 证据边界 / 下一步验证`.
- Visible Employee:5 profile includes `低订单负载 / 客户覆盖较分散 / 订单量排名 9/9`.
- API latest findings include non-empty `structured_answer` and `controlled_aggregate` evidence.
- Forbidden old main-answer terms are absent from API title/conclusion and browser DOM: `work snapshot`, `approved order relationships`, `loaded in the current evidence scope`.

Safety regression:
- Default graph remains approved=true, 157 nodes, 156 edges, checksum `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph remains approved=false, 0 nodes, 0 edges.
- Canonical link `link:employee:1:n:order` remains approved v6.

Screenshots:
- `/tmp/task125-history-q815.png`
- `/tmp/task125-history-card-click.png`
- `/tmp/task125-questions-new.png`
- `/tmp/task125-graph-handoff.png`

JSON report: `/Users/slc/code/Aletheia/reports/employee-profile-regression-task125-saskue.json`

Validation commands passed:
- `python3` regression harness generated `reports/employee-profile-regression-task125-saskue.json`.
- `node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/evidence_app.js && node --check web/review_workbench/findings_app.js && node --check web/review_workbench/shell_app.js && node --check web/review_workbench/graph_app.js && git diff --check`
- `.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests`
- `.venv/bin/python -m unittest tests/test_ontology_eval.py`
