# Task 121 - Employee Profile Reasoning Implementation

Time: 2026-05-13 17:38 CST
Base commit: `c0221b9` (`Unify reasoning question evidence workflow`)
Status: implementation complete, awaiting task #122 validation

## Scope

Task #121 upgrades Employee scoped reasoning from a count summary to a deterministic employee profile answer. This is separate from task #117/#118 Chinese terminology work, which is already product-accepted.

The implementation does not use free-form LLM summarization. It builds structured profile facts from approved graph scope plus controlled Northwind SQL aggregations, then renders those fields in the Current Answer panel.

## Backend Changes

Changed file: `review_workbench.py`

- Added deterministic Employee profile builder for scoped Employee tasks.
- For Employee scoped reasoning, the finding now carries structured answer payload:
  - `profile_summary`
  - `key_facts`
  - `business_interpretation`
  - `evidence_limits`
  - `next_questions`
  - `metrics`
- Added `controlled_aggregate` evidence path with traceable `source_ref`.
- Exposed the structured fields in the finding API response while preserving the existing `recommended_action` payload.
- Kept safety boundaries:
  - read-only controlled aggregations
  - no canonical writes
  - finding remains draft until review
  - no claims based on unavailable fields such as profitability, work hours, targets, or customer satisfaction

## Frontend Changes

Changed file: `web/review_workbench/reasoning_app.js`

- Current Answer now detects `structured_answer` and renders:
  - `画像判断`
  - `关键事实`
  - `业务含义`
  - `证据边界`
  - `下一步验证`
- The first screen is no longer a single count-summary paragraph when structured profile data exists.
- Key facts include `source_ref` for audit traceability.

Changed file: `web/review_workbench/styles.css`

- Added layout styles for structured profile sections, fact lists, and bullet groups.

## Employee:5 Result

API checked:

`/api/reasoning/tasks/reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216?tenant=default`

Result summary:

- Title: `Steven Buchanan 员工画像：低订单负载、客户覆盖较分散`
- Structured payload present: yes
- `key_facts`: 7
- `business_interpretation`: 3
- `evidence_limits`: 3
- `next_questions`: 4
- Supporting evidence kinds: `question_scope`, `controlled_aggregate`

Profile summary:

`Steven Buchanan 是 Sales Manager，位于 London, UK。在当前已批准 Northwind 图谱和受控聚合中，他呈现为低订单负载、客户覆盖较分散的员工：共处理 42 单，占全体订单 5.1%，订单量排名 9/9；覆盖 29 个客户，最大客户为 Save-a-lot Markets（3 单，占该员工订单 7.1%）。因此当前证据不支持把他判断为订单负载异常偏高。`

This is intentionally not a risk or performance judgment. The answer states what can be inferred and what cannot be inferred from the current evidence.

## Browser Smoke

Opened:

`http://127.0.0.1:8767/reasoning.html?tenant=default&task=reasoning%3Agraph-scope%3Adefault-question-center-question-scope-employee-5-d1-n200-e200-q0957854216`

Headless Chrome DOM check found all required visible sections:

- `画像判断`
- `关键事实`
- `业务含义`
- `证据边界`
- `下一步验证`
- `低订单负载`
- `客户覆盖较分散`
- `订单量排名 9/9`
- `Save-a-lot Markets`
- `缺少绩效目标`
- `不会写入正式知识图谱`

The old count-summary phrasing was not present:

- `has 42 approved order relationships`: absent
- `loaded in the current evidence scope`: absent

## Validation Commands

```bash
node --check web/review_workbench/reasoning_app.js && node --check web/review_workbench/questions_app.js && node --check web/review_workbench/evidence_app.js && node --check web/review_workbench/findings_app.js && node --check web/review_workbench/shell_app.js && node --check web/review_workbench/graph_app.js && git diff --check
/Users/slc/code/Aletheia/.venv/bin/python -m compileall -q review_workbench.py agents query_artifacts.py evals tests
/Users/slc/code/Aletheia/.venv/bin/python -m unittest tests/test_ontology_eval.py
```

Result: all passed.

## Service

Service restarted via detached `screen` session and is listening on `127.0.0.1:8767`.

## Handoff

Task #121 is ready for task #122 validation. Do not commit or push until task #122 passes and product acceptance is granted.
