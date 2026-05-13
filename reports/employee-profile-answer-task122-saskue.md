# Employee Profile Answer Validation - Saskue

Result: PASS

Validated Employee:5 original reasoning URL after task #121.

API checks:
- Latest title: Steven Buchanan 员工画像：低订单负载、客户覆盖较分散
- `structured_answer` contains non-empty `profile_summary`, `key_facts`, `business_interpretation`, `evidence_limits`, `next_questions`, and `metrics`.
- Supporting evidence includes `controlled_aggregate` with `source_ref=employees + orders + order_details + customers`.
- Latest conclusion no longer uses the old English count-summary phrases as the main answer.

Browser checks:
- Current Answer first screen shows `画像判断 / 关键事实 / 业务含义 / 证据边界 / 下一步验证`.
- Visible judgment includes low order load, dispersed customer coverage, rank 9/9, Save-a-lot Markets top customer, and missing evidence limits.
- Current answer panel does not expose `has 42 approved order relationships` or `loaded in the current evidence scope` as the main conclusion.

Safety checks:
- Claims are framed as draft/review output and preserve the canonical write boundary.
- No unsupported performance, customer satisfaction, or profit-margin claims were found.
- Sandbox task API did not leak default Employee:5 profile content.

Artifacts:
- JSON report: `/Users/slc/code/Aletheia/reports/employee-profile-answer-task122-saskue.json`
- Screenshot: `/tmp/task122-reasoning-profile.png`

Verification commands passed:
- `python3 /tmp/task122_validate.py`
- `node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/evidence_app.js && node --check web/review_workbench/findings_app.js && node --check web/review_workbench/shell_app.js && node --check web/review_workbench/graph_app.js && git diff --check`
- `/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests`
- `/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py`

Safety baseline:
- Default graph: approved=true, 157 nodes, 156 edges, order checksum `ff3557ce250ade95cd7a127009f7e293c68890b13109fe3a3e213fa8d8447c91`.
- Sandbox graph: approved=false, 0 nodes, 0 edges.
- Canonical link `link:employee:1:n:order`: approved v6.
